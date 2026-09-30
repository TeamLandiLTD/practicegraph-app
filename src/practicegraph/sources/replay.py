"""Conservative Codex fork replay detection. Nothing here retains transcript text.

Only an explicitly linked parent's identical leading records can be excluded.
Timestamps are ignored because forks can rewrite them. A mismatch ends matching;
we never search later records or discard work based on a time threshold.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice
from pathlib import Path

from practicegraph.sources import iter_jsonl_lines


@dataclass(frozen=True)
class ReplayPlan:
    parent_id: str = ""
    relation: str = ""
    records: int = 0
    status: str = "not_forked"

    def version(self, parser_version: int) -> int:
        if not self.parent_id:
            return parser_version
        digest = hashlib.sha256(repr(self).encode()).hexdigest()[:12]
        return (parser_version << 48) + int(digest, 16)


def _fingerprint(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns


@lru_cache(maxsize=4096)
def _header(fingerprint: tuple[str, int, int]) -> tuple[str, str, str]:
    for line in islice(iter_jsonl_lines(Path(fingerprint[0])), 8):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        session = payload.get("id") or payload.get("session_id")
        parent = payload.get("forked_from_id")
        relation = "fork" if isinstance(parent, str) and parent else ""
        source = payload.get("source")
        # Provider-declared delegation is different from a UI fork.
        subagent = source.get("subagent") if isinstance(source, dict) else None
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        if isinstance(spawn, dict) and isinstance(spawn.get("parent_thread_id"), str):
            parent, relation = spawn["parent_thread_id"], "delegation"
        return (
            session if isinstance(session, str) else "",
            parent if isinstance(parent, str) else "",
            relation,
        )
    return "", "", ""


def _records(path: Path) -> Iterator[tuple[bytes, bool] | None]:
    for line in iter_jsonl_lines(path):
        try:
            record = json.loads(line)
        except ValueError:
            yield None  # malformed input cannot establish a replay match
            continue
        if not isinstance(record, dict):
            yield None
        elif record.get("type") != "session_meta":
            # Hash transiently; no prompts, outputs or commands survive.
            value = {key: value for key, value in record.items() if key != "timestamp"}
            payload = record.get("payload")
            # Observed Codex fork serialization adds content:null to older
            # reasoning items. Absent and explicit null carry no content.
            if (isinstance(payload, dict) and payload.get("type") == "reasoning"
                    and payload.get("content") is None):
                value["payload"] = {key: item for key, item in payload.items() if key != "content"}
            is_usage = (
                record.get("type") == "event_msg"
                and isinstance(payload, dict)
                and payload.get("type") == "token_count"
            )
            yield hashlib.sha256(json.dumps(value, sort_keys=True).encode()).digest(), is_usage


@lru_cache(maxsize=2048)
def _prefix(child: tuple[str, int, int], parent: tuple[str, int, int]) -> int:
    confirmed = 0
    pairs = zip(_records(Path(child[0])), _records(Path(parent[0])), strict=False)
    for count, (left, right) in enumerate(pairs, 1):
        if left is None or left != right:
            break
        if left[1]:
            confirmed = count
    # Keep trailing tool calls until a usage record confirms their ownership.
    return confirmed


def prepare(paths: list[Path]) -> dict[Path, ReplayPlan]:
    headers: dict[Path, tuple[str, str, str]] = {}
    fingerprints: dict[Path, tuple[str, int, int]] = {}
    by_session: dict[str, list[Path]] = {}
    for path in paths:
        try:
            fingerprints[path] = _fingerprint(path)
            headers[path] = _header(fingerprints[path])
        except OSError:
            continue
        if headers[path][0]:
            by_session.setdefault(headers[path][0], []).append(path)
    plans = {}
    for path, (session, parent, relation) in headers.items():
        ancestor = parent
        seen = {session}
        cyclic = False
        while ancestor and len(by_session.get(ancestor, [])) == 1:
            if ancestor in seen:
                cyclic = True
                break
            seen.add(ancestor)
            ancestor = headers[by_session[ancestor][0]][1]
        if not parent:
            plans[path] = ReplayPlan()
        elif relation == "delegation":
            plans[path] = ReplayPlan(parent, relation, status="not_forked")
        elif cyclic or parent == session or len(by_session.get(parent, [])) != 1:
            plans[path] = ReplayPlan(parent, relation, status="parent_unavailable")
        else:
            try:
                count = _prefix(fingerprints[path], fingerprints[by_session[parent][0]])
                plans[path] = ReplayPlan(
                    parent, relation, count, "matched_prefix" if count else "unmatched_prefix"
                )
            except OSError:
                plans[path] = ReplayPlan(parent, relation, status="parent_unavailable")
    return plans
