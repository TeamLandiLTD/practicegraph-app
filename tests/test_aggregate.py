"""Daily aggregation over the fixture pack: the numbers the report shows."""

from __future__ import annotations

from datetime import date

from conftest import build_fixture_snapshot
from practicegraph.events import TokenCounts


def test_fixture_day_totals() -> None:
    snapshot = build_fixture_snapshot()
    # Cost accounting INCLUDES the sidechain transcript (P3 gates behavioral
    # metrics only): 39305 from the main lanes + 2700 sidechain sonnet.
    assert snapshot.total_cost_micro_usd == 42005
    assert snapshot.total_unpriced_turns == 0
    assert snapshot.total_assistant_turns == 9  # 6 sonnet (2 sidechain) + 1 opus + 2 codex
    assert snapshot.total_user_turns == 11  # incl. compact-summary + sidechain
    assert snapshot.total_tool_calls == 3
    assert snapshot.total_retries == 1
    assert snapshot.total_interruptions == 2
    assert snapshot.session_count == 4  # the sidechain session counts here
    # P1 execution-quality sums (3 claude tool_result blocks + 2 codex execs
    # + 2 codex function_call_outputs; failures: 1 is_error + 1 exit_code +
    # 1 "execution error:" prefix; rework: 1 userModified + 1 rejected patch).
    assert sum(r.commands_run for r in snapshot.rows) == 5
    assert sum(r.commands_failed for r in snapshot.rows) == 3
    assert sum(r.commands_slow for r in snapshot.rows) == 1
    assert sum(r.rework_edits for r in snapshot.rows) == 2


def test_rows_are_sorted_and_exact() -> None:
    snapshot = build_fixture_snapshot()
    visible = [r for r in snapshot.rows if r.assistant_turns > 0]
    assert [(r.tool, r.model) for r in visible] == [
        ("claude_code", "claude-opus-4-1-20250805"),
        ("claude_code", "claude-sonnet-4-20250514"),
        ("codex", "gpt-5-codex"),
    ]

    opus, sonnet, codex = visible
    assert opus.assistant_turns == 1
    assert opus.cost_micro_usd == 5175
    assert opus.tokens == TokenCounts(input=25, output=64)

    assert sonnet.assistant_turns == 6  # 4 main + 2 sidechain (P3: still counted)
    assert sonnet.cost_micro_usd == 26580  # 23880 main + 2700 sidechain
    assert sonnet.tokens == TokenCounts(
        input=1455, output=1375, cached=2800, cache_creation=200
    )
    assert sonnet.tool_calls == 2
    assert sonnet.retries == 1

    assert codex.assistant_turns == 2
    assert codex.cost_micro_usd == 10250
    assert codex.tokens == TokenCounts(input=1600, output=750, cached=6000, reasoning=320)
    assert codex.interruptions == 1


def test_other_days_are_empty_not_wrong() -> None:
    snapshot = build_fixture_snapshot(day=date(2026, 7, 3))
    assert snapshot.rows == ()
    assert snapshot.total_cost_micro_usd == 0
    assert snapshot.session_count == 0
    # Source health reflects the scan, not the day: lanes are still reported.
    assert [row.source_id for row in snapshot.source_health] == [
        "claude_code_cli",
        "codex_cli",
    ]


def test_source_health_rollup() -> None:
    snapshot = build_fixture_snapshot()
    by_id = {row.source_id: row.health for row in snapshot.source_health}
    claude = by_id["claude_code_cli"]
    assert (claude.seen, claude.parsed, claude.skipped) == (21, 16, 3)
    assert (claude.malformed, claude.unsupported, claude.unknown_field) == (1, 1, 1)
    codex = by_id["codex_cli"]
    assert (codex.seen, codex.parsed, codex.skipped) == (19, 16, 1)
    assert (codex.malformed, codex.unsupported, codex.unknown_field) == (1, 1, 0)
