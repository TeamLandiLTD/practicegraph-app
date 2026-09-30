"""K-anonymous aggregate view-models (INV-4, NFR-PRV-3, FR-API-5).

Pure functions: storage rows in, JSON-able view-models out. Suppression is
applied here, server-side, before anything reaches an API response or the
dashboard renderer. Suppressed cells carry an explicit marker — they are
"unavailable", never zero, never interpolated (FR-DSH-2). Days with no data
at all are labeled distinctly ("no_data") so absence is never confused with
suppression.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from practicegraph.wire import (
    ENGAGEMENT_KEYS,
    MATURITY_LEVEL_VALUES,
    MATURITY_SIGNAL_KEYS,
    TOKEN_KEYS,
    TOOL_VALUES,
    WORK_TYPE_KEYS,
)

STATUS_OK = "ok"
STATUS_SUPPRESSED = "suppressed"
STATUS_NO_DATA = "no_data"

_DRIFT_KEYS = ("malformed", "unknown_field", "unsupported")


def _day_range(from_day: str, to_day: str) -> list[str]:
    start = date.fromisoformat(from_day)
    end = date.fromisoformat(to_day)
    days: list[str] = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _sum_int(payloads: list[dict[str, Any]], field: str) -> int:
    return sum(int(p.get(field, 0)) for p in payloads)


def _sum_nested(
    payloads: list[dict[str, Any]], field: str, keys: tuple[str, ...]
) -> dict[str, int]:
    out = dict.fromkeys(keys, 0)
    for payload in payloads:
        nested = payload.get(field)
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key, 0)
                if isinstance(value, int):
                    out[key] += value
    return out


def _sum_spend_by_family(payloads: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for payload in payloads:
        spend = payload.get("spend_by_family")
        if isinstance(spend, dict):
            for family, value in spend.items():
                if isinstance(value, int):
                    out[family] = out.get(family, 0) + value
    return dict(sorted(out.items()))


def _tool_coverage(payloads: list[dict[str, Any]], k: int) -> dict[str, object]:
    """Contributors observing each tool. Sub-threshold counts are suppressed:
    even inside a displayable day, a small subgroup must not be enumerable."""
    coverage: dict[str, object] = {}
    for tool in TOOL_VALUES:
        observers = sum(
            1
            for payload in payloads
            if isinstance(payload.get("tools_observed"), list)
            and tool in payload["tools_observed"]
        )
        if observers == 0:
            coverage[tool] = 0
        elif observers < k:
            coverage[tool] = STATUS_SUPPRESSED
        else:
            coverage[tool] = observers
    return coverage


def _maturity_counts(payloads: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Raw contributor counts per level per signal — only from payloads that
    carry the v2 field (v1 agents simply do not contribute here). Internal:
    nothing below k-suppression may leave this module."""
    counts: dict[str, dict[str, int]] = {
        signal: dict.fromkeys(MATURITY_LEVEL_VALUES, 0)
        for signal in MATURITY_SIGNAL_KEYS
    }
    for payload in payloads:
        maturity = payload.get("maturity")
        if not isinstance(maturity, dict):
            continue
        for signal in MATURITY_SIGNAL_KEYS:
            level = maturity.get(signal)
            if isinstance(level, str) and level in MATURITY_LEVEL_VALUES:
                counts[signal][level] += 1
    return counts


def _maturity_distribution(
    payloads: list[dict[str, Any]], k: int
) -> dict[str, dict[str, object]]:
    """Maturity buckets with sub-threshold counts suppressed — the SAME rule
    `_tool_coverage` applies (FR-API-5): even inside a displayable day, a
    level held by fewer than k contributors must not be enumerable. Until
    2026-08-13 this took no k at all, so the JSON aggregate route returned
    buckets of size 1 that only the HTML renderer happened to blur — the
    guarantee lived in the wrong layer."""
    return {
        signal: {
            level: (value if value == 0 or value >= k else STATUS_SUPPRESSED)
            for level, value in levels.items()
        }
        for signal, levels in _maturity_counts(payloads).items()
    }


