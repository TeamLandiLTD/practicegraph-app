"""Incremental ingest and daily history (FR-SRC-7, FR-ANL-7).

Each scan re-parses only files whose (size, mtime, parser version) changed —
unchanged files are skipped entirely, which keeps every tick O(today's active
logs). A re-parsed file atomically *replaces* everything it previously
contributed, so re-parses can never double-count. Log rotation/truncation
changes the cursor fingerprint and triggers a clean re-read; a parser version
bump invalidates every cursor (forced rescan); deleted files are pruned.

History (per-day, per tool/model contributions plus a compact per-event
activity timeline) lives only in the local store (INV-1) and feeds reports,
trends, emit building, and focus metrics.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from practicegraph.analysis.aggregate import DailySnapshot, UsageRow
from practicegraph.analysis.ratecard import estimate_cost_micro_usd
from practicegraph.events import TokenCounts, TurnEvent, TurnKind
from practicegraph.sources import ParseHealth, QuotaSnapshot, RateLimitGauge, SourceHealthRow
from practicegraph.sources.codex import CodexAdapter
from practicegraph.sources.context import summarize as summarize_context
from practicegraph.sources.registry import ADAPTERS
from practicegraph.sources.replay import ReplayPlan
from practicegraph.sources.replay import prepare as prepare_replays
from practicegraph.store import (
    ContributionRow,
    DayMarkRow,
    MarkRow,
    RateLimitMarkRow,
    SessionTotalRow,
    Store,
    WorkSpanRow,
)

# W2.0 session grain: bumping this re-derives session totals for ALL history on
# the next scan (the rate-card invalidation precedent below). Deliberately NOT
# a parser_version bump — no parser changed, and parser_version is reported in
# source-health rows that reach the wire.
SESSION_GRAIN_VERSION = "1"

# W4 work spans: bump to force the one backfill rescan that derives spans for
# history already ingested (the session-grain precedent — a meta marker, NOT a
# parser_version bump, because no parser changed and parser_version is
# reported in source-health rows that reach the wire).
WORK_SPAN_VERSION = "1"

# A span breaks when consecutive assistant turns on the same (session,
# project, branch) sit further apart than this. 30 minutes is the FINEST
# boundary the store keeps: the read-time fold can only merge spans, never
# split them, so the stored grain bounds every episode threshold from below.
# The 91-day calibration run showed the
# reading is insensitive to this choice between 30 and 120 minutes.
WORK_SPAN_GAP_MIN = 30

# Contribution values layout (append-only storage contract): the writer below
# and every reader go through ContributionRow for names.
_CONTRIBUTION_FIELDS = len(ContributionRow._fields)


def path_identity(path: Path) -> str:
    """FR-SRC-7 identity hash — the full path is never persisted."""
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _contributions_from_events(
    events: tuple[TurnEvent, ...],
) -> list[tuple[str, str, str, list[int]]]:
    grouped: dict[tuple[str, str, str], list[int]] = {}
    for event in events:
        key = (event.day_key().isoformat(), event.tool.value, event.model)
        values = grouped.setdefault(key, [0] * _CONTRIBUTION_FIELDS)
        if event.kind is TurnKind.ASSISTANT_TURN:
            values[0] += 1
            cost = estimate_cost_micro_usd(event.tokens, event.model, fast=event.fast)
            if cost is None:
                values[11] += 1
            else:
                values[10] += cost
        else:
            values[1] += 1
        values[2] += event.tokens.input
        values[3] += event.tokens.output
        values[4] += event.tokens.cached
        values[5] += event.tokens.cache_creation
        values[6] += event.tokens.reasoning
        values[7] += event.tool_calls
        values[8] += event.retries
        values[9] += event.interruptions
        values[12] += event.tokens.cache_creation_1h
        values[13] += event.rework_edits
        values[14] += event.commands_run
        values[15] += event.commands_failed
        values[16] += event.commands_slow
        values[17] += event.compactions
        values[18] += event.git_commit_attempts
        values[19] += event.test_run_attempts
        values[20] += event.files_created
        values[21] += event.doc_files_created
        values[22] += event.export_writes
        values[23] += event.files_edited
    return [
        (day, tool, model, values)
        for (day, tool, model), values in sorted(grouped.items())
    ]


def _marks_from_events(events: tuple[TurnEvent, ...]) -> list[DayMarkRow]:
    marks: list[DayMarkRow] = []
    for event in events:
        marks.append(
            (
                event.day_key().isoformat(),
                f"{event.source_id}:{event.session_id}",
                event.timestamp.astimezone(UTC).isoformat(timespec="seconds"),
                event.kind.value,
                event.tool_calls,
                event.retries,
                event.interruptions,
                # Amendment 1: each command failure is timeline-visible so P2
                # can compute inter-event gaps — a counter per mark, never a
                # row per command.
                event.commands_failed,
                # P2: compactions ride the timeline too, so the marathon
                # gate can group them per session.
                event.compactions,
                # P3: the behavioral gate and the environment identity
                # hashes — never the values.
                int(event.interactive),
                event.cwd_hash,
                event.branch_hash,
                # Waved approvals (appended LAST, the contract): the
                # "go"-class human-reply flag — length-derived, never text.
                int(event.short_reply),
                # Positive source-classified human-origin signal. Background
                # assistant and tool traffic remains false.
                int(event.human_initiated),
            )
        )
    return marks


@dataclass(slots=True)
class _SessionAccumulator:
    """One session's running totals during ingest (W2.0). Mutable and typed —
    the counters are summed, the two timestamps take the true span."""

    tool: str
    first_ts: str
    last_ts: str
    assistant_turns: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    unpriced_turns: int = 0
    compactions: int = 0
    tool_calls: int = 0
    git_commit_attempts: int = 0
    test_run_attempts: int = 0


def _session_totals_from_events(
    events: tuple[TurnEvent, ...],
) -> list[SessionTotalRow]:
    """Session-grain money and volume (W2.0), grouped per (day, session).

    The same events that feed the day x tool x model contributions, summed at
    the grain that answers "what did that session cost" — which no other table
    can, because contributions discard session identity and marks carry no
    tokens. Cost is priced exactly as the contributions path prices it (same
    rate card, same unpriced fallback), so the two agree to the micro-USD.

    A session crossing midnight writes one row per day; the reader merges by
    session key. `session_key` matches the marks table's identity so the two
    timelines join locally — and, like it, never leaves the machine.
    """
    grouped: dict[tuple[str, str], _SessionAccumulator] = {}
    for event in events:
        session_key = f"{event.source_id}:{event.session_id}"
        key = (event.day_key().isoformat(), session_key)
        stamp = event.timestamp.astimezone(UTC).isoformat(timespec="seconds")
        acc = grouped.get(key)
        if acc is None:
            acc = _SessionAccumulator(
                tool=event.tool.value, first_ts=stamp, last_ts=stamp
            )
            grouped[key] = acc
        acc.first_ts = min(acc.first_ts, stamp)
        acc.last_ts = max(acc.last_ts, stamp)
        if event.kind is TurnKind.ASSISTANT_TURN:
            acc.assistant_turns += 1
            cost = estimate_cost_micro_usd(event.tokens, event.model, fast=event.fast)
            if cost is None:
                acc.unpriced_turns += 1
            else:
                acc.cost_micro_usd += cost
        acc.input_tokens += event.tokens.input
        acc.cached_tokens += event.tokens.cached
        acc.cache_creation_tokens += event.tokens.cache_creation
        acc.output_tokens += event.tokens.output
        acc.compactions += event.compactions
        acc.tool_calls += event.tool_calls
        acc.git_commit_attempts += event.git_commit_attempts
        acc.test_run_attempts += event.test_run_attempts
    return [
        (
            day,
            session_key,
            acc.tool,
            acc.first_ts,
            acc.last_ts,
            acc.assistant_turns,
            acc.input_tokens,
            acc.cached_tokens,
            acc.cache_creation_tokens,
            acc.output_tokens,
            acc.cost_micro_usd,
            acc.unpriced_turns,
            acc.compactions,
            acc.tool_calls,
            acc.git_commit_attempts,
            acc.test_run_attempts,
        )
        for (day, session_key), acc in sorted(grouped.items())
    ]


def _work_spans_from_events(
    events: tuple[TurnEvent, ...],
) -> list[WorkSpanRow]:
    """Deterministic activity spans (W4): per (session, cwd, branch), maximal
    runs of assistant turns with every internal gap <= WORK_SPAN_GAP_MIN,
    split at UTC midnight (the session_totals precedent — the reader
    re-chains). Costs are priced through the same rate card as contributions,
    so all three grains agree to the micro-USD. Identity is carried as the
    hashes the events already hold; user turns don't extend spans (a lone
    human message with no assistant work is not work-unit evidence)."""
    turns = sorted(
        (e for e in events if e.kind is TurnKind.ASSISTANT_TURN),
        key=lambda e: (e.session_id, e.cwd_hash, e.branch_hash, e.timestamp),
    )
    rows: list[WorkSpanRow] = []
    acc: dict[str, int] | None = None
    key: tuple[str, str, str] | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    def flush() -> None:
        nonlocal acc, first_ts, last_ts
        if acc is None or first_ts is None or last_ts is None or key is None:
            return
        rows.append(
            (
                first_ts.date().isoformat(),
                f"{key[0]}",
                key[1],
                key[2],
                first_ts.isoformat(timespec="seconds"),
                last_ts.isoformat(timespec="seconds"),
                acc["turns"],
                acc["input"],
                acc["cached"],
                acc["creation"],
                acc["output"],
                acc["cost"],
                acc["unpriced"],
                acc["commits"],
                acc["tests"],
                acc["created"],
                acc["docs"],
                acc["exports"],
                acc["edited"],
            )
        )
        acc = None
        first_ts = None
        last_ts = None

    for event in turns:
        stamp = event.timestamp.astimezone(UTC)
        event_key = (
            f"{event.source_id}:{event.session_id}",
            event.cwd_hash,
            event.branch_hash,
        )
        gap_break = (
            last_ts is not None
            and (stamp - last_ts).total_seconds() > WORK_SPAN_GAP_MIN * 60
        )
        day_break = last_ts is not None and stamp.date() != last_ts.date()
        if event_key != key or gap_break or day_break:
            flush()
            key = event_key
            first_ts = stamp
            acc = {
                "turns": 0, "input": 0, "cached": 0, "creation": 0,
                "output": 0, "cost": 0, "unpriced": 0, "commits": 0,
                "tests": 0, "created": 0, "docs": 0, "exports": 0,
                "edited": 0,
            }
        assert acc is not None
        last_ts = stamp
        if first_ts is None:
            first_ts = stamp
        acc["turns"] += 1
        cost = estimate_cost_micro_usd(event.tokens, event.model, fast=event.fast)
        if cost is None:
            acc["unpriced"] += 1
        else:
            acc["cost"] += cost
        acc["input"] += event.tokens.input
        acc["cached"] += event.tokens.cached
        acc["creation"] += event.tokens.cache_creation
        acc["output"] += event.tokens.output
        acc["commits"] += event.git_commit_attempts
        acc["tests"] += event.test_run_attempts
        acc["created"] += event.files_created
        acc["docs"] += event.doc_files_created
        acc["exports"] += event.export_writes
        acc["edited"] += event.files_edited
    flush()
    return rows


def _rate_limit_rows(
    gauges: tuple[RateLimitGauge, ...], snapshots: tuple[QuotaSnapshot, ...] = (),
) -> list[RateLimitMarkRow]:
    """P4: provider rate-limit gauge readings ride their own table with the
    same per-file replace lifecycle as marks. Two integers per reading —
    never on a TurnEvent, never on the wire."""
    rows: list[RateLimitMarkRow] = []
    if snapshots:
        return [(s.timestamp.astimezone(UTC).date().isoformat(),
                 s.timestamp.astimezone(UTC).isoformat(timespec="seconds"),
                 s.used_pct_tenths, s.window_minutes, s.window_kind,
                 s.resets_at, s.account_hash, s.pool_hash) for s in snapshots]
    for ts, used_pct_tenths, window_minutes in gauges:
        utc = ts.astimezone(UTC)
        rows.append(
            (
                utc.date().isoformat(),
                utc.isoformat(timespec="seconds"),
                used_pct_tenths,
                window_minutes,
            )
        )
    return rows


def ingest(env: dict[str, str], store: Store, now: datetime) -> list[SourceHealthRow]:
    """Incremental scan of every source. Returns per-lane health rows built
    from the *persisted* counters (FR-SRC-6), which cover all known files —
    including the ones skipped as unchanged this tick.

    Costs are persisted at parse time, so a rate-card change invalidates every
    cursor and forces one full re-parse — history is then re-priced under the
    new card instead of silently mixing rates."""
    from practicegraph.analysis.ratecard import active_rate_card

    active_version = active_rate_card().version
    if store.meta_get("priced_with_rate_card") != active_version:
        store.invalidate_all_cursors()
        store.meta_set("priced_with_rate_card", active_version)
    # W2.0: session totals are derived at ingest, so existing history carries
    # none until its files are re-read. One forced rescan backfills them, then
    # the marker stops it ever running again.
    if store.meta_get("session_grain_version") != SESSION_GRAIN_VERSION:
        store.invalidate_all_cursors()
        store.meta_set("session_grain_version", SESSION_GRAIN_VERSION)
    # W4: work spans backfill the same way — one forced rescan, then quiet.
    if store.meta_get("work_span_version") != WORK_SPAN_VERSION:
        store.invalidate_all_cursors()
        store.meta_set("work_span_version", WORK_SPAN_VERSION)
    if store.meta_get("session_evidence_version") != "1":
        store.invalidate_all_cursors()
        store.meta_set("session_evidence_version", "1")
    health_rows: list[SourceHealthRow] = []
    for adapter in ADAPTERS:
        live_hashes: set[str] = set()
        try:
            paths = adapter.discover(env)
        except Exception:
            paths = []
        plans = prepare_replays(paths) if isinstance(adapter, CodexAdapter) else {}
        for path in sorted(paths, key=str):
            path_hash = path_identity(path)
            live_hashes.add(path_hash)
            try:
                stat = path.stat()
            except OSError:
                continue
            plan = plans.get(path, ReplayPlan())
            fingerprint = (stat.st_size, stat.st_mtime_ns, plan.version(adapter.parser_version))
            if store.cursor_get(adapter.source_id, path_hash) == fingerprint:
                continue  # unchanged — contributions already in history
            try:
                seen = store.seen_turn_keys(adapter.source_id, path_hash)
                result = (adapter.parse_file(path, plan) if isinstance(adapter, CodexAdapter)
                          else adapter.parse_file_seen(path, seen))
                context = summarize_context(path, adapter.source_id)
            except OSError:
                continue  # unreadable file: retried next tick (cursor untouched)
            evidence = []
            session_ids = {event.session_id for event in result.events}
            if len(session_ids) == 1:
                context.update(parent_key=f"{adapter.source_id}:{plan.parent_id}"
                               if plan.parent_id else "", relation=plan.relation,
                               replay_status=plan.status, replay_records=plan.records)
                evidence.append((max(e.day_key().isoformat() for e in result.events),
                                 f"{adapter.source_id}:{next(iter(session_ids))}",
                                 json.dumps(context, sort_keys=True)))
            store.replace_file_data(
                source_id=adapter.source_id,
                path_hash=path_hash,
                cursor=fingerprint,
                contributions=_contributions_from_events(result.events),
                marks=_marks_from_events(result.events),
                health=result.health,
                turn_keys=result.dedup_keys,
                now=now,
                rate_limit_marks=_rate_limit_rows(result.gauges, result.quota_snapshots),
                work_spans=_work_spans_from_events(result.events),
                session_totals=_session_totals_from_events(result.events),
                session_evidence=evidence,
            )
        store.prune_missing_files(adapter.source_id, live_hashes)
        lane = store.lane_health(adapter.source_id)
        health_rows.append(
            SourceHealthRow(
                source_id=adapter.source_id,
                capability=adapter.capability,
                parser_version=adapter.parser_version,
                health=ParseHealth(
                    seen=lane[0],
                    parsed=lane[1],
                    skipped=lane[2],
                    malformed=lane[3],
                    unknown_field=lane[4],
                    unsupported=lane[5],
                ),
            )
        )
    return health_rows


def snapshot_for_day(
    store: Store, day: date, source_health: list[SourceHealthRow]
) -> DailySnapshot:
    """Rebuild the render snapshot for any day from history. Equivalent to the
    direct-parse snapshot (pinned by tests)."""
    rows = []
    for tool, model, values in store.day_usage_rows(day.isoformat()):
        row = ContributionRow.from_values(values)
        rows.append(
            UsageRow(
                tool=tool,
                model=model,
                assistant_turns=row.assistant_turns,
                user_turns=row.user_turns,
                tokens=TokenCounts(
                    input=row.input_tokens,
                    output=row.output_tokens,
                    cached=row.cached_tokens,
                    cache_creation=row.cache_creation_tokens,
                    reasoning=row.reasoning_tokens,
                    cache_creation_1h=row.cache_creation_1h_tokens,
                ),
                tool_calls=row.tool_calls,
                retries=row.retries,
                interruptions=row.interruptions,
                cost_micro_usd=row.cost_micro_usd,
                unpriced_turns=row.unpriced_turns,
                rework_edits=row.rework_edits,
                commands_run=row.commands_run,
                commands_failed=row.commands_failed,
                commands_slow=row.commands_slow,
            )
        )
    return DailySnapshot(
        day=day,
        rows=tuple(rows),
        total_cost_micro_usd=sum(row.cost_micro_usd for row in rows),
        total_unpriced_turns=sum(row.unpriced_turns for row in rows),
        total_assistant_turns=sum(row.assistant_turns for row in rows),
        total_user_turns=sum(row.user_turns for row in rows),
        total_tool_calls=sum(row.tool_calls for row in rows),
        total_retries=sum(row.retries for row in rows),
        total_interruptions=sum(row.interruptions for row in rows),
        session_count=store.session_count_for_day(day.isoformat()),
        source_health=tuple(sorted(source_health, key=lambda row: row.source_id)),
    )


@dataclass(frozen=True, slots=True)
class DayPoint:
    day: str
    cost_micro_usd: int
    tokens_total: int
    assistant_turns: int


def spend_series(store: Store, end_day: date, days: int) -> list[DayPoint]:
    """One point per calendar day in the window (zero-filled): trend input."""
    start = end_day - timedelta(days=days - 1)
    by_day = {
        row[0]: row
        for row in store.spend_series(start.isoformat(), end_day.isoformat())
    }
    points = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).isoformat()
        row = by_day.get(day)
        if row is None:
            points.append(DayPoint(day, 0, 0, 0))
        else:
            points.append(DayPoint(day, row[1], row[2], row[3]))
    return points


PROFILE_BLOCK_WINDOW_DAYS = 90  # longest-block lookback (named constant)


@dataclass(frozen=True, slots=True)
class ProfileStats:
    """Profile-grade lifetime/streak stats, computed entirely from local
    history — the endpoint's answer to vendor profile pages."""

    lifetime_tokens: int
    lifetime_cost_micro_usd: int
    active_days_total: int
    peak_day: str | None
    peak_day_tokens: int
    longest_block_min: int
    current_streak_days: int
    longest_streak_days: int


