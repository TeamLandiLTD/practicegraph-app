"""Closed local capability-unit summaries."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from practicegraph.analysis.capability_units import closed_capability_units
from practicegraph.events import TokenCounts, Tool, TurnEvent, TurnKind
from practicegraph.history import _marks_from_events, _work_spans_from_events
from practicegraph.store import Store

TODAY = date(2026, 7, 3)
AS_OF_EARLY = datetime(2026, 7, 3, 10, 45, tzinfo=UTC)
AS_OF_LATE = datetime(2026, 7, 3, 11, 5, tzinfo=UTC)


def _write_history(
    store: Store,
    events: tuple[TurnEvent, ...],
    *,
    path_hash: str,
    mark_events: tuple[TurnEvent, ...] | None = None,
) -> None:
    store.replace_file_data(
        source_id="claude_code",
        path_hash=path_hash,
        cursor=(1, 1, 1),
        contributions=[],
        marks=_marks_from_events(mark_events or events),
        health=None,
        turn_keys=(),
        now=AS_OF_LATE,
        work_spans=_work_spans_from_events(events),
    )


def _seed_capability_history(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    events = tuple(
        TurnEvent(
            timestamp=datetime(2026, 7, 3, 10, 0, tzinfo=UTC)
            + timedelta(minutes=minute),
            session_id="session-a",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(
                input=1_200 if minute == 0 else 0,
                cached=800 if minute == 0 else 0,
            ),
            retries=2 if minute == 10 else 0,
            commands_failed=1 if minute == 15 else 0,
            git_commit_attempts=1 if minute == 20 else 0,
            test_run_attempts=1 if minute == 25 else 0,
            files_created=1 if minute == 0 else 0,
            doc_files_created=1 if minute == 0 else 0,
            export_writes=1 if minute == 30 else 0,
            files_edited=1 if minute in (5, 35) else 0,
            cwd_hash="project-a",
            branch_hash="feature-a",
        )
        for minute in (0, 5, 10, 15, 20, 25, 30, 35)
    )
    _write_history(store, events, path_hash="capability-history")
    return store


def test_closed_units_require_turn_floor_and_idle_grace(tmp_path: Path) -> None:
    from practicegraph.analysis.capability_units import closed_capability_units

    store = _seed_capability_history(tmp_path)

    assert closed_capability_units(store, TODAY, AS_OF_EARLY) == ()
    units = closed_capability_units(store, TODAY, AS_OF_LATE)
    assert len(units) == 1
    assert units[0].assistant_turns == 8


def test_capability_unit_key_is_local_and_deterministic(tmp_path: Path) -> None:
    from practicegraph.analysis.capability_units import closed_capability_units

    store = _seed_capability_history(tmp_path)

    first = closed_capability_units(store, TODAY, AS_OF_LATE)
    second = closed_capability_units(store, TODAY, AS_OF_LATE)
    assert len(first[0].unit_key) == 24
    assert first == second


def test_capability_unit_summarizes_structural_evidence(tmp_path: Path) -> None:
    from practicegraph.analysis.capability_units import closed_capability_units

    unit = closed_capability_units(_seed_capability_history(tmp_path), TODAY, AS_OF_LATE)[0]

    assert unit.prompt_tokens == 2_000
    assert unit.retries == 2
    assert unit.command_failures == 1
    assert unit.git_commit_attempts == 1
    assert unit.test_run_attempts == 1
    assert unit.files_created == 1
    assert unit.doc_files_created == 1
    assert unit.export_writes == 1
    assert unit.files_edited == 2
    assert unit.knowledge_evidence is True
    assert unit.software_evidence is True


@pytest.mark.parametrize(("age_days", "included"), [(2, True), (3, False)])
def test_candidate_units_keep_the_exact_two_day_lookback(
    tmp_path: Path,
    age_days: int,
    included: bool,
) -> None:
    """Expanding candidate recency would prompt for work older than two days."""
    store = Store(tmp_path / "state.db")
    store.migrate()
    start = datetime.combine(
        TODAY - timedelta(days=age_days),
        datetime.min.time(),
        tzinfo=UTC,
    ) + timedelta(hours=9)
    events = tuple(
        TurnEvent(
            timestamp=start + timedelta(minutes=index * 5),
            session_id=f"age-{age_days}",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(input=100),
            cwd_hash=f"project-age-{age_days}",
            branch_hash="",
        )
        for index in range(5)
    )
    _write_history(store, events, path_hash=f"age-{age_days}")

    units = closed_capability_units(store, TODAY, AS_OF_LATE)

    assert bool(units) is included


def test_marks_must_match_the_session_and_episode_window(tmp_path: Path) -> None:
    """Cross-session or out-of-window marks must not change a unit's evidence."""
    store = Store(tmp_path / "state.db")
    store.migrate()
    start = datetime(2026, 7, 3, 9, tzinfo=UTC)
    events = tuple(
        TurnEvent(
            timestamp=start + timedelta(minutes=index * 5),
            session_id="owned-session",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(input=100),
            retries=2 if index == 2 else 0,
            commands_failed=1 if index == 3 else 0,
            cwd_hash="owned-project",
            branch_hash="",
        )
        for index in range(5)
    )
    unrelated = (
        TurnEvent(
            timestamp=start + timedelta(minutes=10),
            session_id="different-session",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(),
            retries=7,
            commands_failed=7,
            cwd_hash="owned-project",
            branch_hash="",
        ),
        TurnEvent(
            timestamp=start - timedelta(minutes=1),
            session_id="owned-session",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(),
            retries=11,
            commands_failed=11,
            cwd_hash="owned-project",
            branch_hash="",
        ),
        TurnEvent(
            timestamp=start + timedelta(minutes=21),
            session_id="owned-session",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(),
            retries=13,
            commands_failed=13,
            cwd_hash="owned-project",
            branch_hash="",
        ),
    )
    _write_history(
        store,
        events,
        path_hash="mark-attribution",
        mark_events=(*events, *unrelated),
    )

    unit = closed_capability_units(store, TODAY, AS_OF_LATE)[0]

    assert unit.retries == 2
    assert unit.command_failures == 1


