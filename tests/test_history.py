"""Incremental ingest and daily history (FR-SRC-7, FR-ANL-7)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import (
    FIXTURE_DAY,
    FIXTURE_ENV,
    FIXTURE_INGEST_NOW,
    build_fixture_snapshot,
)
from practicegraph.history import (
    ingest,
    path_identity,
    profile_stats,
    snapshot_for_day,
    spend_series,
    week_over_week,
)
from practicegraph.sources.claude_code import ADAPTER as CLAUDE_ADAPTER
from practicegraph.store import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _copy_fixtures(tmp_path: Path) -> dict[str, str]:
    """A mutable copy of the fixture tree so tests can grow/rotate files."""
    root = tmp_path / "fixtures"
    shutil.copytree(Path(FIXTURE_ENV["PRACTICEGRAPH_CLAUDE_HOME"]), root / "claude_code")
    shutil.copytree(Path(FIXTURE_ENV["PRACTICEGRAPH_CODEX_HOME"]), root / "codex")
    return {
        "PRACTICEGRAPH_CLAUDE_HOME": str(root / "claude_code"),
        "PRACTICEGRAPH_CODEX_HOME": str(root / "codex"),
    }


def test_history_snapshot_equals_direct_parse(tmp_path: Path) -> None:
    """The store-backed snapshot must be identical to the direct-parse one —
    the two paths can never drift (M1 correctness pin)."""
    store = _store(tmp_path)
    health = ingest(dict(FIXTURE_ENV), store, FIXTURE_INGEST_NOW)
    from_history = snapshot_for_day(store, FIXTURE_DAY, health)
    direct = build_fixture_snapshot()
    assert from_history == direct


def test_second_scan_skips_unchanged_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    env = dict(FIXTURE_ENV)
    ingest(env, store, FIXTURE_INGEST_NOW)
    first = snapshot_for_day(store, FIXTURE_DAY, [])
    calls: list[Path] = []
    original = CLAUDE_ADAPTER.parse_file_seen

    def counting(path: Path, seen: set[str]) -> object:
        calls.append(path)
        return original(path, seen)

    CLAUDE_ADAPTER.parse_file_seen = counting  # type: ignore[method-assign]
    try:
        ingest(env, store, FIXTURE_INGEST_NOW + timedelta(minutes=15))
    finally:
        CLAUDE_ADAPTER.parse_file_seen = original  # type: ignore[method-assign]
    assert calls == []  # every file skipped via cursor fingerprint
    assert snapshot_for_day(store, FIXTURE_DAY, []) == first  # nothing double-counted


def test_grown_file_is_reparsed_without_double_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    env = _copy_fixtures(tmp_path)
    ingest(env, store, FIXTURE_INGEST_NOW)
    before = snapshot_for_day(store, FIXTURE_DAY, [])

    session = (
        Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "C--demo-app"
        / "22222222-2222-2222-2222-222222222222.jsonl"
    )
    line = (
        '{"parentUuid":"bbbbbbbb-0002","isSidechain":false,"userType":"external",'
        '"cwd":"C:\\\\Users\\\\demo.user\\\\projects\\\\demo-app",'
        '"sessionId":"22222222-2222-2222-2222-222222222222","version":"1.0.44",'
        '"type":"assistant","message":{"id":"msg_1002","type":"message",'
        '"role":"assistant","model":"claude-sonnet-4-20250514",'
        '"content":[{"type":"text","text":"more"}],"usage":{"input_tokens":100,'
        '"cache_creation_input_tokens":0,"cache_read_input_tokens":0,'
        '"output_tokens":50}},"requestId":"req_1002","uuid":"bbbbbbbb-0003",'
        '"timestamp":"2026-07-02T15:00:00.000Z"}\n'
    )
    with session.open("a", encoding="utf-8") as handle:
        handle.write(line)

    ingest(env, store, FIXTURE_INGEST_NOW + timedelta(hours=1))
    after = snapshot_for_day(store, FIXTURE_DAY, [])
    # Exactly one new turn's tokens — the file's older lines were replaced,
    # not re-added.
    assert after.total_assistant_turns == before.total_assistant_turns + 1
    sonnet = next(r for r in after.rows if r.model.startswith("claude-sonnet"))
    sonnet_before = next(r for r in before.rows if r.model.startswith("claude-sonnet"))
    assert sonnet.tokens.input == sonnet_before.tokens.input + 100


def test_truncated_file_triggers_clean_rescan(tmp_path: Path) -> None:
    """Rotation/truncation detection (FR-SRC-7)."""
    store = _store(tmp_path)
    env = _copy_fixtures(tmp_path)
    ingest(env, store, FIXTURE_INGEST_NOW)
    session = (
        Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "C--demo-app"
        / "22222222-2222-2222-2222-222222222222.jsonl"
    )
    lines = session.read_text(encoding="utf-8").splitlines(keepends=True)
    session.write_text(lines[0], encoding="utf-8")  # truncated to the user turn
    ingest(env, store, FIXTURE_INGEST_NOW + timedelta(hours=1))
    snapshot = snapshot_for_day(store, FIXTURE_DAY, [])
    sonnet = next(r for r in snapshot.rows if r.model.startswith("claude-sonnet"))
    assert sonnet.assistant_turns == 5  # the truncated file's turn is gone


def test_deleted_file_state_is_pruned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    env = _copy_fixtures(tmp_path)
    ingest(env, store, FIXTURE_INGEST_NOW)
    session = (
        Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "C--demo-app"
        / "22222222-2222-2222-2222-222222222222.jsonl"
    )
    identity = path_identity(session)
    assert store.cursor_get("claude_code_cli", identity) is not None
    session.unlink()
    ingest(env, store, FIXTURE_INGEST_NOW + timedelta(hours=1))
    assert store.cursor_get("claude_code_cli", identity) is None


def test_parser_version_bump_invalidates_cursors(tmp_path: Path) -> None:
    store = _store(tmp_path)
    env = dict(FIXTURE_ENV)
    ingest(env, store, FIXTURE_INGEST_NOW)
    calls: list[Path] = []
    original_parse = CLAUDE_ADAPTER.parse_file_seen
    original_version = CLAUDE_ADAPTER.parser_version

    def counting(path: Path, seen: set[str]) -> object:
        calls.append(path)
        return original_parse(path, seen)

    CLAUDE_ADAPTER.parse_file_seen = counting  # type: ignore[method-assign]
    CLAUDE_ADAPTER.parser_version = original_version + 1  # type: ignore[misc]
    try:
        ingest(env, store, FIXTURE_INGEST_NOW + timedelta(minutes=15))
    finally:
        CLAUDE_ADAPTER.parse_file_seen = original_parse  # type: ignore[method-assign]
        CLAUDE_ADAPTER.parser_version = original_version  # type: ignore[misc]
    assert len(calls) == 4  # forced clean rescan of every claude file


def test_spend_series_and_week_over_week(tmp_path: Path) -> None:
    store, _health = _store(tmp_path), None
    health = ingest(dict(FIXTURE_ENV), store, FIXTURE_INGEST_NOW)
    assert health  # sanity
    series = spend_series(store, FIXTURE_DAY, 7)
    assert len(series) == 7
    assert series[-1].day == "2026-07-02"
    assert series[-1].cost_micro_usd == 42005
    assert sum(1 for p in series if p.cost_micro_usd > 0) == 1
    # No prior-week activity yet: the comparison stays honestly absent.
    assert week_over_week(store, FIXTURE_DAY) is None
    # Seed a synthetic prior week directly and the deltas appear.
    store.replace_file_data(
        source_id="claude_code_cli",
        path_hash="synthetic-prior-week",
        cursor=(1, 1, 2),
        contributions=[
            ("2026-06-25", "claude_code", "claude-sonnet-4-20250514",
             [1, 0, 100, 100, 0, 0, 0, 0, 0, 0, 42005, 0, 0, 0, 0, 0, 0, 0])
        ],
        marks=[],
        health=None,
        turn_keys=(),
        now=datetime(2026, 7, 3, tzinfo=UTC),
    )
    wow = week_over_week(store, FIXTURE_DAY)
    assert wow is not None
    assert wow.cost_delta_pct == 0  # same spend both weeks


def test_hygiene_prunes_aged_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest(dict(FIXTURE_ENV), store, FIXTURE_INGEST_NOW)
    assert store.active_days("2026-07-01", "2026-07-03") == ["2026-07-02"]
    pruned = store.hygiene(cutoff_day="2026-07-03")
    assert pruned["file_contributions"] > 0
    assert store.active_days("2026-07-01", "2026-07-03") == []


def test_rate_card_change_forces_a_repricing_rescan(tmp_path: Path) -> None:
    """Persisted costs re-price when the active card changes: every cursor is
    invalidated and the next scan re-parses from scratch."""
    import json as json_module

    from practicegraph.analysis.ratecard import activate_rate_card_from

    store = _store(tmp_path)
    env = dict(FIXTURE_ENV)
    ingest(env, store, FIXTURE_INGEST_NOW)
    baseline = snapshot_for_day(store, FIXTURE_DAY, [])
    assert baseline.total_cost_micro_usd == 42005

    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    doubled = {
        "rate_card_version": "cat-doubled",
        "unit": "micro_usd_per_1m_tokens",
        "rates": {
            "claude": {"input": 0, "output": 0, "cache_read": 0,
                       "cache_creation": 0, "cache_creation_1h": 0},
            "gpt": {"input": 0, "output": 0, "cache_read": 0,
                    "cache_creation": 0, "cache_creation_1h": 0},
        },
    }
    (catalog_dir / "rate-card.json").write_text(
        json_module.dumps(doubled), encoding="utf-8"
    )
    assert activate_rate_card_from(tmp_path) == "catalog"
    ingest(env, store, FIXTURE_INGEST_NOW + timedelta(minutes=15))
    repriced = snapshot_for_day(store, FIXTURE_DAY, [])
    assert repriced.total_cost_micro_usd == 0  # everything re-priced at $0
    assert repriced.total_assistant_turns == baseline.total_assistant_turns


def test_profile_stats_streaks_and_peaks(tmp_path: Path) -> None:
    """Profile-grade stats (heatmap/streak layer) from local history only."""
    store = _store(tmp_path)
    ingest(dict(FIXTURE_ENV), store, FIXTURE_INGEST_NOW)
    # Seed two synthetic prior days to form a 3-day streak ending 2026-07-02.
    for day, cost in (("2026-06-30", 100_000), ("2026-07-01", 200_000)):
        store.replace_file_data(
            source_id="demo_seed",
            path_hash=f"seed-{day}",
            cursor=(1, 1, 1),
            contributions=[
                (day, "claude_code", "claude-sonnet-4-20250514",
                 [1, 0, 100, 50, 0, 0, 0, 0, 0, 0, cost, 0, 0, 0, 0, 0, 0, 0])
            ],
            marks=[],
            health=None,
            turn_keys=(),
            now=FIXTURE_INGEST_NOW,
        )
    stats = profile_stats(store, FIXTURE_DAY)
    assert stats.active_days_total == 3
    assert stats.current_streak_days == 3  # 06-30, 07-01, 07-02
    assert stats.longest_streak_days == 3
    assert stats.peak_day == "2026-07-02"  # 12.2K fixture tokens beat the seeds
    assert stats.longest_block_min == 4  # from the fixture activity timeline
    assert stats.lifetime_cost_micro_usd == 42005 + 300_000
    # A gap ends the current streak: evaluated two days later it is zero.
    from datetime import date as date_type

    later = profile_stats(store, date_type(2026, 7, 5))
    assert later.current_streak_days == 0
    assert later.longest_streak_days == 3


def test_path_identity_is_a_hash(tmp_path: Path) -> None:
    """NFR-PRV-1: full paths never land in the store."""
    identity = path_identity(tmp_path / "secret-project" / "x.jsonl")
    assert "secret-project" not in identity
    assert len(identity) == 64


def test_no_paths_persisted_in_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest(dict(FIXTURE_ENV), store, FIXTURE_INGEST_NOW)
    raw = (tmp_path / "state.db").read_bytes()
    assert b"C--demo-app" not in raw
    assert b"fixtures" not in raw


def test_range_summaries_cover_week_month_all() -> None:
    """Today / 7d / 30d / all-time range views (multivendor visible: a tool
    quiet today keeps its slice and its last-active day)."""
    from conftest import build_fixture_history
    from practicegraph.history import RANGE_KINDS, gather_ranges

    store, _health = build_fixture_history()
    summaries = gather_ranges(store, FIXTURE_DAY)
    assert [s.range_id for s in summaries] == [rid for rid, _l, _d in RANGE_KINDS]
    week = summaries[0]
    assert week.active_days >= 1
    assert week.cost_micro_usd > 0
    assert len(week.series) == 7  # zero-filled daily bars
    tools = {split.tool for split in week.tools}
    assert "claude_code" in tools and "codex" in tools
    for split in week.tools:
        assert 0 <= split.share_pct <= 100
        assert split.last_active <= FIXTURE_DAY.isoformat()
        assert split.top_model
    assert week.models, "top-model rows present"
    assert all(r.assistant_turns > 0 or r.cost_micro_usd > 0 for r in week.models)
    month, all_time = summaries[1], summaries[2]
    assert month.cost_micro_usd >= week.cost_micro_usd
    assert all_time.cost_micro_usd >= month.cost_micro_usd
    # All time spans exactly the active history (first active day onward).
    assert all_time.from_day <= all_time.to_day
    assert all_time.active_days >= week.active_days
    assert all(len(label) == 7 for label, _cost in all_time.series)  # YYYY-MM
