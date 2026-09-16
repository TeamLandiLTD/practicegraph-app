"""Focus & cognitive-load coaching (FR-FOC).

Daily metrics computed from the local activity timeline; a closed five-tip
catalog with fixed copy and trigger predicates; and the on-time break-nudge
evaluator. All thresholds are named constants (FR-FOC-1). Everything in this
module is local-only forever (FR-FOC-7, NFR-PRV-6): no symbol defined here may
appear in a wire payload, which the contract tests enforce by field-name scan.

Copy rule (FR-FOC-8): behavioral, evidence-aligned language only — the
forbidden-lexicon test runs over this catalog and every rendered surface.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime

from practicegraph.analysis.schedule import (
    ScheduleProfile,
    classify_time,
    compatibility_utc_schedule,
)
from practicegraph.store import (
    MARK_BRANCH_HASH,
    MARK_CWD_HASH,
    MARK_FAILURES,
    MARK_INTERACTIVE,
    MARK_SHORT_REPLY,
    MARK_TOOL_CALLS,
    MarkRow,
)

# Named threshold constants (FR-FOC-1, FR-FOC-4).
SESSION_SPLIT_GAP_MIN = 30  # a >30-min silence splits a session's focus blocks
ACTIVITY_WINDOW_MIN = 15  # concurrency / switch window
STREAK_BREAK_GAP_MIN = 15  # >=15-min gap ends a cross-session streak
BURST_MIN_EVENTS = 6  # events per burst window
BURST_WINDOW_MIN = 10  # burst window length
NUDGE_STREAK_MIN = 120  # continuous minutes before the one daily nudge
MAX_TIPS_SHOWN = 2  # FR-FOC-2: at most two observation tips
# Reflex replies: a follow-up landing within seconds of the previous answer
# is a behavioral proxy for accepting output without a real read (the
# structural signal behind over-reliance; measured, never judged).
REFLEX_GAP_MAX_S = 30
REFLEX_MIN_FOLLOWUPS = 10  # tip only when the day has enough signal
REFLEX_SHARE_MIN_PCT = 60
# Refire after failure (P2): a HUMAN reply (agent auto-continuation never
# counts) landing within minutes of the session's most recent failed run.
# Calibrated on real history 2026-07-05 (46 active days: median 5/day,
# p90 19, max 112 at the 5-minute window) so the finding flags the tail,
# never a normal day. Descriptive forever — INFO severity by decision.
REFIRE_WINDOW_MIN = 5
REFIRE_MIN_COUNT = 15
# Waved-through approvals: the automation-bias signature — a long delegated
# run ends in a summary/ask and the human replies almost instantly with a
# "go"-class message (approval without review; the acceptance-without-
# engagement pattern the P1 rework amendment names). Calibrated on 50 real
# Claude main transcripts 2026-07-05: work stretches at approval moments ran
# p50=5 p75=18 p90=41 tooled turns, so 8 flags genuinely long runs; close
# gaps ran p50≈4 min (a real handoff read takes minutes) with the <=90s
# short-reply tail at 5% of moments (widening to 120s added ~nothing), so
# 90s isolates the reflex, never the read. Reply shortness is the parser-side
# SHORT_REPLY_MAX_CHARS gate (sources/__init__.py: 17% of real human
# replies). Descriptive forever — INFO severity by decision.
APPROVAL_STRETCH_MIN_TURNS = 8
APPROVAL_FAST_MAX_S = 90
# Finding/tip gates (one trigger family): only a day where waving is the
# pattern — several waved AND at least half of the day's approval moments.
APPROVAL_WAVED_MIN_COUNT = 3
APPROVAL_WAVED_MIN_SHARE_PCT = 50
# Waiting on responses (P4): the same-session gap from the previous event to
# an assistant turn — no structural wait field exists in either log, so the
# wait is derived from mark timing. Bounded so an instant stream (below the
# minimum) and a walked-away silence (above the maximum) never count.
# Informational only, by decision: a KPI, never a dimension or a judgment.
WAIT_GAP_MIN_S = 2
WAIT_GAP_MAX_S = 600

# Focus *data* never reaches the wire (NFR-PRV-6). Note: tip_shown/tip_acted
# are NOT focus data — they are sanctioned closed engagement counters
# (FR-RPT-7, and FR-FOC-7 explicitly allows engagement counting).
# "interactive" and "branch" (P3) verified against every wire payload key
# before adding: no existing field name contains either substring.
# "quota"/"rate_limit"/"waiting" (P4, local-only decision) verified the same
# way 2026-07-05: no payload key contains any of them (capability values like
# "quota_billing_feed" are values, not field names, and never emitted).
# "waved"/"approval" (waved-through approvals, local-only forever) verified
# the same way 2026-07-05: no wire payload field name contains either.
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "focus",
    "streak",
    "burst",
    "nudge",
    "block",
    "refire",
    "marathon",
    "compaction",
    "interactive",
    "branch",
    "quota",
    "rate_limit",
    "waiting",
    "waved",
    "approval",
)


def interactive_marks(marks: list[MarkRow]) -> list[MarkRow]:
    """P3 behavioral gate: keep only human-driven (non-sidechain) marks.

    Behavioral derivations — focus metrics, tips, work-type mix, rhythm,
    per-day behavior scoring, refire/marathon groupings — read through this
    filter; cost/token accounting NEVER does (sidechain compute still
    counts). Rows predating the interactive column (legacy stores, short
    test rows) default to interactive."""
    return [
        mark
        for mark in marks
        if len(mark) <= MARK_INTERACTIVE or bool(mark[MARK_INTERACTIVE])
    ]


@dataclass(frozen=True, slots=True)
class FocusMetrics:
    longest_block_min: int
    max_concurrent_sessions: int
    switch_count: int
    longest_streak_min: int
    first_hour_utc: int | None
    burst_windows: int
    event_count: int
    reflex_replies: int = 0  # follow-ups within REFLEX_GAP_MAX_S of an answer
    assistant_followups: int = 0  # all same-session answer->follow-up pairs
    refire_replies: int = 0  # human replies within minutes of a failed run
    # Waved-through approvals: long-run summary/asks a human turn closed...
    approval_moments: int = 0
    # ...of which the reply was near-instant AND "go"-class short.
    waved_through: int = 0


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def compute_metrics(marks: list[MarkRow]) -> FocusMetrics:
    """FR-FOC-1 daily metrics from (session_key, ts_utc, ...) marks."""
    if not marks:
        return FocusMetrics(0, 0, 0, 0, None, 0, 0)
    events = [(session, _parse(ts)) for session, ts, *_rest in marks]
    events.sort(key=lambda item: (item[1], item[0]))
    times = [ts for _session, ts in events]

    # Longest single-session focus block (split at >30-min internal gaps).
    longest_block = 0
    by_session: dict[str, list[datetime]] = {}
    for session, ts in events:
        by_session.setdefault(session, []).append(ts)
    for session_times in by_session.values():
        block_start = session_times[0]
        previous = session_times[0]
        for ts in session_times[1:]:
            if (ts - previous).total_seconds() > SESSION_SPLIT_GAP_MIN * 60:
                longest_block = max(
                    longest_block, int((previous - block_start).total_seconds() // 60)
                )
                block_start = ts
            previous = ts
        longest_block = max(
            longest_block, int((previous - block_start).total_seconds() // 60)
        )

    # Max concurrent active sessions within a 15-minute activity window:
    # for each event time T, distinct sessions active in [T - window, T].
    # Sliding two-pointer keeps this linear; equal timestamps are grouped so
    # the count matches the per-event definition exactly.
    max_concurrent = 0
    window = ACTIVITY_WINDOW_MIN * 60
    active_counts: dict[str, int] = {}
    left = 0
    index = 0
    while index < len(events):
        group_end = index
        while group_end < len(events) and events[group_end][1] == events[index][1]:
            session = events[group_end][0]
            active_counts[session] = active_counts.get(session, 0) + 1
            group_end += 1
        while (events[index][1] - events[left][1]).total_seconds() > window:
            session = events[left][0]
            remaining = active_counts[session] - 1
            if remaining:
                active_counts[session] = remaining
            else:
                del active_counts[session]
            left += 1
        max_concurrent = max(max_concurrent, len(active_counts))
        index = group_end

    # Session switches: alternations between different sessions within 15 min.
    switches = 0
    for (prev_session, prev_ts), (session, ts) in itertools.pairwise(events):
        if session != prev_session and (ts - prev_ts).total_seconds() <= window:
            switches += 1

    # Longest cross-session no-break streak (chains broken by >=15-min gaps).
    longest_streak = 0
    chain_start = times[0]
    previous_ts = times[0]
    for ts in times[1:]:
        if (ts - previous_ts).total_seconds() >= STREAK_BREAK_GAP_MIN * 60:
            longest_streak = max(
                longest_streak, int((previous_ts - chain_start).total_seconds() // 60)
            )
            chain_start = ts
        previous_ts = ts
    longest_streak = max(
        longest_streak, int((previous_ts - chain_start).total_seconds() // 60)
    )

    # Interaction burst windows (>=6 events / 10 min), greedy non-overlapping.
    # The end pointer is monotone (times are sorted), keeping the scan linear.
    bursts = 0
    index = 0
    end = 0
    burst_window = BURST_WINDOW_MIN * 60
    while index < len(times):
        if end < index:
            end = index
        while (
            end < len(times)
            and (times[end] - times[index]).total_seconds() < burst_window
        ):
            end += 1
        if end - index >= BURST_MIN_EVENTS:
            bursts += 1
            index = end
        else:
            index += 1

    # Reflex replies: same-session answer -> follow-up pairs, split by how
    # fast the follow-up landed. Only pure text answers count: an assistant
    # turn that ran tools gets an *automated* follow-up (the tool result
    # rides a user-role message), which says nothing about human reading.
    reflex = 0
    followups = 0
    refire = 0
    approval_moments = 0
    waved_through = 0
    turns_by_session: dict[str, list[tuple[datetime, str, int, int, int]]] = {}
    for mark in marks:
        # Counters are read via the MARK_* index constants (store.py) — the
        # append-only mark contract means a bare literal or rest[-1] would
        # silently shadow the wrong column when the row grows. Short
        # legacy/test rows default missing counters to zero.
        failures_raw = mark[MARK_FAILURES] if len(mark) > MARK_FAILURES else 0
        short_raw = mark[MARK_SHORT_REPLY] if len(mark) > MARK_SHORT_REPLY else 0
        turns_by_session.setdefault(mark[0], []).append(
            (_parse(mark[1]), mark[2], mark[MARK_TOOL_CALLS], failures_raw, short_raw)
        )
    for turn_list in turns_by_session.values():
        turn_list.sort()
        last_failure: datetime | None = None
        if turn_list and turn_list[0][3] > 0:
            last_failure = turn_list[0][0]
        for earlier_turn, later_turn in itertools.pairwise(turn_list):
            prev_time, prev_kind, prev_tool_calls, _prev_failures, _s = earlier_turn
            turn_time, turn_kind, _turn_tool_calls, turn_failures, _t = later_turn
            is_human_reply = (
                prev_kind == "assistant_turn"
                and prev_tool_calls == 0
                and turn_kind != "assistant_turn"
                and (turn_time - prev_time).total_seconds()
                <= SESSION_SPLIT_GAP_MIN * 60
            )
            if is_human_reply:
                followups += 1
                if (turn_time - prev_time).total_seconds() <= REFLEX_GAP_MAX_S:
                    reflex += 1
                # Refire (P2): the human reply chased a recent failed run.
                if (
                    last_failure is not None
                    and (turn_time - last_failure).total_seconds()
                    <= REFIRE_WINDOW_MIN * 60
                ):
                    refire += 1
            if turn_failures > 0:
                last_failure = turn_time

        # Waved-through approvals: a long tooled stretch ending in an
        # untooled summary/ask opens an approval moment; only the NEXT human
        # turn closes it. Humanness is the same structural rule the reflex
        # metric uses, carried as state: a user turn whose most recent
        # assistant turn ran tools is an automated tool-result carrier
        # (agent auto-continuation), never a human reply — so it neither
        # closes a moment nor resets the work stretch.
        work_stretch = 0  # tooled assistant turns since the last human turn
        last_assistant_tooled = False
        pending_ask: datetime | None = None
        for turn_time, turn_kind, turn_tool_calls, _failures, turn_short in (
            turn_list
        ):
            if turn_kind == "assistant_turn":
                if turn_tool_calls > 0:
                    work_stretch += 1
                    last_assistant_tooled = True
                else:
                    last_assistant_tooled = False
                    if work_stretch >= APPROVAL_STRETCH_MIN_TURNS:
                        pending_ask = turn_time  # (re)anchor on the latest ask
                continue
            if last_assistant_tooled:
                continue  # tool-result carrier — not a human turn
            if pending_ask is not None:
                approval_moments += 1
                gap_s = (turn_time - pending_ask).total_seconds()
                if gap_s <= APPROVAL_FAST_MAX_S and turn_short:
                    waved_through += 1
                pending_ask = None
            work_stretch = 0

    return FocusMetrics(
        longest_block_min=longest_block,
        max_concurrent_sessions=max_concurrent,
        switch_count=switches,
        longest_streak_min=longest_streak,
        first_hour_utc=times[0].astimezone(UTC).hour,
        burst_windows=bursts,
        event_count=len(events),
        reflex_replies=reflex,
        assistant_followups=followups,
        refire_replies=refire,
        approval_moments=approval_moments,
        waved_through=waved_through,
    )


# ---- closed tip catalog (FR-FOC-3) --------------------------------------------

TIP_IDS: tuple[str, ...] = (
    "celebrate-longest-block",
    "protect-a-deep-work-block",
    "break-after-long-streak",
    "batch-parallel-sessions-to-one-task",
    "pause-between-bursts",
    "read-before-the-next-ask",
    "read-the-handoff",
)

# Fixed copy — a wording change here is a reviewed contract diff (NFR-QLT-3).
TIP_COPY: dict[str, tuple[str, str]] = {
    "celebrate-longest-block": (
        "Strong deep-work block",
        "Your longest uninterrupted block today ran past 90 minutes. That kind of "
        "stretch is where the hard problems give in - nicely done.",
    ),
    "protect-a-deep-work-block": (
        "Protect a deep-work block",
        "Today's work happened in many short stretches. Reserving one 45-minute "
        "block for a single task usually gets the tough thing done sooner.",
    ),
    "break-after-long-streak": (
        "A pause pays for itself",
        "You worked a long stretch without a 15-minute pause. Stepping away "
        "briefly tends to make the next hour noticeably sharper.",
    ),
    "batch-parallel-sessions-to-one-task": (
        "Batch parallel sessions",
        "Several sessions were active at the same time. Finishing one task before "
        "opening the next usually costs less attention than juggling.",
    ),
    "pause-between-bursts": (
        "Pause between bursts",
        "Interactions came in rapid bursts today. A short breather between bursts "
        "keeps the follow-ups deliberate instead of reactive.",
    ),
    "read-before-the-next-ask": (
        "Read it before the next ask",
        "Most follow-ups today landed within seconds of the previous answer. A "
        "short read-through first usually catches the one thing worth changing - "
        "and keeps your own judgment in the loop.",
    ),
    "read-the-handoff": (
        "Read the handoff first",
        "Several long agent runs today were approved within seconds of the "
        "summary landing. A one-minute read of the handoff usually finds the "
        "one thing worth changing - and keeps the final call yours.",
    ),
}

# Trigger thresholds (named constants).
CELEBRATE_BLOCK_MIN = 90
PROTECT_MAX_BLOCK_MIN = 45
PROTECT_MIN_EVENTS = 8
BATCH_MIN_CONCURRENT = 3
PAUSE_MIN_BURSTS = 3


def triggered_tips(metrics: FocusMetrics) -> list[str]:
    """Fixed-order trigger predicates; the positive tip leads (FR-FOC-2)."""
    tips: list[str] = []
    if metrics.longest_block_min >= CELEBRATE_BLOCK_MIN:
        tips.append("celebrate-longest-block")
    if (
        metrics.event_count >= PROTECT_MIN_EVENTS
        and metrics.longest_block_min < PROTECT_MAX_BLOCK_MIN
    ):
        tips.append("protect-a-deep-work-block")
    if metrics.longest_streak_min >= NUDGE_STREAK_MIN:
        tips.append("break-after-long-streak")
    if metrics.max_concurrent_sessions >= BATCH_MIN_CONCURRENT:
        tips.append("batch-parallel-sessions-to-one-task")
    if metrics.burst_windows >= PAUSE_MIN_BURSTS:
        tips.append("pause-between-bursts")
    if (
        metrics.assistant_followups >= REFLEX_MIN_FOLLOWUPS
        and metrics.reflex_replies * 100
        >= metrics.assistant_followups * REFLEX_SHARE_MIN_PCT
    ):
        tips.append("read-before-the-next-ask")
    if (
        metrics.waved_through >= APPROVAL_WAVED_MIN_COUNT
        and metrics.waved_through * 100
        >= metrics.approval_moments * APPROVAL_WAVED_MIN_SHARE_PCT
    ):
        tips.append("read-the-handoff")
    return tips


def visible_tips(triggered: list[str], dismissed: set[str]) -> list[str]:
    """The single dismissal-filtered visible list (FR-FOC-6). Capped at two
    observation tips after the positive metric (FR-FOC-2)."""
    visible = [tip for tip in triggered if tip not in dismissed]
    return visible[:MAX_TIPS_SHOWN]


# ---- focus-block timer counters (FR-FOC-9) ------------------------------------

BLOCK_EVENTS: tuple[str, ...] = (
    "block-started",
    "block-completed",
    "break-started",
    "break-completed",
)

_BLOCK_META_PREFIX = "focus_blocks:"


def record_block_event(store: object, day: str, event: str) -> bool:
    """Persist a timer lifecycle event (invoked by the shell through the CLI,
    C-4). Local counters only — never on the wire (NFR-PRV-6)."""
    import json

    if event not in BLOCK_EVENTS:
        return False
    key = f"{_BLOCK_META_PREFIX}{day}"
    raw = store.meta_get(key)  # type: ignore[attr-defined]
    counters: dict[str, int] = dict.fromkeys(BLOCK_EVENTS, 0)
    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, dict):
                for name in BLOCK_EVENTS:
                    value = stored.get(name)
                    if isinstance(value, int) and value >= 0:
                        counters[name] = value
        except ValueError:
            pass
    counters[event] += 1
    store.meta_set(key, json.dumps(counters, sort_keys=True))  # type: ignore[attr-defined]
    return True


def block_counters(store: object, day: str) -> dict[str, int]:
    import json

    raw = store.meta_get(f"{_BLOCK_META_PREFIX}{day}")  # type: ignore[attr-defined]
    counters = dict.fromkeys(BLOCK_EVENTS, 0)
    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, dict):
                for name in BLOCK_EVENTS:
                    value = stored.get(name)
                    if isinstance(value, int) and value >= 0:
                        counters[name] = value
        except ValueError:
            pass
    return counters


# ---- working rhythm (behavioral observations, local only) --------------------

RHYTHM_WINDOW_DAYS = 28  # named constant
DEEP_BLOCK_MIN = 45


@dataclass(frozen=True, slots=True)
class RhythmStats:
    """How the work is paced, from the local activity timeline. Behavioral
    observations only (FR-FOC-8) — and, like every focus surface, this never
    leaves the machine (NFR-PRV-6)."""

    window_days: int
    events_total: int
    outside_preferred_hours_pct: int | None
    quiet_hours_activity_pct: int | None
    off_schedule_day_pct: int | None
    long_streak_days: int  # days with a 120+ min stretch and no 15-min pause
    deep_block_days: int  # days with at least one 45+ min uninterrupted block
    active_days: int
    # Turn velocity (P0, context only — never a flag): events per distinct
    # active hour, in tenths (36 -> 3.6/h).
    turns_per_active_hour_tenths: int = 0
    # Environment cardinality (P3, descriptive only — NEVER a finding, per
    # the peripheral-criteria caveat): distinct non-empty cwd/branch identity
    # hashes across the window's INTERACTIVE marks. Counts of hashes; the
    # project/branch names themselves are never stored.
    distinct_projects: int = 0
    distinct_branches: int = 0
    # Accumulated response wait (P4, informational only): same-session gaps
    # ending on an assistant turn, bounded by the WAIT_GAP_* constants.
    waiting_minutes: int = 0


def rhythm_stats(
    marks_by_day: dict[str, list[MarkRow]],
    schedule: ScheduleProfile | None = None,
    window_days: int = RHYTHM_WINDOW_DAYS,
) -> RhythmStats:
    from practicegraph.report.format import percent

    events_total = 0
    schedule = schedule or compatibility_utc_schedule()
    outside_preferred = 0
    quiet = 0
    off_schedule = 0
    long_streak_days = 0
    deep_block_days = 0
    waiting_seconds = 0
    active_hours: set[tuple[str, int]] = set()
    projects: set[str] = set()
    branches: set[str] = set()
    for marks in marks_by_day.values():
        turns_by_session: dict[str, list[tuple[datetime, str]]] = {}
        for mark in marks:
            events_total += 1
            ts = _parse(mark[1])
            context = classify_time(ts, schedule)
            active_hours.add((context.local_day.isoformat(), context.local_hour))
            if schedule.confirmed:
                if not context.inside_preferred_hours:
                    outside_preferred += 1
                if context.inside_quiet_hours:
                    quiet += 1
                if not context.on_preferred_day:
                    off_schedule += 1
            turns_by_session.setdefault(mark[0], []).append((ts, mark[2]))
        # P4 response wait: same-session previous-event -> assistant-turn gaps
        # inside the named bounds, summed across the window. Derived timing
        # only — informational, never scored (the marks arrive through the
        # callers' interactive gate like every rhythm input).
        for turn_list in turns_by_session.values():
            turn_list.sort()
            for (prev_ts, _prev_kind), (turn_ts, turn_kind) in itertools.pairwise(
                turn_list
            ):
                gap_s = (turn_ts - prev_ts).total_seconds()
                if (
                    turn_kind == "assistant_turn"
                    and WAIT_GAP_MIN_S <= gap_s <= WAIT_GAP_MAX_S
                ):
                    waiting_seconds += int(gap_s)
        # P3 cardinality gates on interactive marks by construction — a
        # sidechain worktree is not a project *you* touched. Legacy/short
        # rows carry no hashes and count nothing.
        for mark in interactive_marks(marks):
            if len(mark) > MARK_CWD_HASH and mark[MARK_CWD_HASH]:
                projects.add(mark[MARK_CWD_HASH])
            if len(mark) > MARK_BRANCH_HASH and mark[MARK_BRANCH_HASH]:
                branches.add(mark[MARK_BRANCH_HASH])
        metrics = compute_metrics(marks)
        if metrics.longest_streak_min >= NUDGE_STREAK_MIN:
            long_streak_days += 1
        if metrics.longest_block_min >= DEEP_BLOCK_MIN:
            deep_block_days += 1
    return RhythmStats(
        window_days=window_days,
        events_total=events_total,
        outside_preferred_hours_pct=(
            percent(outside_preferred, events_total) if schedule.confirmed else None
        ),
        quiet_hours_activity_pct=(
            percent(quiet, events_total) if schedule.confirmed else None
        ),
        off_schedule_day_pct=(
            percent(off_schedule, events_total) if schedule.confirmed else None
        ),
        long_streak_days=long_streak_days,
        deep_block_days=deep_block_days,
        active_days=len(marks_by_day),
        turns_per_active_hour_tenths=(
            events_total * 10 // max(1, len(active_hours))
        ),
        distinct_projects=len(projects),
        distinct_branches=len(branches),
        waiting_minutes=waiting_seconds // 60,
    )


# ---- quiet-hours drift (descriptive-only, vs your own baseline) --------------

QUIET_HOURS_DRIFT_MIN_DELTA_PTS = 10
QUIET_HOURS_DRIFT_MIN_CURRENT_PCT = 15
QUIET_HOURS_DRIFT_MIN_EVENTS = 30


def _quiet_hours_share(
    marks_by_day: dict[str, list[MarkRow]],
    schedule: ScheduleProfile,
) -> tuple[int, int]:
    quiet = 0
    total = 0
    for marks in marks_by_day.values():
        for _session, ts, *_rest in marks:
            total += 1
            if classify_time(_parse(ts), schedule).inside_quiet_hours:
                quiet += 1
    return quiet, total


def quiet_hours_drift(
    current_by_day: dict[str, list[MarkRow]],
    baseline_by_day: dict[str, list[MarkRow]],
    schedule: ScheduleProfile | None = None,
) -> int | None:
    """Percentage-point rise in quiet-hours activity versus your own baseline
    window. None unless both windows carry enough events AND the rise clears
    the named gates — descriptive-only by decision: this can inform, never
    alarm (the finding it feeds stays INFO severity)."""
    from practicegraph.report.format import percent

    schedule = schedule or compatibility_utc_schedule()
    if not schedule.confirmed:
        return None
    quiet_now, total_now = _quiet_hours_share(current_by_day, schedule)
    quiet_base, total_base = _quiet_hours_share(baseline_by_day, schedule)
    if total_now < QUIET_HOURS_DRIFT_MIN_EVENTS:
        return None
    if total_base < QUIET_HOURS_DRIFT_MIN_EVENTS:
        return None
    current_pct = percent(quiet_now, total_now)
    delta = current_pct - percent(quiet_base, total_base)
    if current_pct < QUIET_HOURS_DRIFT_MIN_CURRENT_PCT:
        return None
    if delta < QUIET_HOURS_DRIFT_MIN_DELTA_PTS:
        return None
    return delta


# ---- on-time nudge (FR-FOC-4) ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class OngoingStreak:
    started_at: str  # ISO — the dedupe key so one streak never re-fires
    minutes: int


def ongoing_streak(
    marks: list[MarkRow], now: datetime
) -> OngoingStreak | None:
    """The live no-break streak ending at the latest event, if still alive.
    Accumulates across scheduler cycles by construction: it is derived from
    the persisted timeline, not from a single cycle's window."""
    if not marks:
        return None
    times = sorted(_parse(ts) for _session, ts, *_rest in marks)
    last = times[-1]
    if (now - last).total_seconds() > STREAK_BREAK_GAP_MIN * 60:
        return None  # streak already broken by a pause
    chain_start = times[-1]
    previous = times[-1]
    for ts in reversed(times[:-1]):
        if (previous - ts).total_seconds() >= STREAK_BREAK_GAP_MIN * 60:
            break
        chain_start = ts
        previous = ts
    minutes = int((last - chain_start).total_seconds() // 60)
    return OngoingStreak(started_at=chain_start.isoformat(), minutes=minutes)
