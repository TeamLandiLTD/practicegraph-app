"""Observed per-family economics — the Advisor's receipts layer (Phase 1).

Every Advisor verdict must cite the user's own numbers before any market
figure (docs/ADVISOR_PLAN.md §3-4). This module computes those numbers from
persisted day x (tool, model) counters: pure integer arithmetic, deterministic
(INV-6), no new parsing, and LOCAL-ONLY like every behavioral surface
(NFR-PRV-6) — nothing here may feed an emit.

Family attribution is turn-normalized on purpose. Sessions legitimately mix
models (subagents, mid-session /model switches), so "tokens per session per
family" would be a made-up attribution; per-assistant-turn units are the
honest denominator the store can actually support. Session-shape stats
(duration, marathons) stay with focus/performance and join at fusion
(Phase 3).

All money is integer micro-USD; rates render one decimal via per-100-turn
tenths — no floats anywhere (INV-6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from practicegraph.events import TokenCounts
from practicegraph.store import ContributionRow, Store
from practicegraph.wire import PREMIUM_FAMILIES, model_family

# Local-only vocabulary: these terms must never appear in a wire field name
# (NFR-PRV-6; test_wire.py unions every module's list into the name scan).
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = ("advisor", "receipts", "verdict")

RECEIPTS_WINDOW_DAYS = 28  # one window, aligned with PERFORMANCE_WINDOW_DAYS

# Sample floors (spec §0 named-constant rule). Below either floor a family is
# marked unconfident and the Advisor renders the honest withheld state — a
# low-n guess is never dressed up as a verdict (docs/ADVISOR_PLAN.md §3).
RECEIPTS_MIN_TURNS = 40
RECEIPTS_MIN_ACTIVE_DAYS = 3


@dataclass(frozen=True, slots=True)
class FamilyEconomics:
    """One (tool, family) window aggregate plus its derived unit economics.

    ``prompt`` tokens = input + cached + cache_creation (everything the model
    read); ``cache_hit_pct`` is cached's share of that prompt volume. Derived
    rates are 0 whenever their denominator is 0.
    """

    tool: str
    family: str  # wire.ModelFamily value — raw model strings stay local
    premium: bool
    confident: bool
    active_days: int
    assistant_turns: int
    priced_turns: int
    unpriced_turns: int
    cost_micro_usd: int
    tokens: TokenCounts
    tool_calls: int
    retries: int
    interruptions: int
    rework_edits: int
    commands_run: int
    commands_failed: int
    commands_slow: int
    compactions: int
    # Derived, integer-only.
    prompt_tokens_per_turn: int
    output_tokens_per_turn: int
    cost_per_priced_turn_micro_usd: int
    cache_hit_pct: int
    cost_share_pct: int  # of the whole window's priced spend, all tools
    retries_per_100_turns_tenths: int
    interruptions_per_100_turns_tenths: int
    rework_per_100_tool_calls_tenths: int
    command_fail_pct: int


@dataclass(frozen=True, slots=True)
class ReceiptsWindow:
    """The Advisor's evidence base for one trailing window."""

    end_day: date
    window_days: int
    total_cost_micro_usd: int
    total_assistant_turns: int
    total_unpriced_turns: int
    families: tuple[FamilyEconomics, ...]  # sorted by (tool, family)


@dataclass(slots=True)
class _Accumulator:
    days: set[str]
    assistant_turns: int = 0
    unpriced_turns: int = 0
    cost_micro_usd: int = 0
    tokens: TokenCounts = field(default_factory=TokenCounts)
    tool_calls: int = 0
    retries: int = 0
    interruptions: int = 0
    rework_edits: int = 0
    commands_run: int = 0
    commands_failed: int = 0
    commands_slow: int = 0
    compactions: int = 0


def _per_100_tenths(numerator: int, denominator: int) -> int:
    """Rate per 100 units in tenths (one renderable decimal), 0 on empty."""
    return numerator * 1000 // denominator if denominator > 0 else 0


def _pct(numerator: int, denominator: int) -> int:
    return numerator * 100 // denominator if denominator > 0 else 0