def _day_streaks(active_days: list[str], end_day: date) -> tuple[int, int]:
    """(current, longest) consecutive active-day streaks. The current streak
    may end today or yesterday (today might simply not have activity yet)."""
    if not active_days:
        return 0, 0
    dates = [date.fromisoformat(day) for day in active_days]
    longest = 1
    run = 1
    for previous, current in itertools.pairwise(dates):
        run = run + 1 if (current - previous).days == 1 else 1
        longest = max(longest, run)
    current_streak = 0
    cursor = end_day if dates[-1] == end_day else end_day - timedelta(days=1)
    day_set = set(dates)
    while cursor in day_set:
        current_streak += 1
        cursor -= timedelta(days=1)
    return current_streak, longest


def profile_stats(store: Store, end_day: date) -> ProfileStats:
    from practicegraph.analysis.focus import compute_metrics, interactive_marks

    series = store.spend_series("0001-01-01", end_day.isoformat())
    lifetime_tokens = sum(row[2] for row in series)
    lifetime_cost = sum(row[1] for row in series)
    peak_day: str | None = None
    peak_tokens = 0
    for day, _cost, tokens, _turns in series:
        if tokens > peak_tokens:
            peak_day, peak_tokens = day, tokens
    active_days = store.active_days("0001-01-01", end_day.isoformat())
    current_streak, longest_streak = _day_streaks(active_days, end_day)

    block_from = (end_day - timedelta(days=PROFILE_BLOCK_WINDOW_DAYS - 1)).isoformat()
    marks_by_day: dict[str, list[MarkRow]] = {}
    for row in store.marks_between(block_from, end_day.isoformat()):
        marks_by_day.setdefault(row[0], []).append(row[1:])
    # P3 behavioral gate: the longest-block walk reads human-driven marks
    # only — a sidechain agent grinding away is not *your* deep-work block.
    longest_block = max(
        (
            compute_metrics(interactive_marks(marks)).longest_block_min
            for marks in marks_by_day.values()
        ),
        default=0,
    )
    return ProfileStats(
        lifetime_tokens=lifetime_tokens,
        lifetime_cost_micro_usd=lifetime_cost,
        active_days_total=len(active_days),
        peak_day=peak_day,
        peak_day_tokens=peak_tokens,
        longest_block_min=longest_block,
        current_streak_days=current_streak,
        longest_streak_days=longest_streak,
    )


