"""Work units (W4): deterministic spans -> episodes -> units, the
distribution reading, and the no-peek validation probe. Everything here is
exact-integer and structural — no content, no LLM, no tunable that is not a
frozen named constant (docs/COST_PER_TASK_RESEARCH.md v2)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from practicegraph.analysis.dayclose import record_pieces_probe
from practicegraph.analysis.schedule import read_schedule_profile
from practicegraph.analysis.workunits import (
    LINE_OF_WORK_EPISODES,
    WORK_UNITS_MIN,
    Episode,
    compose_work_units,
    episodes_between,
    measured_units_for_day,
    work_units,
)
from practicegraph.events import TokenCounts, Tool, TurnEvent, TurnKind
from practicegraph.history import _work_spans_from_events
from practicegraph.store import Store

TODAY = date(2026, 7, 3)
NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _turn(
    day: int,
    minute: int,
    session: str = "s1",
    cwd: str = "proj-a",
    branch: str = "feat-1",
    commits: int = 0,
    cost_tokens: int = 1_000,
) -> TurnEvent:
    return TurnEvent(
        timestamp=datetime(2026, 7, day, 9, 0, tzinfo=UTC)
        + timedelta(minutes=minute),
        session_id=session,
        source_id="claude_code",
        tool=Tool.CLAUDE_CODE,
        kind=TurnKind.ASSISTANT_TURN,
        model="claude-fable-5",
        tokens=TokenCounts(input=cost_tokens, output=0),
        cwd_hash=cwd,
        branch_hash=branch,
        git_commit_attempts=commits,
    )


def _seed_spans(
    store: Store, events: tuple[TurnEvent, ...], path_hash: str = "h-wu"
) -> None:
    store.replace_file_data(
        source_id="claude_code",
        path_hash=path_hash,
        cursor=(1, 1, 9),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        work_spans=_work_spans_from_events(events),
    )


def _episode(
    day: int, branch: str, turns: int = 6, commits: int = 0
) -> Episode:
    start = datetime(2026, 7, day, 9, 0, tzinfo=UTC)
    return Episode(
        cwd_hash="proj-a",
        branch_hash=branch,
        first_ts=start,
        last_ts=start + timedelta(minutes=30),
        assistant_turns=turns,
        cost_micro_usd=1_000_000 * turns,
        unpriced_turns=0,
        git_commit_attempts=commits,
        test_run_attempts=0,
    )


def test_episodes_chain_across_sessions_but_not_across_gaps(
    tmp_path: Path,
) -> None:
    """Session identity is deliberately not a boundary (the 3% finding): a
    clear-and-continue on the same project x branch within the gap is ONE
    episode; a real break is two."""
    store = _store(tmp_path)
    events = (
        _turn(1, 0),
        _turn(1, 10),
        _turn(1, 25, session="s2"),  # new session, 15min later -> chains
        _turn(1, 120),  # 95min gap -> new episode
    )
    _seed_spans(store, events)
    episodes = episodes_between(store, date(2026, 7, 1), date(2026, 7, 1))
    assert len(episodes) == 2
    assert episodes[0].assistant_turns == 3  # spans of s1+s2 merged
    assert episodes[1].assistant_turns == 1


def test_cross_file_overlap_merges_into_one_episode(tmp_path: Path) -> None:
    """A parent transcript and a subagent transcript carry disjoint turns of
    the same sitting in different FILES; the fold rejoins them."""
    store = _store(tmp_path)
    _seed_spans(store, (_turn(1, 0), _turn(1, 20)), path_hash="h-parent")
    _seed_spans(
        store,
        (_turn(1, 5, session="sub"), _turn(1, 15, session="sub")),
        path_hash="h-sub",
    )
    episodes = episodes_between(store, date(2026, 7, 1), date(2026, 7, 1))
    assert len(episodes) == 1
    assert episodes[0].assistant_turns == 4


def test_unit_rule_thread_vs_line_of_work() -> None:
    """A small thread is one unit; a thread past the episode limit becomes a
    line of work whose episodes are the units."""
    small = [_episode(1, "feat-1"), _episode(2, "feat-1", commits=1)]
    units = work_units(small)
    assert len(units) == 1
    assert units[0].kind == "thread"
    assert units[0].episodes == 2
    assert units[0].git_commit_attempts == 1

    trunk = [
        _episode(day, "main") for day in range(1, LINE_OF_WORK_EPISODES + 3)
    ]
    units = work_units(trunk)
    assert all(unit.kind == "line_episode" for unit in units)
    assert len(units) == LINE_OF_WORK_EPISODES + 2


def test_reading_is_withheld_below_the_unit_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_spans(
        store,
        (_turn(1, 0), _turn(1, 5), _turn(1, 10), _turn(1, 15), _turn(1, 20)),
    )
    reading = compose_work_units(store, TODAY)
    assert reading.available is False


def test_reading_distribution_is_exact(tmp_path: Path) -> None:
    """WORK_UNITS_MIN one-sitting threads with known costs: median, p90,
    concentration, and landed share all land on hand-computed integers."""
    store = _store(tmp_path)
    events = []
    for i in range(WORK_UNITS_MIN):
        # One thread per branch: 5 turns each 2min apart; cost scales with
        # i; only the last thread carries a commit attempt.
        for turn_index in range(5):
            events.append(
                _turn(
                    1 + (i // 8),
                    (i % 8) * 60 + turn_index * 2,
                    session=f"s{i}",
                    branch=f"feat-{i}",
                    commits=(
                        1
                        if (i == WORK_UNITS_MIN - 1 and turn_index == 4)
                        else 0
                    ),
                    cost_tokens=(i + 1) * 1_000,
                )
            )
    _seed_spans(store, tuple(events))
    reading = compose_work_units(store, TODAY)
    assert reading.available is True
    assert reading.units == WORK_UNITS_MIN
    assert reading.landed_units == 1
    assert reading.landed_share_pct == 100 // WORK_UNITS_MIN
    # claude-fable-5 input is listed at $10/M: unit i cost (i+1)*5k tokens.
    costs = sorted((i + 1) * 5_000 * 10 for i in range(WORK_UNITS_MIN))
    assert reading.median_cost_micro_usd == costs[WORK_UNITS_MIN // 2]
    top = costs[-max(1, WORK_UNITS_MIN // 10) :]
    assert reading.top_decile_share_pct == sum(top) * 100 // sum(costs)


def test_probe_is_no_peek_and_first_answer_wins(tmp_path: Path) -> None:
    """The measured side is computed server-side at record time; the client
    supplies only the felt count; a second answer for the same day is
    refused; junk is refused, never clamped."""
    store = _store(tmp_path)
    events = tuple(
        _turn(3, i * 60 + j * 2, session=f"s{i}", branch=f"feat-{i}")
        for i in range(3)
        for j in range(5)
    )
    _seed_spans(store, events)
    assert record_pieces_probe(store, "2026-07-03", 4) is True
    raw = store.meta_get("wu_probe:2026-07-03")
    assert raw is not None
    pair = json.loads(raw)
    assert pair["felt"] == 4
    profile = read_schedule_profile(store.path.parent)
    assert pair["measured"] == measured_units_for_day(
        store, date(2026, 7, 3), profile
    )
    # First answer wins; junk refused.
    assert record_pieces_probe(store, "2026-07-03", 2) is False
    assert json.loads(store.meta_get("wu_probe:2026-07-03"))["felt"] == 4
    assert record_pieces_probe(store, "2026-07-04", True) is False
    assert record_pieces_probe(store, "2026-07-04", -1) is False
    assert record_pieces_probe(store, "2026-07-04", 31) is False
    assert record_pieces_probe(store, "not-a-day", 3) is False


def test_reading_carries_render_ready_lines_and_top_rows(tmp_path: Path) -> None:
    """The card renders from minted sentences and shaped rows — window grain
    only, and every sentence stays inside the copy lexicon."""
    from practicegraph.privacy import leak_findings, lexicon_violations

    store = _store(tmp_path)
    events = []
    for i in range(WORK_UNITS_MIN):
        for turn_index in range(5):
            events.append(
                _turn(
                    1 + (i // 8),
                    (i % 8) * 60 + turn_index * 2,
                    session=f"s{i}",
                    branch=f"feat-{i}",
                    commits=1 if i == 0 else 0,
                    cost_tokens=(i + 1) * 1_000,
                )
            )
    _seed_spans(store, tuple(events))
    reading = compose_work_units(store, TODAY)
    assert reading.available
    assert "tenth of your pieces" in reading.concentration_line
    assert "not a merge" in reading.landed_line
    assert len(reading.top) == 3
    assert reading.top[0].cost_micro_usd >= reading.top[1].cost_micro_usd
    for row in reading.top:
        assert row.started_day.startswith("2026-07-")
        assert row.hours_tenths >= 0
        assert row.outcome in ("commit", "files", "none")
    for text in (reading.concentration_line, reading.landed_line):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_units_left_files_widen_the_landed_line(tmp_path: Path) -> None:
    """A commit-less unit that created files reads as outcome 'files' and
    joins the landed line's second clause — production, never value."""
    from practicegraph.analysis.workunits import Episode

    def episode(branch: str, commits: int = 0, created: int = 0) -> Episode:
        start = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
        return Episode(
            cwd_hash="proj-a", branch_hash=branch,
            first_ts=start, last_ts=start + timedelta(minutes=30),
            assistant_turns=6, cost_micro_usd=6_000_000,
            unpriced_turns=0, git_commit_attempts=commits,
            test_run_attempts=0, files_created=created,
        )

    units = work_units([
        episode("feat-committed", commits=2, created=3),
        episode("feat-docs-only", created=2),
        episode("feat-nothing"),
    ])
    outcomes = sorted(u.outcome for u in units)
    assert outcomes == ["commit", "files", "none"]
