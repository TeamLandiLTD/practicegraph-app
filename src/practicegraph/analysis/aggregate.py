"""Daily aggregation of TurnEvents into a renderable snapshot (FR-ANL-1,
FR-ANL-7 groundwork). Deterministic: rows and health lanes are sorted."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from practicegraph.analysis.ratecard import estimate_cost_micro_usd
from practicegraph.events import TokenCounts, TurnEvent, TurnKind
from practicegraph.sources import SourceHealthRow


@dataclass(frozen=True, slots=True)
class UsageRow:
    """Per (tool, model) usage for one UTC day."""

    tool: str
    model: str
    assistant_turns: int
    user_turns: int
    tokens: TokenCounts
    tool_calls: int
    retries: int
    interruptions: int
    cost_micro_usd: int  # priced assistant turns only
    unpriced_turns: int  # assistant turns with no rate-card entry
    # P1 execution-quality counters (append-only, defaults keep old callers).
    rework_edits: int = 0
    commands_run: int = 0
    commands_failed: int = 0
    commands_slow: int = 0


@dataclass(frozen=True, slots=True)
class DailySnapshot:
    day: date
    rows: tuple[UsageRow, ...]
    total_cost_micro_usd: int
    total_unpriced_turns: int
    total_assistant_turns: int
    total_user_turns: int
    total_tool_calls: int
    total_retries: int
    total_interruptions: int
    session_count: int
    source_health: tuple[SourceHealthRow, ...]


@dataclass(slots=True)
class _RowAccumulator:
    assistant_turns: int = 0
    user_turns: int = 0
    tokens: TokenCounts = field(default_factory=TokenCounts)
    tool_calls: int = 0
    retries: int = 0
    interruptions: int = 0
    cost_micro_usd: int = 0
    unpriced_turns: int = 0
    rework_edits: int = 0
    commands_run: int = 0
    commands_failed: int = 0
    commands_slow: int = 0


def build_daily_snapshot(
    events: list[TurnEvent],
    source_health: list[SourceHealthRow],
    day: date,
) -> DailySnapshot:
    accumulators: dict[tuple[str, str], _RowAccumulator] = {}
    sessions: set[tuple[str, str]] = set()

    for event in events:
        if event.day_key() != day:
            continue
        sessions.add((event.source_id, event.session_id))
        acc = accumulators.setdefault((event.tool.value, event.model), _RowAccumulator())
        acc.tokens = acc.tokens.add(event.tokens)
        acc.tool_calls += event.tool_calls
        acc.retries += event.retries
        acc.interruptions += event.interruptions
        acc.rework_edits += event.rework_edits
        acc.commands_run += event.commands_run
        acc.commands_failed += event.commands_failed
        acc.commands_slow += event.commands_slow
        if event.kind is TurnKind.ASSISTANT_TURN:
            acc.assistant_turns += 1
            cost = estimate_cost_micro_usd(event.tokens, event.model)
            if cost is None:
                acc.unpriced_turns += 1
            else:
                acc.cost_micro_usd += cost
        else:
            acc.user_turns += 1

    rows = tuple(
        UsageRow(
            tool=tool,
            model=model,
            assistant_turns=acc.assistant_turns,
            user_turns=acc.user_turns,
            tokens=acc.tokens,
            tool_calls=acc.tool_calls,
            retries=acc.retries,
            interruptions=acc.interruptions,
            cost_micro_usd=acc.cost_micro_usd,
            unpriced_turns=acc.unpriced_turns,
            rework_edits=acc.rework_edits,
            commands_run=acc.commands_run,
            commands_failed=acc.commands_failed,
            commands_slow=acc.commands_slow,
        )
        for (tool, model), acc in sorted(accumulators.items())
    )

    return DailySnapshot(
        day=day,
        rows=rows,
        total_cost_micro_usd=sum(row.cost_micro_usd for row in rows),
        total_unpriced_turns=sum(row.unpriced_turns for row in rows),
        total_assistant_turns=sum(row.assistant_turns for row in rows),
        total_user_turns=sum(row.user_turns for row in rows),
        total_tool_calls=sum(row.tool_calls for row in rows),
        total_retries=sum(row.retries for row in rows),
        total_interruptions=sum(row.interruptions for row in rows),
        session_count=len(sessions),
        source_health=tuple(sorted(source_health, key=lambda row: row.source_id)),
    )
