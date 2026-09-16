"""Provider quotes, dated history, and conservative comparison state."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from practicegraph.analysis.token_price_contract import (
    IDENTITY,
    need,
    summarize,
    timestamp,
    validate,
)

SCHEMA = "practicegraph.token-prices/2"
MAX_HISTORY = 12
# A full provider matrix (2026-09-15) runs to a few hundred offers; the feed is
# read once per pull interval, so the bound is about parser safety, not bandwidth.
MAX_FEED_BYTES = 4 * 1024 * 1024


def snapshot(document: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: v for k, v in document.items() if k != "history"},
        "schema": "practicegraph.token-prices.research/" + document["schema"].rsplit("/", 1)[-1],
    }


def check_transition(current: dict[str, Any], previous: dict[str, Any]) -> None:
    """Reject identity reuse, backward observations and rewritten retained editions."""
    summarize(snapshot(current), snapshot(previous))
    old = {s["edition_version"]: s for s in [*previous["history"], snapshot(previous)]}
    for row in [*current["history"], snapshot(current)]:
        if row["edition_version"] in old:
            need(row == old[row["edition_version"]], "retained history was rewritten")


def parse_token_prices(raw: object) -> dict[str, Any] | None:
    try:
        need(isinstance(raw, dict), "expected object")
        assert isinstance(raw, dict)
        need(
            raw.get("schema") in (SCHEMA, "practicegraph.token-prices/1"),
            "unsupported token-price schema",
        )
        size = len(json.dumps(raw, ensure_ascii=False).encode())
        need(size <= MAX_FEED_BYTES, "edition too large")
        history = raw["history"]
        need(isinstance(history, list) and len(history) <= MAX_HISTORY, "invalid history")
        current = snapshot(raw)
        validate(current)
        versions: set[str] = set()
        prior = None
        # Accumulated identities catch disappear/reappear reuse, not just adjacent editions.
        offers: dict[str, Any] = {}
        models: dict[str, Any] = {}
        for row in [*history, current]:
            validate(row)
            need(row["edition_version"] not in versions, "duplicate history version")
            versions.add(row["edition_version"])
            if prior is not None:
                need(
                    timestamp(row["observed_at"], "time") > timestamp(prior["observed_at"], "time"),
                    "history must be chronological",
                )
                summarize(row, prior)
            for model in row["models"]:
                if model["id"] in models:
                    need(
                        all(
                            model[k] == models[model["id"]][k]
                            for k in ("developer", "revision", "weights")
                        ),
                        "model identity reused",
                    )
            for offer in row["offers"]:
                old = offers.get(offer["id"])
                if old:
                    need(all(offer[k] == old[k] for k in IDENTITY), "offer identity reused")
                    need(
                        all(
                            offer["pricing"][k] == old["pricing"][k]
                            for k in ("currency", "unit", "cache_ttl_seconds", "reasoning_billing")
                        ),
                        "billing identity reused",
                    )
                    need(offer["observed_at"] >= old["observed_at"], "observation went backwards")
            offers.update({o["id"]: o for o in row["offers"]})
            models.update({m["id"]: m for m in row["models"]})
            prior = row
        return raw
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return None


def quote_state(offer: dict[str, Any], sources: dict[str, Any], now: datetime) -> str:
    if offer["effective_from"] and timestamp(offer["effective_from"], "start") > now:
        return "announced"
    if offer["effective_to"] and timestamp(offer["effective_to"], "end") <= now:
        return "expired"
    if offer["status"] not in ("available", "preview"):
        return str(offer["status"])
    checks = [timestamp(offer["observed_at"], "observed")]
    checks.extend(
        timestamp(sources[s]["checked_at"], "checked")
        for s in set(offer["pricing"]["source_ids"])
    )
    return "stale" if any((now - t).total_seconds() > 48 * 3600 for t in checks) else "current"


def reading(document: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    if document is None:
        return {"edition": None, "changes": [], "offer_states": {}}
    sources = {s["id"]: s for s in document["sources"]}
    prior = document["history"][-1] if document["history"] else None
    changes = summarize(snapshot(document), prior)["observed_quote_changes"]
    return {
        "edition": document,
        "changes": changes,
        "offer_states": {
            o["id"]: quote_state(o, sources, now.astimezone(UTC)) for o in document["offers"]
        },
    }
