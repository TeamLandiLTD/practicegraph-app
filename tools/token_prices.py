"""Build a bounded production feed from a verified private price snapshot."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from practicegraph.analysis.token_price_contract import validate
from practicegraph.analysis.token_prices import MAX_FEED_BYTES as MAX_CONTENT_BYTES
from practicegraph.analysis.token_prices import (
    MAX_HISTORY,
    check_transition,
    parse_token_prices,
    snapshot,
)
from practicegraph.catalog_crypto import MAX_ENVELOPE_BYTES, open_catalog
from practicegraph.content import canonical_bytes


def read(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_ENVELOPE_BYTES + 1)
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("input too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def build(current: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    current = copy.deepcopy(current)
    validate(current)
    if any(
        o["pricing"]["input"] is None or o["pricing"]["output"] is None
        for o in current["offers"]
    ):
        raise ValueError(
            "current offers require sourced input and output prices; "
            "keep unpriced research in coverage notes"
        )
    priced_models = {o["model_id"] for o in current["offers"]}
    if any(m["id"] not in priced_models for m in current["models"]):
        raise ValueError("current models must have at least one priced offer")
    if previous is not None and parse_token_prices(previous) is None:
        raise ValueError("invalid previous production edition")
    if previous is not None and snapshot(previous) == current:
        return previous
    history = [*previous["history"], snapshot(previous)] if previous else []
    edition = {
        **current,
        "schema": current["schema"].replace(".research/", "/"),
        "history": history[-MAX_HISTORY:],
    }
    if parse_token_prices(edition) is None:
        raise ValueError("invalid production edition or history")
    if previous is not None:
        check_transition(edition, previous)
    # Keep the newest comparison intact; older immutable editions remain archived.
    while len(canonical_bytes(edition)) > MAX_CONTENT_BYTES and len(edition["history"]) > 1:
        edition["history"].pop(0)
    if len(canonical_bytes(edition)) > MAX_CONTENT_BYTES:
        raise ValueError("feed exceeds 4 MiB; reduce selected coverage, preserve private archive")
    return edition


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    previous = open_catalog(read(args.previous), "token-prices") if args.previous else None
    document = build(read(args.snapshot), previous)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as stream:
        stream.write(canonical_bytes(document))
    print(
        json.dumps(
            {
                "version": document["edition_version"],
                "offers": len(document["offers"]),
                "history_retained": len(document["history"]),
                "history_window": MAX_HISTORY,
                "published": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