def test_generic_file_activity_does_not_invent_path_evidence(tmp_path: Path) -> None:
    """Generic file changes alone must not become knowledge or software evidence."""
    store = Store(tmp_path / "state.db")
    store.migrate()
    events = tuple(
        TurnEvent(
            timestamp=datetime(2026, 7, 3, 9, tzinfo=UTC)
            + timedelta(minutes=index * 5),
            session_id="generic-session",
            source_id="claude_code",
            tool=Tool.CLAUDE_CODE,
            kind=TurnKind.ASSISTANT_TURN,
            model="claude-fable-5",
            tokens=TokenCounts(input=100),
            files_created=1 if index == 0 else 0,
            files_edited=1 if index == 1 else 0,
            cwd_hash="generic-project",
            branch_hash="",
        )
        for index in range(5)
    )
    _write_history(store, events, path_hash="generic-evidence")

    unit = closed_capability_units(store, TODAY, AS_OF_LATE)[0]

    assert unit.knowledge_evidence is False
    assert unit.software_evidence is False


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "Pacific/Kiritimati"])
def test_local_horizon_covers_utc_days_on_both_sides(tmp_path: Path, zone: str) -> None:
    from dataclasses import replace
    from datetime import time

    from practicegraph.analysis.schedule import compatibility_utc_schedule

    profile = replace(compatibility_utc_schedule(), timezone_name=zone)
    store = Store(tmp_path / "state.db")
    store.migrate()
    # Late local work may be on the next UTC day; early local work may be on
    # the previous UTC day. Neither should disappear because of UTC queries.
    for hour in (1, 22):
        start = datetime.combine(TODAY, time(hour), tzinfo=profile.zone).astimezone(UTC)
        events = tuple(TurnEvent(
            timestamp=start + timedelta(minutes=i), session_id=f"session-{hour}",
            source_id="claude_code", tool=Tool.CLAUDE_CODE, kind=TurnKind.ASSISTANT_TURN,
            model="fixture", tokens=TokenCounts(),
            cwd_hash=f"project-{hour}", branch_hash="fixture",
        ) for i in range(6))
        _write_history(store, events, path_hash=f"file-{hour}")
    as_of = datetime.combine(TODAY, time(23), tzinfo=profile.zone).astimezone(UTC)
    result = closed_capability_units(store, TODAY, as_of, schedule=profile)
    assert len(result) == 2
