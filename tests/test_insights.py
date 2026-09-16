"""Insight analysis (FR-ANL-2..6) and suggestion lifecycle (FR-RPT-8)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import build_fixture_history, build_fixture_snapshot
from practicegraph.analysis.aggregate import DailySnapshot, UsageRow
from practicegraph.analysis.insights import (
    FINDING_COPY,
    FINDING_IDS,
    SUGGESTION_COPY,
    SUGGESTION_IDS,
    MaturityLevel,
    Severity,
    WorkType,
    derive_suggestions,
    detect,
    dismiss_suggestion,
    maturity_signals,
    spend_pace,
    visible_suggestions,
    work_type_mix,
)
from practicegraph.events import TokenCounts
from practicegraph.history import snapshot_for_day
from practicegraph.privacy import lexicon_violations
from practicegraph.store import Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _snapshot(rows: tuple[UsageRow, ...], **overrides: int) -> DailySnapshot:
    defaults = {
        "total_cost_micro_usd": sum(r.cost_micro_usd for r in rows),
        "total_unpriced_turns": sum(r.unpriced_turns for r in rows),
        "total_assistant_turns": sum(r.assistant_turns for r in rows),
        "total_user_turns": 0,
        "total_tool_calls": sum(r.tool_calls for r in rows),
        "total_retries": sum(r.retries for r in rows),
        "total_interruptions": sum(r.interruptions for r in rows),
        "session_count": 1,
    }
    defaults.update(overrides)
    return DailySnapshot(
        day=NOW.date(), rows=rows, source_health=(), **defaults  # type: ignore[arg-type]
    )


def _row(**overrides: object) -> UsageRow:
    base: dict[str, object] = {
        "tool": "claude_code",
        "model": "claude-sonnet-4-20250514",
        "assistant_turns": 10,
        "user_turns": 0,
        "tokens": TokenCounts(input=1000, output=500, cached=4000),
        "tool_calls": 5,
        "retries": 0,
        "interruptions": 0,
        "cost_micro_usd": 10_000,
        "unpriced_turns": 0,
    }
    base.update(overrides)
    return UsageRow(**base)  # type: ignore[arg-type]


def test_vocabularies_are_pinned() -> None:
    assert FINDING_IDS == (
        "retry_storm",
        "interruption_cluster",
        "low_cache_reuse",
        "context_bloat",
        "premium_heavy",
        "unpriced_models",
        "late_night_drift",
        "command_friction",
        "refire_after_failure",
        "marathon_session",
        "approaching_quota",
        "approvals_waved_through",
        "context_carried",
    )
    assert SUGGESTION_IDS == (
        "route-routine-to-midtier",
        "keep-sessions-warm",
        "trim-carried-context",
        "update-rate-card",
    )
    assert [s.value for s in Severity] == ["info", "opportunity", "attention"]
    assert [w.value for w in WorkType] == ["build", "investigate", "converse", "unknown"]
    assert [m.value for m in MaturityLevel] == [
        "not_yet", "emerging", "developing", "leading",
    ]
    # The wire contract carries its own literals (no import cycle); they must
    # stay pinned equal to the analysis enums forever.
    from practicegraph.wire import (
        MATURITY_LEVEL_VALUES,
        MATURITY_SIGNAL_KEYS,
        WORK_TYPE_KEYS,
    )

    assert tuple(w.value for w in WorkType) == WORK_TYPE_KEYS
    assert tuple(m.value for m in MaturityLevel) == MATURITY_LEVEL_VALUES
    from practicegraph.analysis.insights import MATURITY_SIGNAL_IDS

    assert MATURITY_SIGNAL_KEYS == MATURITY_SIGNAL_IDS


def test_catalog_copy_passes_the_forbidden_lexicon() -> None:
    """FR-FOC-8 lexicon discipline applies to every closed copy catalog."""
    for title, body in list(FINDING_COPY.values()) + list(SUGGESTION_COPY.values()):
        assert lexicon_violations(title) == []
        assert lexicon_violations(body) == []


def test_detectors_fire_on_their_thresholds() -> None:
    quiet = _snapshot((_row(),))
    assert [f.finding_id for f in detect(quiet)] == []

    retries = _snapshot((_row(retries=3),))
    assert [f.finding_id for f in detect(retries)] == ["retry_storm"]

    cold_cache = _snapshot(
        (_row(tokens=TokenCounts(input=50_000, output=100, cached=1_000)),)
    )
    ids = [f.finding_id for f in detect(cold_cache)]
    assert "low_cache_reuse" in ids

    bloated = _snapshot(
        (_row(assistant_turns=1, tokens=TokenCounts(input=70_000, cached=0)),)
    )
    assert "context_bloat" in [f.finding_id for f in detect(bloated)]

    premium = _snapshot(
        (_row(model="claude-opus-4-1", cost_micro_usd=90_000),
         _row(model="claude-sonnet-4", cost_micro_usd=10_000)),
    )
    assert "premium_heavy" in [f.finding_id for f in detect(premium)]

    unpriced = _snapshot((_row(unpriced_turns=2),))
    findings = detect(unpriced)
    assert findings[-1].finding_id == "unpriced_models"
    assert findings[-1].severity is Severity.INFO


def test_a_retired_detector_can_never_fire_again() -> None:
    """rework_heavy read toolUseResult.userModified, an IDE-side flag that is
    false on every record real logs contain. A detector for a signal the log
    does not emit promises a reading the page can never deliver, so it was
    retired rather than left to look alive. rework_edits is still parsed and
    stored — the column stays, the claim goes."""
    reworked = _snapshot((_row(tool_calls=40, rework_edits=20),))
    assert "rework_heavy" not in [f.finding_id for f in detect(reworked)]
    assert "rework_heavy" not in FINDING_IDS


def test_execution_detectors_fire_on_rates_over_volume_gates() -> None:
    """P1: command_friction is a rate detector gated on real volume — a quiet
    day can never false-alarm (FR-FOC-8 discipline). The floor is 50 steps,
    not 10: the counter increments on every tool_result of any tool, so at a
    floor of 10 a handful of file-not-founds cleared the gate."""
    # 30 of 60 steps erroring -> command_friction; 3 of 12 does not (and 12
    # is under the volume floor regardless).
    friction = _snapshot((_row(commands_run=60, commands_failed=30),))
    found = [f for f in detect(friction) if f.finding_id == "command_friction"]
    assert len(found) == 1
    assert found[0].severity is Severity.OPPORTUNITY
    assert found[0].metric == 50
    assert "command_friction" not in [
        f.finding_id
        for f in detect(_snapshot((_row(commands_run=12, commands_failed=3),)))
    ]
    # High fail rate over too few steps stays silent (volume gate). This is
    # the case the old floor of 10 let through: every step failing, on a
    # sample far too small to mean anything.
    assert "command_friction" not in [
        f.finding_id
        for f in detect(_snapshot((_row(commands_run=10, commands_failed=10),)))
    ]


def test_work_type_mix_is_structural() -> None:
    marks = [
        # build: tool calls present
        ("s1", "2026-07-02T09:00:00+00:00", "assistant_turn", 2, 0, 0, 0, 0),
        # converse: assistant turns, no tools
        ("s2", "2026-07-02T10:00:00+00:00", "assistant_turn", 0, 0, 0, 0, 0),
        # investigate: disruptions dominate
        ("s3", "2026-07-02T11:00:00+00:00", "assistant_turn", 1, 1, 1, 0, 0),
        # unknown: user-only session
        ("s4", "2026-07-02T12:00:00+00:00", "user_turn", 0, 0, 0, 0, 0),
    ]
    assert work_type_mix(marks) == {
        "build": 1, "investigate": 1, "converse": 1, "unknown": 1,
    }


def test_work_type_v2_splits_delegation_from_iteration() -> None:
    """Agentic logs saturated v1 at 100% Build (every turn carries a tool
    call). v2 discriminates by who is driving: delegated tooled runs are
    build; human-steered back-and-forth is investigate."""

    def turn(session: str, minute: int, kind: str, tools: int):
        return (
            session, f"2026-07-02T09:{minute:02d}:00+00:00", kind, tools,
            0, 0, 0, 0,
        )

    # Delegated run: ten tooled turns, one human course-correction.
    build = [turn("b", m, "assistant_turn", 1) for m in range(10)]
    build.insert(5, turn("b", 5, "user_turn", 0))
    # Human-in-the-loop: every plain answer gets a human reply.
    investigate = []
    for index in range(4):
        investigate.append(turn("i", 20 + index * 2, "assistant_turn", 1))
        investigate.append(turn("i", 21 + index * 2, "assistant_turn", 0))
        investigate.append(turn("i", 22 + index * 2, "user_turn", 0))
    mix = work_type_mix(build + investigate)
    assert mix["build"] == 1
    assert mix["investigate"] == 1


def test_marathon_gate_needs_one_sessions_compactions() -> None:
    """P2: the long-haul finding fires only when ONE session compacted its
    context MARATHON_MIN_COMPACTIONS+ times — one fewer, or the same count
    spread across sessions, stays silent. INFO forever by decision."""
    from practicegraph.analysis.insights import (
        MARATHON_MIN_COMPACTIONS,
        marathon_finding,
    )

    def mark(session: str, minute: int, compactions: int):
        return (session, f"2026-07-02T09:{minute:02d}:00+00:00", "user_turn",
                0, 0, 0, 0, compactions)

    fires = [mark("s1", m, 1) for m in range(MARATHON_MIN_COMPACTIONS)]
    finding = marathon_finding(fires)
    assert finding is not None
    assert finding.finding_id == "marathon_session"
    assert finding.severity is Severity.INFO
    assert finding.metric == MARATHON_MIN_COMPACTIONS == 3  # calibrated gate

    below = [mark("s1", m, 1) for m in range(MARATHON_MIN_COMPACTIONS - 1)]
    assert marathon_finding(below) is None  # 2 compactions: silent
    spread = [mark(f"s{i}", i, 1) for i in range(MARATHON_MIN_COMPACTIONS)]
    assert marathon_finding(spread) is None  # never summed across sessions
    assert marathon_finding([]) is None


def test_maturity_levels() -> None:
    snapshot = build_fixture_snapshot()
    signals = dict(maturity_signals(snapshot, active_days_last_7=1))
    assert signals["cache_reuse"] is MaturityLevel.LEADING  # 74% cached
    # Two tools is the ceiling: the Tool enum has exactly two members, so
    # LEADING used to sit at >=3 where nobody could reach it.
    assert signals["multi_tool"] is MaturityLevel.LEADING  # both tools
    assert signals["consistency"] is MaturityLevel.EMERGING  # one active day


def test_suggestion_lifecycle_filters_every_surface(tmp_path: Path) -> None:
    premium = _snapshot(
        (_row(model="claude-opus-4-1", cost_micro_usd=90_000),
         _row(model="claude-sonnet-4", cost_micro_usd=10_000)),
    )
    suggestions = derive_suggestions(premium)
    assert [s.suggestion_id for s in suggestions] == ["route-routine-to-midtier"]

    store = Store(tmp_path / "state.db")
    store.migrate()
    assert visible_suggestions(store, suggestions) == suggestions
    assert dismiss_suggestion(store, "route-routine-to-midtier", never=False, now=NOW)
    assert visible_suggestions(store, suggestions) == []  # FR-RPT-8: gone everywhere
    assert not dismiss_suggestion(store, "not-a-suggestion", never=True, now=NOW)


def test_spend_pace_projection(tmp_path: Path) -> None:
    store, _health = build_fixture_history(tmp_path)
    pace = spend_pace(store, "2026-07-02", budget_micro_usd=500_000)
    assert pace.month_to_date_micro_usd == 42005
    assert pace.projected_micro_usd == 42005 * 31 // 2
    assert pace.over_budget  # projected ~$0.65 > $0.50 budget


def test_fixture_history_snapshot_drives_detectors(tmp_path: Path) -> None:
    store, health = build_fixture_history(tmp_path)
    snapshot = snapshot_for_day(store, NOW.date().replace(day=2), health)
    ids = [f.finding_id for f in detect(snapshot)]
    assert ids == []  # the demo day is healthy — no false alarms