def build_receipts(
    day_rows: list[tuple[str, str, str, list[int]]],
    end_day: date,
    window_days: int = RECEIPTS_WINDOW_DAYS,
) -> ReceiptsWindow:
    """Pure aggregation of day-prefixed usage rows (the ``usage_rows_by_day``
    layout) into per-(tool, family) economics. Rows outside the window are
    ignored so callers may pass a broader range. Deterministic: output order
    and every figure depend only on the input rows."""
    start_key = (end_day - timedelta(days=window_days - 1)).isoformat()
    end_key = end_day.isoformat()
    accumulators: dict[tuple[str, str], _Accumulator] = {}

    for day, tool, model, values in day_rows:
        if not start_key <= day <= end_key:
            continue
        row = ContributionRow.from_values(values)
        family = model_family(model).value
        acc = accumulators.setdefault(
            (tool, family), _Accumulator(days=set())
        )
        acc.days.add(day)
        acc.assistant_turns += row.assistant_turns
        acc.unpriced_turns += row.unpriced_turns
        acc.cost_micro_usd += row.cost_micro_usd
        acc.tokens = acc.tokens.add(
            TokenCounts(
                input=row.input_tokens,
                output=row.output_tokens,
                cached=row.cached_tokens,
                cache_creation=row.cache_creation_tokens,
                reasoning=row.reasoning_tokens,
                cache_creation_1h=row.cache_creation_1h_tokens,
            )
        )
        acc.tool_calls += row.tool_calls
        acc.retries += row.retries
        acc.interruptions += row.interruptions
        acc.rework_edits += row.rework_edits
        acc.commands_run += row.commands_run
        acc.commands_failed += row.commands_failed
        acc.commands_slow += row.commands_slow
        acc.compactions += row.compactions

    total_cost = sum(acc.cost_micro_usd for acc in accumulators.values())
    total_turns = sum(acc.assistant_turns for acc in accumulators.values())
    total_unpriced = sum(acc.unpriced_turns for acc in accumulators.values())

    families: list[FamilyEconomics] = []
    for (tool, family), acc in sorted(accumulators.items()):
        priced_turns = acc.assistant_turns - acc.unpriced_turns
        prompt_tokens = (
            acc.tokens.input + acc.tokens.cached + acc.tokens.cache_creation
        )
        families.append(
            FamilyEconomics(
                tool=tool,
                family=family,
                premium=family in PREMIUM_FAMILIES,
                confident=(
                    acc.assistant_turns >= RECEIPTS_MIN_TURNS
                    and len(acc.days) >= RECEIPTS_MIN_ACTIVE_DAYS
                ),
                active_days=len(acc.days),
                assistant_turns=acc.assistant_turns,
                priced_turns=priced_turns,
                unpriced_turns=acc.unpriced_turns,
                cost_micro_usd=acc.cost_micro_usd,
                tokens=acc.tokens,
                tool_calls=acc.tool_calls,
                retries=acc.retries,
                interruptions=acc.interruptions,
                rework_edits=acc.rework_edits,
                commands_run=acc.commands_run,
                commands_failed=acc.commands_failed,
                commands_slow=acc.commands_slow,
                compactions=acc.compactions,
                prompt_tokens_per_turn=(
                    prompt_tokens // acc.assistant_turns
                    if acc.assistant_turns > 0
                    else 0
                ),
                output_tokens_per_turn=(
                    acc.tokens.output // acc.assistant_turns
                    if acc.assistant_turns > 0
                    else 0
                ),
                cost_per_priced_turn_micro_usd=(
                    acc.cost_micro_usd // priced_turns if priced_turns > 0 else 0
                ),
                cache_hit_pct=_pct(acc.tokens.cached, prompt_tokens),
                cost_share_pct=_pct(acc.cost_micro_usd, total_cost),
                retries_per_100_turns_tenths=_per_100_tenths(
                    acc.retries, acc.assistant_turns
                ),
                interruptions_per_100_turns_tenths=_per_100_tenths(
                    acc.interruptions, acc.assistant_turns
                ),
                rework_per_100_tool_calls_tenths=_per_100_tenths(
                    acc.rework_edits, acc.tool_calls
                ),
                command_fail_pct=_pct(acc.commands_failed, acc.commands_run),
            )
        )

    return ReceiptsWindow(
        end_day=end_day,
        window_days=window_days,
        total_cost_micro_usd=total_cost,
        total_assistant_turns=total_turns,
        total_unpriced_turns=total_unpriced,
        families=tuple(families),
    )


def gather_receipts(
    store: Store, end_day: date, window_days: int = RECEIPTS_WINDOW_DAYS
) -> ReceiptsWindow:
    """The report-time entry point: one trailing window from persisted
    history only (the ``window_inputs_from_store`` pattern)."""
    start = end_day - timedelta(days=window_days - 1)
    return build_receipts(
        store.usage_rows_by_day(start.isoformat(), end_day.isoformat()),
        end_day,
        window_days,
    )
