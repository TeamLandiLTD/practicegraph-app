"""Validate a Codex-authored playbook edition before the normal content release gate."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from practicegraph.analysis.harness_playbooks import parse_playbooks
from practicegraph.catalog_crypto import MAX_ENVELOPE_BYTES, open_catalog
from practicegraph.content import MAX_CONTENT_BYTES


def load(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_ENVELOPE_BYTES + 1)
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("edition exceeds the 512 KB limit")
    value = open_catalog(json.loads(raw), "harness-playbooks")
    if len(json.dumps(value).encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("edition exceeds the 512 KB limit")
    if not isinstance(value, dict) or parse_playbooks(value) is None:
        raise ValueError("edition does not match the harness-playbooks/1 contract")
    return value


def validate(path: Path, previous: Path | None = None, *, today: date | None = None) -> int:
    document = load(path)
    today = today or datetime.now(UTC).date()
    if date.fromisoformat(document["published_on"]) > today:
        raise ValueError("publication date is in the future")
    if previous is not None:
        prior = load(previous)
        if prior != document and prior["playbooks_version"] == document["playbooks_version"]:
            raise ValueError("changed edition requires a new playbooks_version")
        old = {entry["id"]: entry for entry in prior["entries"]}
        for entry in document["entries"]:
            before = old.get(entry["id"])
            if before and entry != before and entry["revision"] <= before["revision"]:
                raise ValueError("changed entry requires a higher revision")
        # Withdrawal remains visible to clients and to the editorial audit.
        if set(old) - {entry["id"] for entry in document["entries"]}:
            raise ValueError("retain removed entries with status withdrawn")
    return len(document["entries"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--against", type=Path, help="Previously approved edition")
    args = parser.parse_args()
    try:
        count = validate(args.draft, args.against)
        print(
            f"Validated {count} playbook entries. "
            "Validation does not approve or publish an edition."
        )
        return 0
    except (OSError, ValueError, RecursionError) as error:
        print(f"Playbook validation refused: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