def _ok_day_cell(day: str, payloads: list[dict[str, Any]], k: int) -> dict[str, Any]:
    drift = dict.fromkeys(_DRIFT_KEYS, 0)
    for payload in payloads:
        health = payload.get("agent_health")
        if isinstance(health, dict):
            for key in _DRIFT_KEYS:
                value = health.get(key, 0)
                if isinstance(value, int):
                    drift[key] += value
    return {
        "day": day,
        "status": STATUS_OK,
        "contributors": len(payloads),
        "estimated_cost_micro_usd": _sum_int(payloads, "estimated_cost_micro_usd"),
        "unpriced_turns": _sum_int(payloads, "unpriced_turns"),
        "sessions": _sum_int(payloads, "sessions"),
        "assistant_turns": _sum_int(payloads, "assistant_turns"),
        "user_turns": _sum_int(payloads, "user_turns"),
        "tool_calls": _sum_int(payloads, "tool_calls"),
        "retries": _sum_int(payloads, "retries"),
        "interruptions": _sum_int(payloads, "interruptions"),
        "tokens": _sum_nested(payloads, "tokens", TOKEN_KEYS),
        "spend_by_family": _sum_spend_by_family(payloads),
        "tools": _tool_coverage(payloads, k),
        "drift": drift,
        "engagement": _sum_nested(payloads, "engagement", ENGAGEMENT_KEYS),
        "work_type_sessions": _sum_nested(
            payloads, "work_type_sessions", WORK_TYPE_KEYS
        ),
        "maturity_distribution": _maturity_distribution(payloads, k),
    }


def build_summary(
    payloads_by_day: dict[str, list[dict[str, Any]]],
    k_threshold: int,
    from_day: str,
    to_day: str,
) -> dict[str, Any]:
    """The dashboard view-model. Every cell is one of ok / suppressed / no_data."""
    days: list[dict[str, Any]] = []
    ok_payloads: list[dict[str, Any]] = []
    ok_days = 0
    for day in _day_range(from_day, to_day):
        payloads = payloads_by_day.get(day, [])
        if not payloads:
            days.append({"day": day, "status": STATUS_NO_DATA})
        elif len(payloads) < k_threshold:
            days.append({"day": day, "status": STATUS_SUPPRESSED})
        else:
            days.append(_ok_day_cell(day, payloads, k_threshold))
            ok_payloads.extend(payloads)
            ok_days += 1

    if ok_days == 0:
        has_any = any(payloads_by_day.get(day) for day in _day_range(from_day, to_day))
        totals: dict[str, Any] = {
            "status": STATUS_SUPPRESSED if has_any else STATUS_NO_DATA,
            "days_included": 0,
        }
    else:
        # Totals aggregate the raw counts over ok-day payloads, then apply
        # the same k-suppression once — never by summing already-suppressed
        # day cells, which would either break on markers or under-count.
        maturity_total = _maturity_distribution(ok_payloads, k_threshold)
        tokens_total_map = _sum_nested(ok_payloads, "tokens", TOKEN_KEYS)
        totals = {
            "status": STATUS_OK,
            "days_included": ok_days,
            "estimated_cost_micro_usd": _sum_int(ok_payloads, "estimated_cost_micro_usd"),
            "sessions": _sum_int(ok_payloads, "sessions"),
            "assistant_turns": _sum_int(ok_payloads, "assistant_turns"),
            "tool_calls": _sum_int(ok_payloads, "tool_calls"),
            # Total tokens across the window's ok days — so the JSON aggregate
            # endpoint carries it too (the live dashboard reads it from there;
            # the rendered page derives the same figure).
            "tokens": tokens_total_map,
            "tokens_total": sum(tokens_total_map.values()),
            "max_contributors": max(
                (cell["contributors"] for cell in days if cell["status"] == STATUS_OK),
                default=0,
            ),
            "spend_by_family": _sum_spend_by_family(ok_payloads),
            "engagement": _sum_nested(ok_payloads, "engagement", ENGAGEMENT_KEYS),
            "drift_total": sum(
                sum(cell["drift"].values()) for cell in days if cell["status"] == STATUS_OK
            ),
            "work_type_sessions": _sum_nested(
                ok_payloads, "work_type_sessions", WORK_TYPE_KEYS
            ),
            # Contributor-days per level per signal (v2 payloads only).
            "maturity_distribution": maturity_total,
        }

    return {
        "k_threshold": k_threshold,
        "from_day": from_day,
        "to_day": to_day,
        "days": days,
        "totals": totals,
        "estimates_note": "all_monetary_values_are_estimates",
    }


def build_coverage(
    payloads_by_day: dict[str, list[dict[str, Any]]],
    k_threshold: int,
    from_day: str,
    to_day: str,
) -> dict[str, Any]:
    """Source-coverage metadata (FR-API-1), same suppression rules."""
    days: list[dict[str, Any]] = []
    for day in _day_range(from_day, to_day):
        payloads = payloads_by_day.get(day, [])
        if not payloads:
            days.append({"day": day, "status": STATUS_NO_DATA})
        elif len(payloads) < k_threshold:
            days.append({"day": day, "status": STATUS_SUPPRESSED})
        else:
            days.append(
                {
                    "day": day,
                    "status": STATUS_OK,
                    "contributors": len(payloads),
                    "tools": _tool_coverage(payloads, k_threshold),
                }
            )
    return {
        "k_threshold": k_threshold,
        "from_day": from_day,
        "to_day": to_day,
        "days": days,
    }