# ---- range views (Today / 7 days / 30 days / all time) ------------------------

# Closed range catalog: (panel id, label, window days; None = all history).
RANGE_KINDS: tuple[tuple[str, str, int | None], ...] = (
    ("range-week", "Last 7 days", 7),
    ("range-month", "Last 30 days", 30),
    ("range-all", "All time", None),
)


@dataclass(frozen=True, slots=True)
class ToolSplit:
    """One tool's slice of a range — the multivendor answer at a glance."""

    tool: str
    cost_micro_usd: int
    assistant_turns: int
    tokens_total: int
    share_pct: int  # of range cost; of turns when the range priced nothing
    top_model: str
    last_active: str  # most recent active day over ALL history, not the range


@dataclass(frozen=True, slots=True)
class ModelRow:
    tool: str
    model: str
    assistant_turns: int
    tokens_total: int
    cost_micro_usd: int
    unpriced_turns: int = 0


@dataclass(frozen=True, slots=True)
class RangeSummary:
    range_id: str
    label: str
    from_day: str
    to_day: str
    active_days: int
    cost_micro_usd: int
    tokens_total: int
    assistant_turns: int
    unpriced_turns: int
    tools: tuple[ToolSplit, ...]
    models: tuple[ModelRow, ...]  # top by est. cost, zero rows filtered
    series: tuple[tuple[str, int], ...]  # (day or YYYY-MM bucket, cost)


