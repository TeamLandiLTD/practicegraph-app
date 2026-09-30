"""Period aggregate construction and the offline emit queue driver
(FR-EMT-1/2). Payloads are built from the local daily history (M1). Nothing in
this module runs unless consent is ON; callers gate on
:func:`practicegraph.consent.read_consent` (FR-CNS-1)."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

from practicegraph import __version__
from practicegraph.analysis.insights import maturity_signals, work_type_mix
from practicegraph.analysis.ratecard import RATE_CARD_VERSION, active_rate_card
from practicegraph.config import Config
from practicegraph.events import EngagementCounter
from practicegraph.history import snapshot_for_day
from practicegraph.sources import SourceHealthRow
from practicegraph.store import Store
from practicegraph.transport import post_emit
from practicegraph.wire import (
    AGENT_HEALTH_KEYS,
    EMIT_SCHEMA_VERSION,
    ModelFamily,
    model_family,
    validate_emit,
)

# Only recently completed days are queued; older history never leaves the
# machine retroactively (named constant, §0 spirit).
EMIT_BACKFILL_DAYS = 7

# Retry backoff per attempt, capped (FR-EMT-2).
BACKOFF_SCHEDULE_S: tuple[int, ...] = (60, 240, 960, 3840, 15360, 21600)

SYNTHETIC_EXAMPLE_LABEL = "SYNTHETIC EXAMPLE - no emit is queued; values are illustrative"


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def build_payload_for_day(
    store: Store,
    day: str,
    org_id: str,
    engagement: dict[str, int],
    emit_id: str,
    platform: str,
    source_health: list[SourceHealthRow],
) -> dict[str, object]:
    """Build the closed FR-EMT-1 aggregate for one completed UTC day from the
    local history. Raw model strings never cross the wire — they collapse to
    the closed family enum (INV-3)."""
    rows = store.day_usage_rows(day)
    tokens_total = dict.fromkeys(
        ("input", "output", "cached", "cache_creation", "reasoning"), 0
    )
    totals = dict.fromkeys(
        ("assistant_turns", "user_turns", "tool_calls", "retries", "interruptions",
         "cost", "unpriced"), 0
    )
    spend_by_family: dict[str, int] = {}
    tools_observed: set[str] = set()
    for tool, model, values in rows:
        tools_observed.add(tool)
        totals["assistant_turns"] += values[0]
        totals["user_turns"] += values[1]
        tokens_total["input"] += values[2]
        tokens_total["output"] += values[3]
        tokens_total["cached"] += values[4]
        tokens_total["cache_creation"] += values[5]
        tokens_total["reasoning"] += values[6]
        totals["tool_calls"] += values[7]
        totals["retries"] += values[8]
        totals["interruptions"] += values[9]
        totals["cost"] += values[10]
        totals["unpriced"] += values[11]
        if values[10] > 0:
            family = model_family(model).value
            spend_by_family[family] = spend_by_family.get(family, 0) + values[10]

    health_totals = dict.fromkeys(AGENT_HEALTH_KEYS, 0)
    for row in source_health:
        health_totals["seen"] += row.health.seen
        health_totals["parsed"] += row.health.parsed
        health_totals["skipped"] += row.health.skipped
        health_totals["malformed"] += row.health.malformed
        health_totals["unknown_field"] += row.health.unknown_field
        health_totals["unsupported"] += row.health.unsupported
        if row.health.seen > 0:
            health_totals["sources_detected"] += 1

    engagement_out = {
        counter.value: max(0, engagement.get(counter.value, 0))
        for counter in EngagementCounter
    }

    # v2 closed fields: structural work-type mix and maturity signal levels.
    # Neither carries focus data (NFR-PRV-6) — pinned by the wire tests.
    day_date = date.fromisoformat(day)
    work_types = work_type_mix(store.marks_for_day(day))
    active_7 = len(
        store.active_days((day_date - timedelta(days=6)).isoformat(), day)
    )
    day_snapshot = snapshot_for_day(store, day_date, [])
    maturity = {
        signal: level.value
        for signal, level in maturity_signals(day_snapshot, active_7)
    }

    return {
        "schema_version": EMIT_SCHEMA_VERSION,
        "emit_id": emit_id,
        "org_id": org_id,
        "day": day,
        "platform": platform,
        "agent_version": __version__,
        "rate_card_version": active_rate_card().version,
        "is_estimate": True,
        "contributors": 1,
        "sessions": store.session_count_for_day(day),
        "assistant_turns": totals["assistant_turns"],
        "user_turns": totals["user_turns"],
        "tool_calls": totals["tool_calls"],
        "retries": totals["retries"],
        "interruptions": totals["interruptions"],
        "tokens": tokens_total,
        "estimated_cost_micro_usd": totals["cost"],
        "unpriced_turns": totals["unpriced"],
        "spend_by_family": dict(sorted(spend_by_family.items())),
        "tools_observed": sorted(tools_observed),
        "engagement": engagement_out,
        "agent_health": health_totals,
        "work_type_sessions": work_types,
        "maturity": maturity,
        "content_present": False,
        "identity_present": False,
    }


def synthetic_example_payload() -> dict[str, object]:
    """A stable, clearly-synthetic payload for the Privacy Center preview when
    nothing is queued (FR-RPT-5)."""
    return {
        "schema_version": EMIT_SCHEMA_VERSION,
        "emit_id": "00000000-0000-4000-8000-000000000000",
        "org_id": "example-org",
        "day": "1970-01-01",
        "platform": "windows",
        "agent_version": __version__,
        "rate_card_version": RATE_CARD_VERSION,
        "is_estimate": True,
        "contributors": 1,
        "sessions": 2,
        "assistant_turns": 5,
        "user_turns": 4,
        "tool_calls": 3,
        "retries": 0,
        "interruptions": 1,
        "tokens": {"input": 1000, "output": 500, "cached": 4000, "cache_creation": 0,
                   "reasoning": 100},
        "estimated_cost_micro_usd": 12500,
        "unpriced_turns": 0,
        "spend_by_family": {ModelFamily.CLAUDE_SONNET.value: 12500},
        "tools_observed": ["claude_code"],
        "engagement": {
            "report_generated": 1,
            "tip_shown": 0,
            "tip_acted": 0,
            "recommendation_dismissed": 0,
            "recommendation_never_suggest": 0,
            "engagement_unavailable": 0,
        },
        "agent_health": {
            "seen": 40, "parsed": 38, "skipped": 2, "malformed": 0,
            "unknown_field": 0, "unsupported": 0, "sources_detected": 1,
        },
        "work_type_sessions": {"build": 1, "investigate": 0, "converse": 1,
                               "unknown": 0},
        "maturity": {
            "cache_reuse": "developing",
            "tool_usage": "emerging",
            "multi_tool": "emerging",
            "consistency": "emerging",
        },
        "content_present": False,
        "identity_present": False,
    }


def queueable_days(store: Store, now: datetime) -> list[str]:
    """Completed UTC days with activity, within the backfill window, not yet
    claimed by the queue."""
    today = now.astimezone(UTC).date()
    horizon = (today - timedelta(days=EMIT_BACKFILL_DAYS)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    return [
        day
        for day in store.active_days(horizon, yesterday)
        if not store.day_queued(day)
    ]


def build_and_queue(
    store: Store,
    config: Config,
    now: datetime,
    source_health: list[SourceHealthRow],
) -> dict[str, int]:
    """Queue aggregates for every queueable day. Payloads must pass the wire
    validator before they may touch the queue — a failing payload is a bug,
    counted and skipped, never sent (fail-open, NFR-REL-1)."""
    org_id = config.org_id or "unconfigured-org"
    queued = 0
    invalid = 0
    for day in queueable_days(store, now):
        payload = build_payload_for_day(
            store=store,
            day=day,
            org_id=org_id,
            engagement=store.engagement_for_day(day),
            emit_id=str(uuid.uuid4()),
            platform=current_platform(),
            source_health=source_health,
        )
        if validate_emit(payload):
            invalid += 1
            continue
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        if store.queue_day(day, str(payload["emit_id"]), payload_json, now):
            queued += 1
    return {"queued": queued, "invalid": invalid}


def flush_queue(
    store: Store, config: Config, now: datetime, org_token: str
) -> dict[str, int]:
    """Send due emits. Version rejection is terminal (dead_letter); every other
    failure schedules a capped-backoff retry (FR-EMT-2/3)."""
    if not config.api_base_url or not org_token:
        return {"skipped_unconfigured": 1}
    outcomes = {"sent": 0, "retry_wait": 0, "dead_letter": 0}
    for row in store.due_emits(now):
        result = post_emit(config.api_base_url, org_token, row.payload_json)
        if result.ok:
            store.mark_sent(row.row_id, now)
            outcomes["sent"] += 1
        elif result.error_code == "schema_version_not_supported":
            store.mark_dead_letter(row.row_id, result.error_code)
            outcomes["dead_letter"] += 1
        else:
            attempts = row.attempts + 1
            delay_index = min(attempts - 1, len(BACKOFF_SCHEDULE_S) - 1)
            next_at = now + timedelta(seconds=BACKOFF_SCHEDULE_S[delay_index])
            store.mark_retry(
                row.row_id, result.error_code or "transport_error", next_at, attempts
            )
            outcomes["retry_wait"] += 1
    return outcomes
