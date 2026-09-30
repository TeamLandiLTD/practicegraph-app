"""Focus coaching (FR-FOC): metrics, tips, dismissal, and the break nudge."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import build_fixture_history
from practicegraph.analysis.focus import (
    MAX_TIPS_SHOWN,
    TIP_COPY,
    TIP_IDS,
    compute_metrics,
    ongoing_streak,
    triggered_tips,
    visible_tips,
)
from practicegraph.analysis.schedule import compatibility_utc_schedule
from practicegraph.privacy import lexicon_violations
from practicegraph.store import Store

BASE = datetime(2026, 7, 2, 9, 0, tzinfo=UTC)


def _mark(
    session: str, minute_offset: int
) -> tuple[str, str, str, int, int, int, int, int]:
    ts = (BASE + timedelta(minutes=minute_offset)).isoformat()
    return (session, ts, "assistant_turn", 0, 0, 0, 0, 0)


def test_tip_catalog_is_pinned_and_lexicon_clean() -> None:
    assert TIP_IDS == (
        "celebrate-longest-block",
        "protect-a-deep-work-block",
        "break-after-long-streak",
        "batch-parallel-sessions-to-one-task",
        "pause-between-bursts",
        "read-before-the-next-ask",
        "read-the-handoff",
    )
    for title, body in TIP_COPY.values():
        assert lexicon_violations(title) == []
        assert lexicon_violations(body) == []


def test_reflex_replies_and_the_read_first_tip() -> None:
    """Follow-ups within seconds of an answer are the structural signal of
    accepting output without a read; slow, considered replies are not."""
    from practicegraph.analysis.focus import REFLEX_MIN_FOLLOWUPS

    def _pair(minute: int, gap_s: int) -> list[tuple[str, str, str, int, int, int]]:
        answer = (BASE + timedelta(minutes=minute)).isoformat()
        reply = (BASE + timedelta(minutes=minute, seconds=gap_s)).isoformat()
        return [
            ("s1", answer, "assistant_turn", 0, 0, 0),
            ("s1", reply, "user_turn", 0, 0, 0),
        ]

    fast: list[tuple[str, str, str, int, int, int]] = []
    for index in range(REFLEX_MIN_FOLLOWUPS + 2):
        fast.extend(_pair(index * 2, gap_s=5))
    metrics = compute_metrics(fast)
    assert metrics.assistant_followups == REFLEX_MIN_FOLLOWUPS + 2
    assert metrics.reflex_replies == REFLEX_MIN_FOLLOWUPS + 2
    assert "read-before-the-next-ask" in triggered_tips(metrics)

    slow: list[tuple[str, str, str, int, int, int]] = []
    for index in range(REFLEX_MIN_FOLLOWUPS + 2):
        slow.extend(_pair(index * 10, gap_s=300))  # five-minute reads
    considered = compute_metrics(slow)
    assert considered.assistant_followups == REFLEX_MIN_FOLLOWUPS + 2
    assert considered.reflex_replies == 0
    assert "read-before-the-next-ask" not in triggered_tips(considered)

    # Agentic loops don't count: an assistant turn that ran tools gets an
    # automated tool-result follow-up, which says nothing about reading.
    agentic = [
        mark if mark[2] == "user_turn" else (*mark[:3], 4, *mark[4:])
        for mark in fast
    ]
    looped = compute_metrics(agentic)
    assert looped.assistant_followups == 0
    assert looped.reflex_replies == 0
    assert "read-before-the-next-ask" not in triggered_tips(looped)


def test_refire_counts_human_replies_chasing_failures() -> None:
    """P2: a refire is a HUMAN reply within minutes of the session's most
    recent failed run — agent auto-continuation never counts (calibrated on
    real history; the finding stays INFO forever by decision)."""
    from datetime import timedelta as td

    from practicegraph.analysis.focus import REFIRE_WINDOW_MIN

    def mark(minute: int, second: int, kind: str, failures: int):
        ts = (BASE + td(minutes=minute, seconds=second)).isoformat()
        # command_failures stays at position 6; compactions appended last.
        return ("s1", ts, kind, 0, 0, 0, failures, 0)

    chased = [
        mark(0, 0, "user_turn", 1),        # a failed run lands here
        mark(0, 30, "assistant_turn", 0),  # untooled answer
        mark(1, 0, "user_turn", 0),        # human reply 1 min after failure
    ]
    assert compute_metrics(chased).refire_replies == 1

    # The same reply far outside the window is a considered follow-up.
    calm = [
        mark(0, 0, "user_turn", 1),
        mark(REFIRE_WINDOW_MIN + 5, 0, "assistant_turn", 0),
        mark(REFIRE_WINDOW_MIN + 6, 0, "user_turn", 0),
    ]
    assert compute_metrics(calm).refire_replies == 0

    # No failure, no refire — even for instant replies.
    quick = [
        mark(0, 0, "assistant_turn", 0),
        mark(0, 1, "user_turn", 0),
    ]
    assert compute_metrics(quick).refire_replies == 0


def _approval_marks(
    session: str,
    start_min: int,
    reply_gap_s: int,
    short: int,
    stretch: int,
    close: bool = True,
) -> list[tuple]:
    """One delegated run as marks: `stretch` tooled assistant turns (each
    trailed by its automated tool-result user mark), an untooled summary,
    and — when `close` — the human reply `reply_gap_s` after it."""

    def mark(minute: int, second: int, kind: str, tool_calls: int, s: int = 0):
        ts = (BASE + timedelta(minutes=minute, seconds=second)).isoformat()
        return (session, ts, kind, tool_calls, 0, 0, 0, 0, 1, "", "", s)

    marks = []
    for index in range(stretch):
        marks.append(mark(start_min + index, 0, "assistant_turn", 2))
        marks.append(mark(start_min + index, 30, "user_turn", 0))  # tool result
    marks.append(mark(start_min + stretch, 0, "assistant_turn", 0))  # the ask
    if close:
        marks.append(mark(start_min + stretch, reply_gap_s, "user_turn", 0, short))
    return marks


def test_waved_through_approvals_walk() -> None:
    """The automation-bias signature: a long tooled stretch ends in an
    untooled ask; the next HUMAN turn closes the moment, and only a fast
    "go"-class reply counts as waved. Calibrated constants (focus.py)."""
    from practicegraph.analysis.focus import (
        APPROVAL_FAST_MAX_S,
        APPROVAL_STRETCH_MIN_TURNS,
    )

    stretch = APPROVAL_STRETCH_MIN_TURNS
    waved = compute_metrics(_approval_marks("s1", 0, 3, short=1, stretch=stretch))
    assert waved.approval_moments == 1
    assert waved.waved_through == 1

    # The same 3-second close with a real (long) reply is fast but read:
    # the short_reply flag is what separates approval from review.
    considered = compute_metrics(
        _approval_marks("s1", 0, 3, short=0, stretch=stretch)
    )
    assert considered.approval_moments == 1
    assert considered.waved_through == 0

    # A short reply after a real read (gap over the bound) is considered too;
    # the bound itself is inclusive.
    slow = compute_metrics(
        _approval_marks("s1", 0, APPROVAL_FAST_MAX_S + 1, short=1, stretch=stretch)
    )
    assert slow.approval_moments == 1
    assert slow.waved_through == 0
    at_bound = compute_metrics(
        _approval_marks("s1", 0, APPROVAL_FAST_MAX_S, short=1, stretch=stretch)
    )
    assert at_bound.waved_through == 1

    # Below the stretch gate no moment ever opens — short interactive
    # exchanges are not approvals of a long delegated run.
    short_run = compute_metrics(
        _approval_marks("s1", 0, 3, short=1, stretch=stretch - 1)
    )
    assert short_run.approval_moments == 0
    assert short_run.waved_through == 0


def test_approval_moments_close_on_human_turns_only() -> None:
    """Agent auto-continuation never counts: tool-result user marks (they
    follow tooled assistant turns) neither close a moment nor reset the
    stretch — an unanswered ask stays uncounted."""
    from practicegraph.analysis.focus import APPROVAL_STRETCH_MIN_TURNS

    stretch = APPROVAL_STRETCH_MIN_TURNS

    def mark(session: str, minute: int, second: int, kind: str,
             tool_calls: int, s: int = 0):
        ts = (BASE + timedelta(minutes=minute, seconds=second)).isoformat()
        return (session, ts, kind, tool_calls, 0, 0, 0, 0, 1, "", "", s)

    # The ask is never answered by a human: zero moments.
    unanswered = compute_metrics(
        _approval_marks("s1", 0, 0, short=0, stretch=stretch, close=False)
    )
    assert unanswered.approval_moments == 0

    # The agent continues after the ask (tooled turn + its tool result), the
    # human replies "go" later: ONE moment, anchored at the latest ask.
    marks = _approval_marks("s1", 0, 0, short=0, stretch=stretch, close=False)
    resumed_min = stretch + 1
    marks.append(mark("s1", resumed_min, 0, "assistant_turn", 2))
    marks.append(mark("s1", resumed_min, 10, "user_turn", 0))  # tool result
    marks.append(mark("s1", resumed_min, 20, "assistant_turn", 0))  # new ask
    marks.append(mark("s1", resumed_min, 25, "user_turn", 0, 1))  # human "go"
    resumed = compute_metrics(marks)
    assert resumed.approval_moments == 1
    assert resumed.waved_through == 1  # 5s after the latest ask

    # Legacy short rows (no short_reply column) default to not-short: the
    # moment still closes, waved stays 0 (fail-open, append-arity guard).
    legacy = [m[:8] for m in _approval_marks("s1", 0, 3, short=1, stretch=stretch)]
    legacy_metrics = compute_metrics(legacy)
    assert legacy_metrics.approval_moments == 1
    assert legacy_metrics.waved_through == 0


def test_read_the_handoff_tip_triggers_on_the_waved_pattern() -> None:
    """One trigger family with the finding: several waved AND at least half
    of the day's approval moments (calibrated gates)."""
    from practicegraph.analysis.focus import (
        APPROVAL_STRETCH_MIN_TURNS,
        APPROVAL_WAVED_MIN_COUNT,
    )

    stretch = APPROVAL_STRETCH_MIN_TURNS
    marks: list[tuple] = []
    for index in range(APPROVAL_WAVED_MIN_COUNT):
        marks.extend(
            _approval_marks(f"s{index}", 0, 3, short=1, stretch=stretch)
        )
    pattern = compute_metrics(marks)
    assert pattern.waved_through == APPROVAL_WAVED_MIN_COUNT
    assert "read-the-handoff" in triggered_tips(pattern)

    # One wave among many considered approvals is not a pattern (share gate).
    mixed: list[tuple] = [
        *_approval_marks("s0", 0, 3, short=1, stretch=stretch),
        *_approval_marks("s1", 0, 300, short=0, stretch=stretch),
        *_approval_marks("s2", 0, 300, short=0, stretch=stretch),
    ]
    balanced = compute_metrics(mixed)
    assert balanced.approval_moments == 3
    assert balanced.waved_through == 1
    assert "read-the-handoff" not in triggered_tips(balanced)


