"""Advisor receipts: observed per-family economics (docs/ADVISOR_PLAN.md
Phase 1). Pure integer aggregation, deterministic, local-only."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from practicegraph.analysis.advisor_receipts import (
    RECEIPTS_MIN_ACTIVE_DAYS,
    RECEIPTS_MIN_TURNS,
    RECEIPTS_WINDOW_DAYS,
    build_receipts,
    gather_receipts,
)
from practicegraph.store import ContributionRow, Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
END = date(2026, 7, 3)


def _values(**overrides: int) -> list[int]:
    base = dict.fromkeys(ContributionRow._fields, 0)
    base.update(overrides)
    return [base[field] for field in ContributionRow._fields]


def test_families_merge_models_and_derive_integer_economics() -> None:
    """Two model ids of one family merge; every derived figure is exact
    integer math; premium and share flags follow wire vocabulary."""
    rows = [
        (
            "2026-07-01",
            "claude_code",
            "claude-fable-5",
            _values(
                assistant_turns=30,
                input_tokens=3_000,
                output_tokens=6_000,
                cached_tokens=6_000,
                cache_creation_tokens=1_000,
                cost_micro_usd=9_000_000,
                tool_calls=200,
                retries=3,
                rework_edits=10,
                commands_run=50,
                commands_failed=10,
            ),
        ),
        (
            "2026-07-02",
            "claude_code",
            "claude-fable-5-20260630",
            _values(
                assistant_turns=30,
                input_tokens=3_000,
                output_tokens=6_000,
                cost_micro_usd=3_000_000,
                tool_calls=100,
                retries=3,
            ),
        ),
        (
            "2026-07-02",
            "claude_code",
            "claude-haiku-4-5",
            _values(assistant_turns=60, cost_micro_usd=4_000_000),
        ),
    ]
    window = build_receipts(rows, END)

    assert window.end_day == END
    assert window.window_days == RECEIPTS_WINDOW_DAYS
    assert window.total_cost_micro_usd == 16_000_000
    assert window.total_assistant_turns == 120
    assert [(f.tool, f.family) for f in window.families] == [
        ("claude_code", "claude_fable"),
        ("claude_code", "claude_haiku"),
    ]

    fable = window.families[0]
    assert fable.premium and not window.families[1].premium
    assert fable.active_days == 2
    assert fable.assistant_turns == 60
    assert fable.priced_turns == 60
    assert fable.cost_micro_usd == 12_000_000
    # prompt = input 6000 + cached 6000 + cache_creation 1000 = 13000
    assert fable.prompt_tokens_per_turn == 13_000 // 60
    assert fable.output_tokens_per_turn == 12_000 // 60
    assert fable.cost_per_priced_turn_micro_usd == 200_000
    assert fable.cache_hit_pct == 6_000 * 100 // 13_000
    assert fable.cost_share_pct == 75
    assert fable.retries_per_100_turns_tenths == 100  # 6 per 60 = 10.0/100
    assert fable.rework_per_100_tool_calls_tenths == 10 * 1000 // 300
    assert fable.command_fail_pct == 20


def test_window_clips_and_unknown_models_fall_to_other() -> None:
    inside = ("2026-07-01", "codex", "somebody-else-model", _values(assistant_turns=5))
    before = ("2026-06-01", "codex", "gpt-5.5", _values(assistant_turns=99))
    after = ("2026-07-04", "codex", "gpt-5.5", _values(assistant_turns=99))
    window = build_receipts([inside, before, after], END)
    assert [(f.tool, f.family, f.assistant_turns) for f in window.families] == [
        ("codex", "other", 5)
    ]


def test_confidence_needs_both_turn_and_active_day_floors() -> None:
    burst = [
        (
            "2026-07-01",
            "codex",
            "gpt-5.5",
            _values(assistant_turns=RECEIPTS_MIN_TURNS),
        )
    ]
    spread = [
        ("2026-07-0" + str(day), "codex", "gpt-5.5", _values(assistant_turns=1))
        for day in range(1, 1 + RECEIPTS_MIN_ACTIVE_DAYS)
    ]
    enough = [
        (
            "2026-07-0" + str(day),
            "codex",
            "gpt-5.5",
            _values(assistant_turns=RECEIPTS_MIN_TURNS),
        )
        for day in range(1, 1 + RECEIPTS_MIN_ACTIVE_DAYS)
    ]
    assert not build_receipts(burst, END).families[0].confident  # one day only
    assert not build_receipts(spread, END).families[0].confident  # too few turns
    assert build_receipts(enough, END).families[0].confident


def test_zero_denominators_yield_zero_rates() -> None:
    rows = [("2026-07-01", "codex", "gpt-5.5", _values(unpriced_turns=0))]
    econ = build_receipts(rows, END).families[0]
    assert econ.prompt_tokens_per_turn == 0
    assert econ.output_tokens_per_turn == 0
    assert econ.cost_per_priced_turn_micro_usd == 0
    assert econ.cache_hit_pct == 0
    assert econ.cost_share_pct == 0
    assert econ.retries_per_100_turns_tenths == 0
    assert econ.rework_per_100_tool_calls_tenths == 0
    assert econ.command_fail_pct == 0


def test_unpriced_turns_leave_the_priced_denominator() -> None:
    rows = [
        (
            "2026-07-01",
            "codex",
            "mystery-model",
            _values(assistant_turns=10, unpriced_turns=4, cost_micro_usd=600_000),
        )
    ]
    econ = build_receipts(rows, END).families[0]
    assert econ.priced_turns == 6
    assert econ.cost_per_priced_turn_micro_usd == 100_000


def test_input_order_never_changes_the_output() -> None:
    rows = [
        ("2026-07-02", "codex", "gpt-5.5", _values(assistant_turns=2)),
        ("2026-07-01", "claude_code", "claude-fable-5", _values(assistant_turns=1)),
        ("2026-07-01", "codex", "gpt-5.4-mini", _values(assistant_turns=3)),
    ]
    assert build_receipts(rows, END) == build_receipts(list(reversed(rows)), END)


def test_gather_receipts_reads_persisted_history(tmp_path: Path) -> None:
    """Store-backed entry point: multi-file, multi-day history lands in one
    window; days outside the trailing window are clipped."""
    store = Store(tmp_path / "state.db")
    store.migrate()
    store.replace_file_data(
        source_id="claude_code_cli",
        path_hash="h1",
        cursor=(1, 1, 1),
        contributions=[
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(assistant_turns=4, cost_micro_usd=400_000),
            ),
            (
                "2026-07-02",
                "claude_code",
                "claude-fable-5",
                _values(assistant_turns=6, cost_micro_usd=600_000),
            ),
        ],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
    )
    store.replace_file_data(
        source_id="codex_cli",
        path_hash="h2",
        cursor=(1, 1, 1),
        contributions=[
            (
                "2026-07-02",
                "codex",
                "gpt-5.5",
                _values(assistant_turns=8, cost_micro_usd=200_000),
            ),
            # Ancient history: must clip out of the 28-day window.
            (
                "2020-01-01",
                "codex",
                "gpt-5.5",
                _values(assistant_turns=100, cost_micro_usd=9_000_000),
            ),
        ],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
    )

    window = gather_receipts(store, END)
    assert window.total_cost_micro_usd == 1_200_000
    assert window.total_assistant_turns == 18
    by_key = {(f.tool, f.family): f for f in window.families}
    assert by_key[("claude_code", "claude_fable")].active_days == 2
    assert by_key[("claude_code", "claude_fable")].cost_share_pct == 83
    assert by_key[("codex", "gpt_5")].assistant_turns == 8