RANGE_MODEL_ROWS_MAX = 8


def _tokens_total(values: list[int]) -> int:
    # Reasoning is already part of output; cache_creation_1h is a subset too.
    return values[2] + values[3] + values[4] + values[5]


def build_range_summary(
    store: Store, end_day: date, range_id: str, label: str, days: int | None
) -> RangeSummary:
    """Deterministic aggregation for one range (INV-6: sums and sorts only)."""
    from practicegraph.report.format import percent

    if days is not None:
        from_day = (end_day - timedelta(days=days - 1)).isoformat()
    else:
        all_days = store.active_days("0001-01-01", end_day.isoformat())
        from_day = all_days[0] if all_days else end_day.isoformat()
    to_day = end_day.isoformat()
    rows = store.usage_rows_between(from_day, to_day)
    last_active = store.tool_last_active()

    cost = sum(values[10] for _t, _m, values in rows)
    turns = sum(values[0] for _t, _m, values in rows)
    tokens = sum(_tokens_total(values) for _t, _m, values in rows)
    unpriced = sum(values[11] for _t, _m, values in rows)

    by_tool: dict[str, list[int]] = {}
    top_model: dict[str, tuple[int, int, str]] = {}
    for tool, model, values in rows:
        totals = by_tool.setdefault(tool, [0, 0, 0])
        totals[0] += values[10]
        totals[1] += values[0]
        totals[2] += _tokens_total(values)
        candidate = (values[10], values[0], model)
        best = top_model.get(tool)
        if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
            top_model[tool] = candidate
    tools = tuple(
        ToolSplit(
            tool=tool,
            cost_micro_usd=totals[0],
            assistant_turns=totals[1],
            tokens_total=totals[2],
            share_pct=(
                percent(totals[0], cost) if cost > 0 else percent(totals[1], turns)
            ),
            top_model=top_model[tool][2],
            last_active=last_active.get(tool, to_day),
        )
        for tool, totals in sorted(
            by_tool.items(), key=lambda item: (-item[1][0], -item[1][1], item[0])
        )
    )

    models = tuple(
        ModelRow(tool, model, values[0], _tokens_total(values), values[10], values[11])
        for tool, model, values in sorted(
            rows, key=lambda row: (-row[2][10], -row[2][0], row[0], row[1])
        )
        if values[0] > 0 or values[10] > 0
    )[:RANGE_MODEL_ROWS_MAX]

    if days is not None:
        series = tuple(
            (point.day, point.cost_micro_usd)
            for point in spend_series(store, end_day, days)
        )
    else:
        monthly: dict[str, int] = {}
        for day, day_cost, _tokens, _turns in store.spend_series(from_day, to_day):
            month = day[:7]
            monthly[month] = monthly.get(month, 0) + day_cost
        series = tuple(sorted(monthly.items()))

    return RangeSummary(
        range_id=range_id,
        label=label,
        from_day=from_day,
        to_day=to_day,
        active_days=len(store.active_days(from_day, to_day)),
        cost_micro_usd=cost,
        tokens_total=tokens,
        assistant_turns=turns,
        unpriced_turns=unpriced,
        tools=tools,
        models=models,
        series=series,
    )


def gather_ranges(store: Store, end_day: date) -> list[RangeSummary]:
    return [
        build_range_summary(store, end_day, range_id, label, days)
        for range_id, label, days in RANGE_KINDS
    ]


@dataclass(frozen=True, slots=True)
class WeekOverWeek:
    cost_delta_pct: int
    tokens_delta_pct: int


def week_over_week(store: Store, end_day: date) -> WeekOverWeek | None:
    """Deltas vs the prior 7 days; None until the prior week has activity."""
    this_week = spend_series(store, end_day, 7)
    prior_week = spend_series(store, end_day - timedelta(days=7), 7)
    prior_cost = sum(p.cost_micro_usd for p in prior_week)
    prior_tokens = sum(p.tokens_total for p in prior_week)
    if prior_cost <= 0 and prior_tokens <= 0:
        return None
    this_cost = sum(p.cost_micro_usd for p in this_week)
    this_tokens = sum(p.tokens_total for p in this_week)

    def _delta(now_value: int, prior: int) -> int:
        if prior <= 0:
            return 0
        return (now_value - prior) * 100 // prior

    return WeekOverWeek(
        cost_delta_pct=_delta(this_cost, prior_cost),
        tokens_delta_pct=_delta(this_tokens, prior_tokens),
    )