def test_metrics_on_a_deep_work_day() -> None:
    # One session, events every 10 minutes for two hours: a 120-minute block.
    marks = [_mark("s1", offset) for offset in range(0, 121, 10)]
    metrics = compute_metrics(marks)
    assert metrics.longest_block_min == 120
    assert metrics.longest_streak_min == 120
    assert metrics.max_concurrent_sessions == 1
    assert metrics.switch_count == 0
    assert metrics.first_hour_utc == 9
    tips = triggered_tips(metrics)
    assert tips[0] == "celebrate-longest-block"  # positive tip leads (FR-FOC-2)
    assert "break-after-long-streak" in tips


def test_metrics_on_a_fragmented_day() -> None:
    # Three parallel sessions alternating within minutes, in bursts.
    marks = []
    for burst_start in (0, 60, 120):
        for offset in range(6):
            session = f"s{offset % 3 + 1}"
            marks.append(_mark(session, burst_start + offset))
    metrics = compute_metrics(marks)
    assert metrics.max_concurrent_sessions == 3
    assert metrics.switch_count >= 10
    assert metrics.burst_windows == 3
    assert metrics.longest_block_min < 45
    tips = triggered_tips(metrics)
    assert "protect-a-deep-work-block" in tips
    assert "batch-parallel-sessions-to-one-task" in tips
    assert "pause-between-bursts" in tips
    # FR-FOC-2: at most two observation tips ever reach a surface.
    assert len(visible_tips(tips, dismissed=set())) == MAX_TIPS_SHOWN


