"""PracticeGraph CLI: the single writer and the brain's front door (C-4).

Exit codes follow FR-DIA-2 conventions: 0 success/healthy, 1 unhealthy,
2 command error (argparse's native behavior for usage errors).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import webbrowser
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from practicegraph import __version__
from practicegraph.agent import DEFAULT_INTERVAL_S, initialize, run_loop, run_tick
from practicegraph.analysis.aggregate import DailySnapshot, build_daily_snapshot
from practicegraph.analysis.focus import TIP_IDS, record_block_event
from practicegraph.analysis.insights import SUGGESTION_IDS, dismiss_suggestion
from practicegraph.analysis.ratecard import activate_rate_card_from, active_rate_card
from practicegraph.analysis.schedule import (
    ScheduleUpdate,
    local_day,
    read_schedule_profile,
    save_schedule_profile,
)
from practicegraph.config import (
    CONFIG_FILE_NAME,
    REFLECTION_STYLE_PROVIDERS,
    read_prefs,
    resolve,
    store_org_token,
    write_config_updates,
)
from practicegraph.consent import CONSENT_FILE_NAME, read_consent
from practicegraph.doctor import run_doctor
from practicegraph.emit import SYNTHETIC_EXAMPLE_LABEL
from practicegraph.events import EngagementCounter
from practicegraph.history import ingest, snapshot_for_day
from practicegraph.report.html import render_html
from practicegraph.report.shell import (
    gather_privacy_status,
    gather_shell_extras,
    render_shell,
)
from practicegraph.report.text import render_text
from practicegraph.sources.registry import collect_events
from practicegraph.store import Store

DAY_NAME = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_NUMBER = {value: key for key, value in DAY_NAME.items()}


def _working_days(raw: str) -> tuple[int, ...]:
    names = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    if not names or any(name not in DAY_NAME for name in names):
        raise argparse.ArgumentTypeError("use comma-separated mon..sun")
    return tuple(sorted({DAY_NAME[name] for name in names}))


def _parse_generated_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 timestamp") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a date as YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="practicegraph",
        description="Privacy-preserving AI-usage coaching. All analysis is local.",
    )
    parser.add_argument("--version", action="version", version=f"practicegraph {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="create the local data directory and state store")

    report = subparsers.add_parser("report", help="render the daily report")
    report.add_argument(
        "--date",
        type=_parse_day,
        default=None,
        help="UTC day to report on (default: today, UTC)",
    )
    report.add_argument(
        "--generated-at",
        type=_parse_generated_at,
        default=None,
        help="fix the generation timestamp (deterministic output)",
    )
    report.add_argument(
        "--html",
        type=Path,
        default=None,
        help="write the single-file HTML report to this file",
    )
    report.add_argument(
        "--shell",
        type=Path,
        default=None,
        help="write the multi-view HTML shell (incl. Privacy Center) to this file",
    )
    report.add_argument(
        "--open",
        action="store_true",
        help="render the multi-view shell and open it in the default browser",
    )

    agent = subparsers.add_parser("agent", help="run the endpoint agent scheduler")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    agent_run = agent_sub.add_parser("run", help="run the scheduler loop")
    agent_run.add_argument("--once", action="store_true", help="run exactly one tick")
    agent_run.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_S,
        help=f"poll interval in seconds (default {DEFAULT_INTERVAL_S})",
    )

    consent = subparsers.add_parser("consent", help="view or change emission consent")
    consent_sub = consent.add_subparsers(dest="consent_command", required=True)
    consent_sub.add_parser("status", help="one-line consent state")
    consent_sub.add_parser("on", help="enable anonymous aggregate sharing")
    consent_sub.add_parser("off", help="disable sharing (the default)")
    consent_sub.add_parser("show", help="display the exact sharing boundary")

    hygiene = subparsers.add_parser("hygiene", help="prune aged local state")
    hygiene.add_argument(
        "--age-days",
        type=int,
        default=90,
        help="prune local history older than this many days (default 90)",
    )

    suggest = subparsers.add_parser("suggest", help="manage advisory suggestions")
    suggest_sub = suggest.add_subparsers(dest="suggest_command", required=True)
    suggest_sub.add_parser("list", help="suggestion ids and their state")
    suggest_dismiss = suggest_sub.add_parser("dismiss", help="hide a suggestion")
    suggest_dismiss.add_argument("suggestion_id")
    suggest_never = suggest_sub.add_parser("never", help="never show a suggestion again")
    suggest_never.add_argument("suggestion_id")

    focus = subparsers.add_parser("focus", help="focus coaching controls (local only)")
    focus_sub = focus.add_subparsers(dest="focus_command", required=True)
    focus_sub.add_parser("status", help="current focus-coaching state")
    focus_sub.add_parser("on", help="enable focus coaching (the default)")
    focus_sub.add_parser("off", help="disable nudges and observation tips")
    focus_dismiss = focus_sub.add_parser("dismiss", help="dismiss a tip permanently")
    focus_dismiss.add_argument("tip_id")
    focus_record = focus_sub.add_parser(
        "record", help="record a focus-timer event (invoked by the shell)"
    )
    focus_record.add_argument("event")

    reflect = subparsers.add_parser(
        "reflect",
        help="daily wording refresh for the reflections (local only)",
    )
    reflect_sub = reflect.add_subparsers(dest="reflect_command", required=True)
    reflect_sub.add_parser("status", help="current styling provider (or off)")
    reflect_sub.add_parser("off", help="use the fixed wording (the default)")
    reflect_on = reflect_sub.add_parser(
        "on", help="restyle daily with a local agent CLI (claude or codex)"
    )
    reflect_on.add_argument(
        "provider", choices=REFLECTION_STYLE_PROVIDERS,
        help="which on-machine CLI rewrites the wording",
    )

    checkin = subparsers.add_parser(
        "checkin",
        help="rate how today felt, 1-5 (local historical note; not a score)",
    )
    checkin.add_argument("rating", type=int)

    schedule = subparsers.add_parser("schedule", help="local working schedule")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_sub.add_parser("show", help="show the local schedule or suggestion")
    schedule_set = schedule_sub.add_parser("set", help="confirm the local schedule")
    schedule_set.add_argument("--timezone", required=True)
    schedule_set.add_argument("--working-days", type=_working_days, required=True)
    schedule_set.add_argument("--work-start", required=True)
    schedule_set.add_argument("--work-end", required=True)
    schedule_set.add_argument("--quiet-start", required=True)
    schedule_set.add_argument("--quiet-end", required=True)
    schedule_set.add_argument(
        "--weekend-mode", choices=("expected", "exceptional"), required=True
    )

    ui = subparsers.add_parser(
        "ui", help="local interactive dashboard (127.0.0.1 only, token-gated)"
    )
    ui_sub = ui.add_subparsers(dest="ui_command", required=True)
    ui_serve = ui_sub.add_parser("serve", help="start (or reuse) the dashboard")
    ui_serve.add_argument(
        "--open", action="store_true", help="open it in the default browser"
    )

    config = subparsers.add_parser("config", help="view or set connection settings")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show", help="resolved settings and sources (token masked)")
    config_set = config_sub.add_parser("set", help="set connection settings")
    config_set.add_argument("--api-base-url", default=None)
    config_set.add_argument("--org-id", default=None)
    config_sub.add_parser(
        "set-token",
        help="store the org token in the protected store (read from stdin, never echoed)",
    )

    doctor = subparsers.add_parser("doctor", help="print install health as closed JSON")
    doctor.add_argument(
        "--probe-network",
        action="store_true",
        help="opt in to the network reachability probe",
    )
    return parser


def _snapshot_inputs(env: dict[str, str], day: date) -> DailySnapshot:
    events, source_health = collect_events(env)
    return build_daily_snapshot(events, source_health, day)


def _record_report_generated(env: dict[str, str], day: date) -> None:
    """FR-RPT-7: rendering increments the closed engagement counter, fail-open."""
    try:
        config = resolve(env)
        store = Store.in_data_dir(config.data_dir)
        if store.exists():
            store.engagement_add(day.isoformat(), EngagementCounter.REPORT_GENERATED.value)
    except Exception:
        pass


def _cmd_report(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    day = args.date or datetime.now(UTC).date()
    generated_at = args.generated_at or datetime.now(UTC)
    config = resolve(env)
    store = Store.in_data_dir(config.data_dir)
    extras = None
    if store.exists():
        # Rendering is a migration point, the create_server discipline (field
        # report 2026-08-21). Migrations used to run on the agent tick alone, so
        # a store the previous release left behind crashed here — `no such
        # column: git_commit_attempts` — and this is the surface the app falls
        # back to when the dashboard is unavailable, i.e. exactly where an
        # upgraded install lands when the dashboard is the thing that broke.
        store.migrate()
        activate_rate_card_from(config.data_dir)
        prefs = read_prefs(config.data_dir)
        source_health = ingest(env, store, generated_at)
        snapshot = snapshot_for_day(store, day, source_health)
        schedule = read_schedule_profile(config.data_dir)
        extras = gather_shell_extras(
            store,
            snapshot,
            prefs,
            day,
            schedule,
            local_day(generated_at, schedule),
            generated_at=generated_at,
        )
    else:
        # Stateless fallback: direct parse, no history-backed sections.
        snapshot = _snapshot_inputs(env, day)
    rate_card_version = active_rate_card().version
    sys.stdout.write(
        render_text(snapshot, generated_at, __version__, rate_card_version,
                    extras=extras)
    )
    if args.html is not None:
        html_text = render_html(
            snapshot,
            generated_at,
            __version__,
            rate_card_version,
            extras=extras,
        )
        html_path: Path = args.html
        html_path.write_text(html_text, encoding="utf-8", newline="")
        print(f"wrote {html_path}", file=sys.stderr)
    if args.shell is not None or args.open:
        privacy = gather_privacy_status(config, store if store.exists() else None)
        shell_text = render_shell(snapshot, privacy, generated_at, __version__,
                                  rate_card_version, extras=extras)
        if args.shell is not None:
            shell_path: Path = args.shell
            shell_path.write_text(shell_text, encoding="utf-8", newline="")
            print(f"wrote {shell_path}", file=sys.stderr)
        if args.open:
            target_dir = config.data_dir / "reports"
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                import tempfile

                target_dir = Path(tempfile.mkdtemp(prefix="practicegraph-"))
            open_path = target_dir / f"daily-{day.isoformat()}.html"
            open_path.write_text(shell_text, encoding="utf-8", newline="")
            # FR-RPT-6: failure to launch a browser degrades to printing the path.
            opened = False
            try:
                opened = webbrowser.open(open_path.as_uri())
            except Exception:
                opened = False
            print(f"report: {open_path}", file=sys.stderr)
            if not opened:
                print("could not launch a browser; open the file above", file=sys.stderr)
    _record_report_generated(env, day)
    return 0


def _cmd_init() -> int:
    data_dir = initialize(dict(os.environ))
    consent = read_consent(data_dir)
    print("PracticeGraph initialized.")
    print(f"data directory: {data_dir}")
    if not consent.emission_enabled:
        # FR-CNS-1: first-run states plainly that data stays local while off.
        print("Sharing is OFF (the default): nothing leaves this machine.")
        print("Enable anonymous org aggregates any time with: practicegraph consent on")
    return 0


def _cmd_agent(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    if args.agent_command == "run":
        if args.once:
            initialize(env)
            outcomes = run_tick(env)
            line = " ".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
            print(f"tick {line}")
            return 0
        return run_loop(env, interval_s=max(60, args.interval))
    return 2


def _write_consent(enabled: bool) -> int:
    env = dict(os.environ)
    config = resolve(env)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "emission_enabled": enabled,
        "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = config.data_dir / CONSENT_FILE_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if enabled:
        print("Sharing is ON: anonymous daily aggregates will be sent to your org.")
        print("See exactly what is shared with: practicegraph consent show")
    else:
        print("Sharing is OFF: nothing leaves this machine.")
    return 0


def _cmd_consent(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    config = resolve(env)
    if args.consent_command == "on":
        return _write_consent(True)
    if args.consent_command == "off":
        return _write_consent(False)
    consent = read_consent(config.data_dir)
    if args.consent_command == "status":
        state = "ON" if consent.emission_enabled else "OFF (default)"
        decided = f" (decided {consent.decided_at})" if consent.decided_at else ""
        print(f"Sharing: {state}{decided}")
        if not consent.emission_enabled:
            print("Nothing leaves this machine while sharing is off.")
        return 0
    # show (FR-CNS-2): the exact boundary — what, where, under which identity.
    store = Store.in_data_dir(config.data_dir)
    privacy = gather_privacy_status(config, store if store.exists() else None)
    print(f"Sharing: {'ON' if consent.emission_enabled else 'OFF (default)'}")
    print("What would be sent: one anonymous aggregate per day - counters, closed")
    print("categories, spend estimates, and version identifiers. Never content,")
    print("never file paths, never your identity. Schema is closed: nothing else.")
    destination = config.api_base_url or "(not configured)"
    org = config.org_id or "(not configured)"
    print(f"Where: {destination}")
    print(f"Org identity: {org}")
    if privacy.next_payload_pretty is not None:
        print("Exact next queued payload:")
        print(privacy.next_payload_pretty)
    else:
        print(f"{SYNTHETIC_EXAMPLE_LABEL}:")
        print(privacy.example_payload_pretty)
    print("Change any time: practicegraph consent on | practicegraph consent off")
    return 0


def _require_store(config_env: dict[str, str]) -> Store | None:
    store = Store.in_data_dir(resolve(config_env).data_dir)
    if not store.exists():
        print("not initialized - run: practicegraph init", file=sys.stderr)
        return None
    return store


def _cmd_hygiene(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    store = _require_store(env)
    if store is None:
        return 1
    cutoff = (datetime.now(UTC).date() - timedelta(days=max(1, args.age_days)))
    pruned = store.hygiene(cutoff.isoformat())
    for table, rows in sorted(pruned.items()):
        print(f"pruned {rows} rows: {table}")
    print(f"cutoff: {cutoff.isoformat()} (un-sent emits are never pruned)")
    return 0


def _cmd_suggest(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    store = _require_store(env)
    if store is None:
        return 1
    if args.suggest_command == "list":
        states = store.suggestion_states()
        for suggestion_id in SUGGESTION_IDS:
            state = states.get(suggestion_id, "active")
            print(f"{suggestion_id}: {state}")
        return 0
    never = args.suggest_command == "never"
    if not dismiss_suggestion(store, args.suggestion_id, never, datetime.now(UTC)):
        print("unknown suggestion id; see: practicegraph suggest list", file=sys.stderr)
        return 2
    counter = (
        EngagementCounter.RECOMMENDATION_NEVER_SUGGEST
        if never
        else EngagementCounter.RECOMMENDATION_DISMISSED
    )
    store.engagement_add(datetime.now(UTC).date().isoformat(), counter.value)
    print(f"{args.suggestion_id}: {'never suggested again' if never else 'dismissed'}")
    return 0


def _cmd_checkin(args: argparse.Namespace) -> int:
    from practicegraph.analysis.perception import record_checkin

    env = dict(os.environ)
    store = _require_store(env)
    if store is None:
        return 1
    config = resolve(env)
    schedule = read_schedule_profile(config.data_dir)
    today = local_day(datetime.now(UTC), schedule).isoformat()
    if not record_checkin(store, today, args.rating):
        print("rating must be 1 (rough day) to 5 (great day)", file=sys.stderr)
        return 2
    print(
        f"checked in: {args.rating}/5 for {today} - local historical note only."
    )
    return 0


def _cmd_focus(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    config = resolve(env)
    if args.focus_command in ("on", "off"):
        enabled = args.focus_command == "on"
        write_config_updates(config.data_dir, {"focus_coaching": enabled})
        if enabled:
            print("Focus coaching is ON: positive-first, at most two tips, one")
            print("break nudge per day. Everything stays on this machine.")
        else:
            print("Focus coaching is OFF: no nudges, no tips; reports show only")
            print("the positive metric. Nothing was or will be shared.")
        return 0
    if args.focus_command == "status":
        prefs = read_prefs(config.data_dir)
        print(f"Focus coaching: {'ON' if prefs.focus_coaching else 'OFF'}")
        print("Focus data never leaves this machine.")
        return 0
    store = _require_store(env)
    if store is None:
        return 1
    if args.focus_command == "record":
        if not record_block_event(
            store, datetime.now(UTC).date().isoformat(), args.event
        ):
            print("unknown timer event", file=sys.stderr)
            return 2
        print(f"recorded: {args.event}")
        return 0
    if args.tip_id not in TIP_IDS:
        print("unknown tip id", file=sys.stderr)
        return 2
    store.dismiss_tip(args.tip_id, datetime.now(UTC))
    store.engagement_add(
        datetime.now(UTC).date().isoformat(), EngagementCounter.TIP_ACTED.value
    )
    print(f"{args.tip_id}: dismissed everywhere, permanently")
    return 0


def _cmd_reflect(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    config = resolve(env)
    if args.reflect_command == "status":
        prefs = read_prefs(config.data_dir)
        if prefs.reflection_style in REFLECTION_STYLE_PROVIDERS:
            print(
                f"Reflection styling: ON via '{prefs.reflection_style}' "
                "(local rewording once a day; the numbers stay fixed; nothing is shared)."
            )
        else:
            print("Reflection styling: OFF (fixed wording; nothing is shared).")
        return 0
    # on/off both change the setting AND drop any cached styling, so the band
    # reflects the new mode immediately rather than lingering on stale text.
    if args.reflect_command == "on":
        write_config_updates(config.data_dir, {"reflection_style": args.provider})
        _clear_style_cache(config.data_dir)
        print(f"Reflection styling is ON via '{args.provider}'.")
        print("Once a day the local CLI rewords the reflections; a rewrite that")
        print("changes any number is rejected and the exact wording stands.")
        return 0
    write_config_updates(config.data_dir, {"reflection_style": "off"})
    _clear_style_cache(config.data_dir)
    print("Reflection styling is OFF: the band uses its fixed wording.")
    return 0


def _clear_style_cache(data_dir: Path) -> None:
    """Drop the persisted restyle so a mode change takes effect at once."""
    from practicegraph.report.restyle import STYLE_META_KEY

    store = Store.in_data_dir(data_dir)
    if store.exists():
        with contextlib.suppress(Exception):
            store.meta_set(STYLE_META_KEY, "")


def _cmd_config(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    config = resolve(env)
    if args.config_command == "show":
        token_state = (
            f"present (source: {config.org_token_source}, never displayed)"
            if config.org_token_present
            else "not configured"
        )
        print(f"data_dir: {config.data_dir} (source: {config.data_dir_source})")
        print(
            f"api_base_url: {config.api_base_url or '(not configured)'} "
            f"(source: {config.api_base_url_source})"
        )
        print(f"org_id: {config.org_id or '(not configured)'} (source: {config.org_id_source})")
        print(f"org_token: {token_state}")
        return 0
    if args.config_command == "set":
        updates: dict[str, str] = {}
        if args.api_base_url:
            updates["api_base_url"] = args.api_base_url
        if args.org_id:
            updates["org_id"] = args.org_id
        if not updates:
            print("nothing to set: pass --api-base-url and/or --org-id", file=sys.stderr)
            return 2
        write_config_updates(config.data_dir, updates)
        print(f"updated {', '.join(sorted(updates))} in {CONFIG_FILE_NAME}")
        return 0
    # set-token: read without echoing (FR-CFG-2).
    if sys.stdin.isatty():
        import getpass

        token = getpass.getpass("Org token: ")
    else:
        token = sys.stdin.readline().strip()
    if not token:
        print("no token provided", file=sys.stderr)
        return 2
    store_org_token(config.data_dir, token)
    print("org token stored in the protected store; it will never be displayed")
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    config = resolve(dict(os.environ))
    if args.schedule_command == "set":
        try:
            profile = save_schedule_profile(
                config.data_dir,
                ScheduleUpdate(
                    timezone_name=args.timezone,
                    working_days=args.working_days,
                    work_start=args.work_start,
                    work_end=args.work_end,
                    quiet_start=args.quiet_start,
                    quiet_end=args.quiet_end,
                    weekend_mode=args.weekend_mode,
                ),
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        profile = read_schedule_profile(config.data_dir)
    days = ",".join(DAY_NUMBER[day] for day in profile.working_days)
    print(f"confirmed: {'yes' if profile.confirmed else 'no (suggestion only)'}")
    print(f"version: {profile.version}")
    print(f"timezone: {profile.timezone_name}")
    print(f"working days: {days}")
    print(f"working hours: {profile.work_start}-{profile.work_end}")
    print(f"quiet hours: {profile.quiet_start}-{profile.quiet_end}")
    print(f"weekend work: {profile.weekend_mode}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    document, exit_code = run_doctor(dict(os.environ), probe_network=args.probe_network)
    sys.stdout.write(json.dumps(document, indent=2) + "\n")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "init":
        return _cmd_init()
    if args.command == "agent":
        return _cmd_agent(args)
    if args.command == "consent":
        return _cmd_consent(args)
    if args.command == "config":
        return _cmd_config(args)
    if args.command == "schedule":
        return _cmd_schedule(args)
    if args.command == "hygiene":
        return _cmd_hygiene(args)
    if args.command == "suggest":
        return _cmd_suggest(args)
    if args.command == "focus":
        return _cmd_focus(args)
    if args.command == "reflect":
        return _cmd_reflect(args)
    if args.command == "checkin":
        return _cmd_checkin(args)
    if args.command == "ui":
        from practicegraph.uiserver import serve as ui_serve_run

        return ui_serve_run(dict(os.environ), args.open)
    if args.command == "doctor":
        return _cmd_doctor(args)
    return 2  # unreachable while subcommands are required


def entry() -> None:
    raise SystemExit(main())
