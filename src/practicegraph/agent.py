"""Endpoint agent scheduler (FR-ALR-1 core).

A tick runs a fixed set of categories, each isolated so one failing evaluation
never breaks the cycle (fail-open per category, NFR-REL-1/2). Categories
report closed outcome codes only.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from practicegraph import __version__, winsec
from practicegraph.alerts import AlertContext, evaluate
from practicegraph.analysis.advisor import record_advisor_ledger
from practicegraph.analysis.advisor_receipts import gather_receipts
from practicegraph.analysis.focus import (
    block_counters,
    compute_metrics,
    ongoing_streak,
)
from practicegraph.analysis.harness_inventory import installed_versions
from practicegraph.analysis.insights import spend_pace
from practicegraph.analysis.ratecard import activate_rate_card_from, active_rate_card
from practicegraph.analysis.schedule import local_day, read_schedule_profile
from practicegraph.analysis.setup_improvements import refresh_followups
from practicegraph.catalog import (
    pull_catalog,
    pull_public_advisor,
    pull_public_build_ideas,
    pull_public_community,
    pull_public_docs,
    pull_public_model_catalog,
    pull_public_models,
    pull_public_news,
    pull_public_playbooks,
    pull_public_ratecard,
    pull_public_releases,
    pull_public_skills,
    pull_public_token_prices,
    pull_public_training,
    pull_update_manifest,
)
from practicegraph.config import (
    Config,
    Prefs,
    import_bootstrap,
    read_prefs,
    resolve,
    resolve_org_token,
)
from practicegraph.consent import read_consent
from practicegraph.emit import build_and_queue, flush_queue
from practicegraph.events import EngagementCounter
from practicegraph.history import ingest, snapshot_for_day
from practicegraph.news_notifications import evaluate_news
from practicegraph.news_notifications import reading as news_reading
from practicegraph.report.coach import (
    coaching_as_style_items,
    mark_acknowledgment,
    record_ledger_entry,
)
from practicegraph.report.restyle import (
    COACH_STYLE_META_KEY,
    STYLE_META_KEY,
    STYLE_PROVIDERS,
    build_style_cache,
    cached_styled,
    restyle_reflections,
)
from practicegraph.report.shell import (
    ShellExtras,
    gather_privacy_status,
    gather_shell_extras,
    render_shell,
)
from practicegraph.sources import ENV_SCAN_PROFILES, SourceHealthRow
from practicegraph.store import Store
from practicegraph.uiserver import ensure_ui_server_process
from practicegraph.update_notifications import evaluate_update

DEFAULT_INTERVAL_S = 900  # FR-ALR-1 default poll interval

REPORTS_DIR_NAME = "reports"
STATUS_FILE_NAME = "status.json"

# Closed tick outcome codes.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_SKIPPED_NOT_INITIALIZED = "skipped_not_initialized"
OUTCOME_SKIPPED_CONSENT_OFF = "skipped_consent_off"
OUTCOME_SKIPPED_UNCONFIGURED = "skipped_unconfigured"
OUTCOME_SKIPPED_QUIET = "skipped_nothing_due"

_TICK_CATEGORIES = (
    "catalog_pull",
    "skills_pull",
    "news_pull",
    "playbooks_pull",
    "training_pull",
    "token_prices_pull",
    "setup_followup",
    "community_pull",
    "build_ideas_pull",
    "models_pull",
    "advisor_pull",
    "docs_pull",
    "model_catalog_pull",
    "releases_pull",
    "ratecard_pull",
    "update_pull",
    "ui_server",
    "collect",
    "report_artifact",
    "restyle",
    "ledger",
    "emit_build",
    "emit_flush",
    "alerts",
    "news_alerts",
    "update_alerts",
)

# Closed restyle outcomes (a daily, best-effort, local-only styling pass).
OUTCOME_SKIPPED_STYLE_OFF = "skipped_style_off"
OUTCOME_SKIPPED_STYLE_FRESH = "skipped_fresh"
OUTCOME_SKIPPED_STYLE_EMPTY = "skipped_nothing_to_style"


def initialize(env: dict[str, str]) -> Path:
    """Create the data directory and state store (idempotent). This is the only
    entry point that creates local state from nothing. On Windows it also
    consumes any MSI-seeded provisioning bootstrap (FR-DEP-2)."""
    config = resolve(env)
    from practicegraph.secureio import private_directory, require_personal_context

    require_personal_context(config.data_dir, env)
    # Rebuild from this user's logs. A legacy machine store may contain other
    # people's history and must never be automatically adopted.
    private_directory(config.data_dir)
    (config.data_dir / REPORTS_DIR_NAME).mkdir(exist_ok=True)
    store = Store.in_data_dir(config.data_dir)
    store.migrate()
    if store.meta_get("initialized_at") is None:
        store.meta_set("initialized_at", datetime.now(UTC).isoformat(timespec="seconds"))
    # Provisioning is best-effort; doctor surfaces missing config.
    with contextlib.suppress(Exception):
        import_bootstrap(env, config.data_dir)
    return config.data_dir


def _write_report_artifact(
    config: Config,
    store: Store,
    prefs: Prefs,
    source_health: list[SourceHealthRow],
    now: datetime,
) -> tuple[bool, ShellExtras]:
    """Render today's shell report; return (first-render-of-day, extras).
    The first-render flag feeds the daily-report-ready alert; the extras are
    reused by the restyle stage so the reflections are composed only once."""
    day = now.astimezone(UTC).date()
    snapshot = snapshot_for_day(store, day, source_health)
    schedule = read_schedule_profile(config.data_dir)
    extras = gather_shell_extras(
        store, snapshot, prefs, day, schedule, local_day(now, schedule), generated_at=now
    )
    privacy = gather_privacy_status(config, store)
    html_text = render_shell(
        snapshot, privacy, now, __version__, active_rate_card().version, extras=extras
    )
    out_path = config.data_dir / REPORTS_DIR_NAME / f"daily-{day.isoformat()}.html"
    out_path.write_text(html_text, encoding="utf-8", newline="")
    store.engagement_add(day.isoformat(), EngagementCounter.REPORT_GENERATED.value)
    if extras.tips:
        store.engagement_add(day.isoformat(), EngagementCounter.TIP_SHOWN.value, len(extras.tips))
    first_today = store.meta_get("last_report_day") != day.isoformat()
    store.meta_set("last_report_day", day.isoformat())
    return first_today, extras


def _restyle_items(store: Store, prefs: Prefs, items: list[dict[str, str]], meta_key: str) -> str:
    """Daily wording refresh for one styled surface (report/restyle.py): the
    recognition band or the coaching cues, same pipeline, separate cache.

    Off unless the user named a provider; otherwise a fingerprint of today's
    items gates the spend — a same-source cache is reused and no model runs. On
    a genuine change the local CLI restyles, every rewrite is validated back
    against the facts (numbers + copy rules), and only accepted lines are
    cached. Fail-open and local-only: any failure leaves the deterministic text."""
    if winsec.is_local_system():
        # Restyle shells out to the user's own Claude/Codex CLI — a per-user
        # feature. Under LocalSystem there is no such CLI, and resolving an
        # executable by name in a SYSTEM context is a planted-binary EoP, so the
        # service never restyles regardless of the configured provider.
        return OUTCOME_SKIPPED_STYLE_OFF
    provider = STYLE_PROVIDERS.get(prefs.reflection_style)
    if provider is None:
        return OUTCOME_SKIPPED_STYLE_OFF
    if not items:
        return OUTCOME_SKIPPED_STYLE_EMPTY
    if cached_styled(items, store.meta_get(meta_key)) is not None:
        return OUTCOME_SKIPPED_STYLE_FRESH  # today's source already styled
    styled = restyle_reflections(items, provider)
    doc = build_style_cache(items, styled, datetime.now(UTC).isoformat(timespec="seconds"))
    # Cache written even when nothing was accepted, so a barren result does not
    # re-spend every tick — the fingerprint holds until the source changes.
    store.meta_set(meta_key, json.dumps(doc, sort_keys=True))
    return OUTCOME_OK


def _restyle_reflections(store: Store, prefs: Prefs, extras: ShellExtras) -> str:
    """Restyle the recognition band and the coaching cues (each cached apart).
    Both share one provider setting and one tick outcome — a fresh-daily voice
    for the two composed surfaces. Returns the combined outcome (off/empty/fresh
    only when BOTH are, else ok)."""
    reflect = _restyle_items(store, prefs, extras.reflections_raw, STYLE_META_KEY)
    coach = _restyle_items(
        store,
        prefs,
        coaching_as_style_items(extras.coaching_raw),
        COACH_STYLE_META_KEY,
    )
    # ok if either surface actually restyled; otherwise report the shared skip.
    if OUTCOME_OK in (reflect, coach):
        return OUTCOME_OK
    return reflect  # both skipped for the same reason (off / empty / fresh)


def _evaluate_alerts(
    store: Store,
    prefs: Prefs,
    source_health: list[SourceHealthRow],
    report_first: bool,
    now: datetime,
) -> str:
    day = now.astimezone(UTC).date().isoformat()
    drift_records = sum(
        row.health.malformed + row.health.unknown_field + row.health.unsupported
        for row in source_health
    )
    pace = None
    if prefs.monthly_budget_micro_usd:
        pace = spend_pace(store, day, prefs.monthly_budget_micro_usd)
    marks = store.marks_for_day(day)
    streak = ongoing_streak(marks, now)
    # The escalation counters the app's break plan reads, mirrored into the
    # toast: re-fires from today's marks, completed blocks from the local
    # day's counters (blocks are recorded against the local day).
    metrics = compute_metrics(marks) if marks else None
    local_day = datetime.now().date().isoformat()
    blocks = block_counters(store, local_day)
    context = AlertContext(
        day=day,
        report_generated_first_time=report_first,
        drift_records=drift_records,
        pace=pace,
        streak=streak,
        focus_coaching=prefs.focus_coaching,
        refires_today=metrics.refire_replies if metrics else 0,
        blocks_completed=blocks.get("block-completed", 0),
    )
    local_hhmm = datetime.now().strftime("%H:%M")
    fired = evaluate(
        store,
        prefs.alerts_enabled,
        context,
        local_hhmm,
        prefs.quiet_start,
        prefs.quiet_end,
        now,
    )
    return OUTCOME_OK if fired else OUTCOME_SKIPPED_QUIET


def run_tick(env: dict[str, str], now: datetime | None = None) -> dict[str, str]:
    """One scheduler cycle. Returns closed outcome codes per category."""
    tick_now = now or datetime.now(UTC)
    config = resolve(env)
    from practicegraph.secureio import require_personal_context

    require_personal_context(config.data_dir, env)
    outcomes: dict[str, str] = {}

    store = Store.in_data_dir(config.data_dir)
    if not store.exists():
        # The SERVICE bootstraps its own state; a user-context tick still
        # never creates state from nothing (that stays `init`'s job, and
        # test_tick_without_init_skips_everything pins it). The MSI is
        # fully declarative (C-3: no custom actions), so nothing in the
        # install path can run `init` — without this the service skips
        # every tick forever and a clean install is inert: no store, no
        # endpoint, nothing listening (field report 2026-07-20,
        # "state_db: not_initialized"). Gated on the same service flag the
        # shell's core_command sets. Idempotent and fail-open.
        if env.get(ENV_SCAN_PROFILES) != "1":
            return dict.fromkeys(_TICK_CATEGORIES, OUTCOME_SKIPPED_NOT_INITIALIZED)
        try:
            initialize(env)
        except Exception:
            return dict.fromkeys(_TICK_CATEGORIES, OUTCOME_SKIPPED_NOT_INITIALIZED)
        if not store.exists():
            return dict.fromkeys(_TICK_CATEGORIES, OUTCOME_SKIPPED_NOT_INITIALIZED)
    prefs = read_prefs(config.data_dir)

    # Dashboard availability FIRST, before any network pull. In the service
    # context (the only writable one on hardened machine-store installs) make
    # sure a ui server is up so the per-user shell can always connect instead
    # of falling back to the static report. This MUST precede the catalog
    # pulls: on a fresh install the very first tick is what raises the server,
    # and on a managed network the pulls can block for minutes (DNS/proxy
    # blackhole) — ordering the server after them left a fresh corporate
    # install with no dashboard until the pulls timed out (field report
    # 2026-07-20, "something is not starting during a regular install").
    # Fail-open like every category.
    try:
        outcomes["ui_server"] = ensure_ui_server_process(env, config)
    except Exception:
        outcomes["ui_server"] = OUTCOME_ERROR

    # Catalog pull (FR-EMT-4): a freshly pulled rate card applies to this very
    # tick's activation; failures never block anything below.
    try:
        outcomes["catalog_pull"] = pull_catalog(store, config, tick_now)
    except Exception:
        outcomes["catalog_pull"] = OUTCOME_ERROR
    try:
        outcomes["playbooks_pull"] = pull_public_playbooks(store, config, tick_now)
    except Exception:
        outcomes["playbooks_pull"] = OUTCOME_ERROR
    try:
        outcomes["token_prices_pull"] = pull_public_token_prices(store, config, tick_now)
    except Exception:
        outcomes["token_prices_pull"] = OUTCOME_ERROR

    try:
        outcomes["training_pull"] = pull_public_training(store, config, tick_now)
    except Exception:
        outcomes["training_pull"] = OUTCOME_ERROR
    # Independent-mode skills: pull directly from the configured public URL
    # (fills the gap for serverless agents; the server-proxied catalog wins when
    # both are configured). Fail-open, never blocks the tick.
    try:
        outcomes["skills_pull"] = pull_public_skills(store, config, tick_now)
    except Exception:
        outcomes["skills_pull"] = OUTCOME_ERROR
    try:
        outcomes["news_pull"] = pull_public_news(store, config, tick_now)
    except Exception:
        outcomes["news_pull"] = OUTCOME_ERROR
    try:
        outcomes["community_pull"] = pull_public_community(store, config, tick_now)
    except Exception:
        outcomes["community_pull"] = OUTCOME_ERROR
    try:
        outcomes["build_ideas_pull"] = pull_public_build_ideas(store, config, tick_now)
    except Exception:
        outcomes["build_ideas_pull"] = OUTCOME_ERROR
    try:
        outcomes["models_pull"] = pull_public_models(store, config, tick_now)
    except Exception:
        outcomes["models_pull"] = OUTCOME_ERROR
    try:
        outcomes["advisor_pull"] = pull_public_advisor(store, config, tick_now)
    except Exception:
        outcomes["advisor_pull"] = OUTCOME_ERROR
    try:
        outcomes["docs_pull"] = pull_public_docs(store, config, tick_now)
    except Exception:
        outcomes["docs_pull"] = OUTCOME_ERROR
    try:
        outcomes["model_catalog_pull"] = pull_public_model_catalog(store, config, tick_now)
    except Exception:
        outcomes["model_catalog_pull"] = OUTCOME_ERROR
    try:
        outcomes["releases_pull"] = pull_public_releases(store, config, tick_now)
    except Exception:
        outcomes["releases_pull"] = OUTCOME_ERROR
    # The rate card is pulled immediately before it is activated, so a price
    # correction published today takes effect on this tick. `ingest` below
    # re-prices all history when the active card version changes, so the
    # correction reaches past estimates too rather than leaving a seam.
    try:
        outcomes["ratecard_pull"] = pull_public_ratecard(store, config, tick_now)
    except Exception:
        outcomes["ratecard_pull"] = OUTCOME_ERROR
    with contextlib.suppress(Exception):
        activate_rate_card_from(config.data_dir)

    # Is there a newer build? Once a day, signature-verified before it is
    # cached, and it never installs anything - it produces a sentence and a
    # link the person acts on.
    try:
        outcomes["update_pull"] = pull_update_manifest(store, config, tick_now)
    except Exception:
        outcomes["update_pull"] = OUTCOME_ERROR
    # Say so once: a verified newer release earns one toast per version,
    # outside quiet hours, opening the app on the update card.
    try:
        outcomes["update_alerts"] = evaluate_update(store, config.data_dir, tick_now, __version__)
    except Exception:
        outcomes["update_alerts"] = OUTCOME_ERROR

    source_health: list[SourceHealthRow] = []
    try:
        source_health = ingest(env, store, tick_now)
        outcomes["collect"] = OUTCOME_OK
    except Exception:
        outcomes["collect"] = OUTCOME_ERROR

    try:
        versions = {item.tool: item.installed for item in installed_versions(env)}
        refresh_followups(store, tick_now, versions)
        outcomes["setup_followup"] = OUTCOME_OK
    except Exception:
        outcomes["setup_followup"] = OUTCOME_ERROR

    report_first = False
    report_extras: ShellExtras | None = None
    try:
        report_first, report_extras = _write_report_artifact(
            config, store, prefs, source_health, tick_now
        )
        outcomes["report_artifact"] = OUTCOME_OK
    except Exception:
        outcomes["report_artifact"] = OUTCOME_ERROR

    # Daily wording refresh for the recognition band: local-only, best-effort,
    # facts frozen. Never runs (and never spends) unless the composed
    # sentences changed since the last styling and the user opted in.
    try:
        if report_extras is None:
            outcomes["restyle"] = OUTCOME_ERROR
        else:
            outcomes["restyle"] = _restyle_reflections(store, prefs, report_extras)
    except Exception:
        outcomes["restyle"] = OUTCOME_ERROR

    # Coaching acknowledgment ledger (A3): record this week's cue-pillar and its
    # baseline, and retire any entry whose ack window has fully passed. The ONLY
    # place the ledger is written — view reads compose the acknowledgment purely.
    # Local-only, best-effort; a failure never blocks the tick.
    try:
        ledger_day = (
            report_extras.local_day
            if report_extras is not None and report_extras.local_day is not None
            else tick_now.astimezone(UTC).date()
        )
        record_ledger_entry(
            store,
            ledger_day,
            report_extras.schedule if report_extras is not None else None,
        )
        mark_acknowledgment(store, ledger_day)
        # Advisor closed loop: record a surfaced routing call (and retire an
        # expired one) so later windows can audit it against the person's own
        # numbers. Same tick-only discipline as the coach ledger above.
        if report_extras is not None and report_extras.advisor is not None:
            accounting_day = (
                report_extras.accounting_day_utc
                if report_extras.accounting_day_utc is not None
                else tick_now.astimezone(UTC).date()
            )
            record_advisor_ledger(
                store,
                report_extras.advisor,
                gather_receipts(store, accounting_day),
                ledger_day,
            )
        outcomes["ledger"] = OUTCOME_OK
    except Exception:
        outcomes["ledger"] = OUTCOME_ERROR

    consent = read_consent(config.data_dir)
    if not consent.emission_enabled:
        outcomes["emit_build"] = OUTCOME_SKIPPED_CONSENT_OFF
        outcomes["emit_flush"] = OUTCOME_SKIPPED_CONSENT_OFF
    else:
        try:
            build_and_queue(store, config, tick_now, source_health)
            outcomes["emit_build"] = OUTCOME_OK
        except Exception:
            outcomes["emit_build"] = OUTCOME_ERROR
        try:
            org_token = resolve_org_token(env, config)
            flush_result = flush_queue(store, config, tick_now, org_token)
            if "skipped_unconfigured" in flush_result:
                outcomes["emit_flush"] = OUTCOME_SKIPPED_UNCONFIGURED
            else:
                outcomes["emit_flush"] = OUTCOME_OK
        except Exception:
            outcomes["emit_flush"] = OUTCOME_ERROR

    try:
        outcomes["alerts"] = _evaluate_alerts(store, prefs, source_health, report_first, tick_now)
    except Exception:
        outcomes["alerts"] = OUTCOME_ERROR

    try:
        outcomes["news_alerts"] = evaluate_news(store, config.data_dir, tick_now)
    except Exception:
        outcomes["news_alerts"] = OUTCOME_ERROR

    try:
        store.meta_set("last_tick_at", tick_now.isoformat(timespec="seconds"))
        store.meta_set_json("last_tick_outcomes", outcomes)
    except Exception:
        outcomes["persist_tick_state"] = OUTCOME_ERROR

    # Closed status file for the tray (read-only shell surface, C-4). Counts,
    # closed codes, and a relative filename only — never paths or content.
    try:
        day = tick_now.astimezone(UTC).date().isoformat()
        status_doc = {
            "schema": "practicegraph.status/1",
            "last_tick_at": tick_now.astimezone(UTC).isoformat(timespec="seconds"),
            "outcomes": dict(sorted(outcomes.items())),
            "queue_counts": store.queue_counts(),
            "consent_enabled": consent.emission_enabled,
            "latest_report": f"daily-{day}.html",
            "news_unread": news_reading(store, config.data_dir, tick_now)["unread_count"],
        }
        (config.data_dir / STATUS_FILE_NAME).write_text(
            json.dumps(status_doc, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        outcomes["status_file"] = OUTCOME_ERROR

    return outcomes


def run_loop(
    env: dict[str, str],
    interval_s: int = DEFAULT_INTERVAL_S,
    once: bool = False,
) -> int:
    """The service loop: initialize, then tick forever (or once). Survives
    indefinitely unattended; a tick can never raise out of the loop."""
    initialize(env)
    while True:
        outcomes = run_tick(env)
        line = " ".join(f"{key}={value}" for key, value in sorted(outcomes.items()))
        print(f"tick {datetime.now(UTC).isoformat(timespec='seconds')} {line}", flush=True)
        if once:
            return 0
        time.sleep(interval_s)