def test_session_gap_splits_focus_blocks() -> None:
    marks = [_mark("s1", 0), _mark("s1", 20), _mark("s1", 60), _mark("s1", 70)]
    metrics = compute_metrics(marks)  # 40-min silence splits the session
    assert metrics.longest_block_min == 20


def test_dismissal_is_permanent_and_single_sourced(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.migrate()
    triggered = ["celebrate-longest-block", "protect-a-deep-work-block"]
    assert visible_tips(triggered, store.dismissed_tips()) == triggered
    store.dismiss_tip("protect-a-deep-work-block", BASE)
    # FR-FOC-6: the dismissal filters the single visible list for all surfaces.
    assert visible_tips(triggered, store.dismissed_tips()) == ["celebrate-longest-block"]


def test_ongoing_streak_accumulates_across_cycles() -> None:
    marks = [_mark("s1", offset) for offset in range(0, 121, 10)]
    now_alive = BASE + timedelta(minutes=125)
    streak = ongoing_streak(marks, now_alive)
    assert streak is not None
    assert streak.minutes == 120
    assert streak.started_at == BASE.isoformat()
    # After a 15-minute pause the streak is over — no nudge.
    now_paused = BASE + timedelta(minutes=140)
    assert ongoing_streak(marks, now_paused) is None


def test_rhythm_stats_are_behavioral_observations() -> None:
    """Late-night/weekend shares and pause discipline (the 'daily AI use vs
    focus' watchpoints), computed locally with named constants."""
    from practicegraph.analysis.focus import rhythm_stats

    weekday_marks = [_mark("s1", offset) for offset in range(0, 121, 10)]  # Thu
    late_marks = [
        ("s2", (BASE.replace(hour=23) + timedelta(minutes=m)).isoformat(),
         "assistant_turn", 0, 0, 0)
        for m in range(0, 30, 10)
    ]
    weekend_marks = [
        ("s3", "2026-07-04T10:00:00+00:00", "assistant_turn", 0, 0, 0),  # Saturday
        ("s3", "2026-07-04T10:05:00+00:00", "assistant_turn", 0, 0, 0),
    ]
    stats = rhythm_stats(
        {
            "2026-07-02": weekday_marks + late_marks,
            "2026-07-04": weekend_marks,
        }
    )
    assert stats.active_days == 2
    assert stats.events_total == 18
    assert stats.quiet_hours_activity_pct == 17  # 3 of 18 events after 22:00 UTC
    assert stats.off_schedule_day_pct == 11  # 2 of 18 events on a weekend day
    assert stats.long_streak_days == 1  # the 120-minute weekday stretch
    assert stats.deep_block_days == 1
    # P0 velocity (context only): 18 events over 5 distinct active hours.
    assert stats.turns_per_active_hour_tenths == 36
    # P4 waiting time: every 10-minute same-session cadence gap sits exactly
    # at the inclusive WAIT_GAP_MAX_S bound (14 gaps x 600s) and the weekend
    # pair adds 300s -> 8700s // 60 = 145 minutes.
    assert stats.waiting_minutes == 145


def test_rhythm_uses_confirmed_local_schedule_and_withholds_without_it() -> None:
    from practicegraph.analysis.focus import rhythm_stats

    sofia = replace(
        compatibility_utc_schedule(),
        timezone_name="Europe/Sofia",
        quiet_start="22:00",
        quiet_end="07:00",
    )
    marks = {
        "2026-07-06": [
            ("s1", "2026-07-06T07:30:00+00:00", "assistant_turn", 0, 0, 0),
            ("s1", "2026-07-06T20:30:00+00:00", "assistant_turn", 0, 0, 0),
        ]
    }
    stats = rhythm_stats(marks, sofia)
    assert stats.outside_preferred_hours_pct == 50
    assert stats.quiet_hours_activity_pct == 50
    assert stats.off_schedule_day_pct == 0

    unconfirmed = rhythm_stats(marks, replace(sofia, version=0, confirmed=False))
    assert unconfirmed.outside_preferred_hours_pct is None
    assert unconfirmed.quiet_hours_activity_pct is None
    assert unconfirmed.off_schedule_day_pct is None


def test_rhythm_waiting_minutes_sums_bounded_response_gaps() -> None:
    """P4: waiting time is the same-session previous-event -> assistant-turn
    gap inside the named bounds. Instant streams (below the minimum) and
    walked-away silences (above the maximum) never count; a gap ending on a
    user turn is your reading time, not a wait; sessions never cross-pair."""
    from practicegraph.analysis.focus import (
        WAIT_GAP_MAX_S,
        WAIT_GAP_MIN_S,
        rhythm_stats,
    )

    def mark(
        session: str, minute: int, second: int, kind: str
    ) -> tuple[str, str, str, int, int, int, int, int]:
        ts = (BASE + timedelta(minutes=minute, seconds=second)).isoformat()
        return (session, ts, kind, 0, 0, 0, 0, 0)

    marks = [
        # Three 60-second waits for an answer -> exactly 3 minutes.
        mark("s1", 0, 0, "user_turn"),
        mark("s1", 1, 0, "assistant_turn"),
        mark("s1", 5, 0, "user_turn"),
        mark("s1", 6, 0, "assistant_turn"),
        mark("s1", 10, 0, "user_turn"),
        mark("s1", 11, 0, "assistant_turn"),
        # Below WAIT_GAP_MIN_S: an instant stream, not a wait.
        mark("s1", 20, 0, "user_turn"),
        mark("s1", 20, WAIT_GAP_MIN_S - 1, "assistant_turn"),
        # Above WAIT_GAP_MAX_S: you walked away; that silence is not a wait.
        mark("s1", 30, 0, "user_turn"),
        mark("s1", 30, WAIT_GAP_MAX_S + 1, "assistant_turn"),
        # Another session's answer 60s after an s1 event: no first event of
        # its own, so nothing to pair with — sessions never cross.
        mark("s2", 31, 0, "assistant_turn"),
    ]
    stats = rhythm_stats({"2026-07-02": marks})
    assert stats.waiting_minutes == 3


def test_late_night_drift_is_gated_and_descriptive() -> None:
    """P0: drift fires only vs your OWN baseline, with enough signal in both
    windows — and by decision it can inform, never alarm."""
    from practicegraph.analysis.focus import (
        QUIET_HOURS_DRIFT_MIN_EVENTS,
        quiet_hours_drift,
    )

    def window(day: str, hour: int, events: int):
        return {
            day: [
                ("s1", f"{day}T{hour:02d}:{m:02d}:00+00:00", "assistant_turn",
                 0, 0, 0)
                for m in range(events)
            ]
        }

    quiet_baseline = window("2026-06-01", 10, QUIET_HOURS_DRIFT_MIN_EVENTS)
    late_current = window("2026-07-01", 23, QUIET_HOURS_DRIFT_MIN_EVENTS)
    # 100% late now vs 0% baseline: a 100-point rise.
    assert quiet_hours_drift(late_current, quiet_baseline) == 100
    # Same pattern as baseline -> no drift.
    late_baseline = window("2026-06-01", 23, QUIET_HOURS_DRIFT_MIN_EVENTS)
    assert quiet_hours_drift(late_current, late_baseline) is None
    # Too little signal in either window -> None, never a guess.
    thin = window("2026-07-01", 23, 5)
    assert quiet_hours_drift(thin, quiet_baseline) is None
    assert quiet_hours_drift(late_current, window("2026-06-01", 10, 5)) is None


def test_block_event_recording_roundtrip(tmp_path: Path) -> None:
    """FR-FOC-9: timer lifecycle counters, recorded via the CLI seam, local
    only, visible in the daily report."""
    from practicegraph.analysis.focus import block_counters, record_block_event

    store = Store(tmp_path / "state.db")
    store.migrate()
    assert record_block_event(store, "2026-07-03", "block-started")
    assert record_block_event(store, "2026-07-03", "block-completed")
    assert record_block_event(store, "2026-07-03", "break-completed")
    assert record_block_event(store, "2026-07-03", "block-started")
    assert not record_block_event(store, "2026-07-03", "not-an-event")
    counters = block_counters(store, "2026-07-03")
    assert counters["block-started"] == 2
    assert counters["block-completed"] == 1
    assert counters["break-completed"] == 1
    assert block_counters(store, "2026-07-04")["block-started"] == 0


def test_fixture_day_focus_metrics_gate_on_interactive_marks(tmp_path: Path) -> None:
    """P3: behavioral metrics read only human-driven marks. The sidechain
    fixture interleaves with the 09:12-09:16 main session — unfiltered it
    fabricates switches and concurrency; the gate removes exactly that."""
    from practicegraph.analysis.focus import interactive_marks

    store, _health = build_fixture_history(tmp_path)
    all_marks = store.marks_for_day("2026-07-02")
    gated = interactive_marks(all_marks)

    metrics = compute_metrics(gated)
    assert metrics.event_count == 16
    assert metrics.longest_block_min == 4  # 09:12 -> 09:16
    assert metrics.burst_windows == 1  # ten events inside ten minutes
    assert metrics.switch_count == 0  # one session at a time, humanly
    assert metrics.max_concurrent_sessions == 1

    unfiltered = compute_metrics(all_marks)
    assert unfiltered.event_count == 20  # + the 4 sidechain marks
    assert unfiltered.switch_count == 8  # agent interleaving looked like churn
    assert unfiltered.max_concurrent_sessions == 2


def test_interactive_marks_defaults_legacy_rows_to_interactive() -> None:
    """Rows predating the interactive column (v5 stores, short test rows)
    stay behavioral inputs; only an explicit 0 filters (P3 fail-open)."""
    from practicegraph.analysis.focus import interactive_marks

    legacy = _mark("s1", 0)  # 8-tuple, no interactive column
    ts = (BASE + timedelta(minutes=1)).isoformat()
    human = ("s1", ts, "assistant_turn", 0, 0, 0, 0, 0, 1, "a" * 16, "b" * 16)
    sidechain = ("s2", ts, "assistant_turn", 0, 0, 0, 0, 0, 0, "c" * 16, "d" * 16)
    assert interactive_marks([legacy, human, sidechain]) == [legacy, human]


def test_rhythm_env_cardinality_counts_interactive_marks_only() -> None:
    """P3: distinct projects/branches are counted over identity hashes of
    INTERACTIVE marks only — descriptive context, never a finding; sidechain
    worktrees and legacy hash-less rows add nothing."""
    from practicegraph.analysis.focus import rhythm_stats

    def mark(session: str, minute: int, interactive: int, cwd: str, branch: str):
        ts = (BASE + timedelta(minutes=minute)).isoformat()
        return (session, ts, "assistant_turn", 0, 0, 0, 0, 0,
                interactive, cwd, branch)

    day_marks = [
        mark("s1", 0, 1, "a" * 16, "b" * 16),
        mark("s1", 10, 1, "a" * 16, "b" * 16),  # repeats never double-count
        mark("s1", 20, 1, "e" * 16, "f" * 16),  # a second project + branch
        mark("s2", 30, 0, "c" * 16, "d" * 16),  # sidechain: excluded
        _mark("s3", 40),  # legacy row without hashes: nothing to count
    ]
    stats = rhythm_stats({"2026-07-02": day_marks})
    assert stats.distinct_projects == 2
    assert stats.distinct_branches == 2
    # The pre-P3 fields keep their unfiltered semantics (callers gate).
    assert stats.events_total == 5


def test_fixture_rhythm_cardinality_is_one_project_one_branch(tmp_path: Path) -> None:
    """The fixture pack touches one project on one branch interactively; the
    sidechain worktree/branch stays out of the count (P3 pin)."""
    from practicegraph.analysis.focus import rhythm_stats
    from practicegraph.store import MARK_INTERACTIVE
    from practicegraph.store import MarkRow as MarkRowT

    store, _health = build_fixture_history(tmp_path)
    marks_by_day: dict[str, list[MarkRowT]] = {}
    for row in store.marks_between("2026-07-02", "2026-07-02"):
        if not row[1:][MARK_INTERACTIVE]:
            continue
        marks_by_day.setdefault(row[0], []).append(row[1:])
    stats = rhythm_stats(marks_by_day)
    assert stats.distinct_projects == 1
    assert stats.distinct_branches == 1
    # Even fed unfiltered marks, rhythm_stats self-gates the cardinality on
    # the interactive flag — the sidechain worktree/branch never inflates it.
    unfiltered = {
        "2026-07-02": [
            row[1:] for row in store.marks_between("2026-07-02", "2026-07-02")
        ]
    }
    assert rhythm_stats(unfiltered).distinct_projects == 1
    assert rhythm_stats(unfiltered).distinct_branches == 1
