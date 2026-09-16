"""Authenticated local usage investigation over retained counters only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from practicegraph.history import build_range_summary, spend_series
from practicegraph.sources.context import CATEGORIES
from practicegraph.store import ContributionRow, SessionRow, Store

PERIODS = {"today": 1, "7d": 7, "30d": 30, "90d": 90, "all": None}
PAGE_SIZE = 30


def session_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _evidence(store: Store) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key, body in store.session_evidence_rows():
        try:
            value = json.loads(body)
        except (ValueError, RecursionError):
            continue
        if (isinstance(value, dict) and isinstance(value.get("observed_at"), str)
                and value["observed_at"] >= result.get(key, {}).get("observed_at", "")):
            result[key] = value
    return result


def _number(value: object) -> int:
    return max(0, value) if type(value) is int else 0


def _session(row: SessionRow, evidence: dict[str, Any]) -> dict[str, Any]:
    fields = row._asdict()
    fields.pop("session_key")
    relation = evidence.get("relation")
    parent = evidence.get("parent_key")
    replay = evidence.get("replay_status")
    return {
        **fields,
        "id": session_id(row.session_key),
        "parent_id": session_id(parent) if isinstance(parent, str) and parent else None,
        "relation": relation if relation in ("fork", "delegation") else None,
        "replay_status": replay
        if replay in ("not_forked", "matched_prefix", "unmatched_prefix", "parent_unavailable")
        else "unknown",
        "replay_records": _number(evidence.get("replay_records")),
    }


def reading(
    store: Store,
    today: date,
    period: str = "7d",
    page: int = 0,
    selected: str = "",
) -> dict[str, Any]:
    if period not in PERIODS or not 0 <= page <= 100000:
        raise ValueError("invalid_usage_request")
    summary = build_range_summary(store, today, period, period, PERIODS[period])
    rows = store.sessions_between(summary.from_day, summary.to_day)
    evidence = _evidence(store)
    if selected:
        row = next((r for r in rows if session_id(r.session_key) == selected), None)
        if row is None:
            raise KeyError("session_not_in_period")
        value = evidence.get(row.session_key, {})
        characters = value.get("characters")
        characters = characters if isinstance(characters, dict) else {}
        context = [
            {
                "category": category,
                "characters": _number(characters.get(category)),
                "estimated_tokens": (_number(characters.get(category)) + 3) // 4,
            }
            for category in CATEGORIES
        ]
        observed = value.get("observed_at")
        # Parse rather than echo an arbitrary stored string.
        from practicegraph.sources import parse_timestamp

        timestamp = parse_timestamp(observed)
        latest_input = value.get("latest_input_tokens")
        return {
            "session": _session(row, value),
            "period": period,
            "context": {
                "available": bool(value),
                "basis": "visible_transcript",
                "observed_at": timestamp.isoformat() if timestamp else None,
                "components": context,
                "latest_input_tokens": latest_input
                if type(latest_input) is int and latest_input >= 0
                else None,
                "compactions": _number(value.get("compactions")),
            },
        }
    contributions = [
        ContributionRow.from_values(v)
        for _, _, v in store.usage_rows_between(summary.from_day, summary.to_day)
    ]
    cached = sum(r.cached_tokens for r in contributions)
    prompt = sum(r.input_tokens + r.cached_tokens + r.cache_creation_tokens for r in contributions)
    output = sum(r.output_tokens for r in contributions)
    unverified = set()
    edited = set()
    tests: dict[str, int] = {}
    for span in store.work_spans_between(summary.from_day, summary.to_day):
        tests[span[0]] = tests.get(span[0], 0) + span[13]
        if span[14] or span[17]:
            edited.add(span[0])
    unverified = {key for key in edited if not tests.get(key)}
    sessions = [_session(row, evidence.get(row.session_key, {})) for row in rows]
    by_id = {s["id"]: s for s in sessions}
    families: dict[str, list[str]] = {}
    for item in sessions:
        current = item
        visited = {item["id"]}
        while current["parent_id"]:
            parent = current["parent_id"]
            if parent in visited:  # corrupt/cyclic links cannot establish a family
                break
            visited.add(parent)
            if parent not in by_id:
                current = {"id": parent, "parent_id": None}
                break
            current = by_id[parent]
        families.setdefault(current["id"], []).append(item["id"])
    family_rows = []
    for root, members in families.items():
        if len(members) < 2 and not any(by_id[m]["parent_id"] for m in members):
            continue
        family_rows.append(
            {
                "id": root,
                "members": members,
                "parent_in_period": root in by_id,
                "cost_micro_usd": sum(by_id[m]["cost_micro_usd"] for m in members),
                "assistant_turns": sum(by_id[m]["assistant_turns"] for m in members),
                "unpriced_turns": sum(by_id[m]["unpriced_turns"] for m in members),
            }
        )
    family_rows.sort(key=lambda f: (-f["cost_micro_usd"], f["id"]))
    days = (today - date.fromisoformat(summary.from_day)).days + 1
    # Long retained histories get monthly buckets; avoid years of empty points.
    series = (
        [asdict(p) for p in spend_series(store, today, days)]
        if days <= 366
        else [
            {"day": day, "cost_micro_usd": cost, "tokens_total": None, "assistant_turns": None}
            for day, cost in summary.series
        ]
    )
    previous_end = date.fromisoformat(summary.from_day) - timedelta(days=1)
    previous = (
        None
        if period == "all"
        else build_range_summary(store, previous_end, period, period, PERIODS[period])
    )
    return {
        "schema": "practicegraph.usage/1",
        "period": period,
        "from_day": summary.from_day,
        "to_day": summary.to_day,
        "summary": {**asdict(summary), "sessions": len(rows)},
        "series": series,
        "previous": {
            "cost_micro_usd": previous.cost_micro_usd,
            "from_day": previous.from_day,
            "to_day": previous.to_day,
            # Same window length ending the day before this one: the ghost
            # series the trend draws behind the current bars, point for point.
            "series": [asdict(p) for p in spend_series(store, previous_end, days)]
            if days <= 366
            else None,
        }
        if previous
        else None,
        "drivers": {
            "cached_tokens": cached,
            "prompt_tokens": prompt,
            "output_tokens": output,
            "cache_pct": (100 * cached // prompt) if prompt else None,
            "reasoning_tokens": sum(r.reasoning_tokens for r in contributions),
            "compactions": sum(r.compactions for r in contributions),
            "edited_sessions": len(edited),
            "without_test_attempt": len(unverified),
        },
        "sessions": sessions[page * PAGE_SIZE : (page + 1) * PAGE_SIZE],
        "page": page,
        "page_size": PAGE_SIZE,
        "families": family_rows[:30],
        "unresolved_forks": sum(
            s["relation"] == "fork" and s["replay_status"] != "matched_prefix" for s in sessions
        ),
    }
