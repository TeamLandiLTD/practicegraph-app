"""Local, count-only transcript composition. No text, paths or tool names retained.

Character counts describe visible transcript material, not a tokenizer or the
provider's current context. Hidden instructions, images and tool schemas are not
measurable here. Provider input usage is kept separately, never used to rescale
these estimates into a falsely exact allocation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from practicegraph.sources import iter_jsonl_lines, parse_timestamp

CATEGORIES = ("instructions", "user", "assistant", "tools", "other")


def _length(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (dict, list)):
        return len(json.dumps(value, ensure_ascii=False))
    return 0


def _content(value: object, role: str) -> dict[str, int]:
    result = dict.fromkeys(CATEGORIES, 0)
    category = role if role in CATEGORIES else "other"
    if isinstance(value, str):
        result[category] += len(value)
    elif isinstance(value, list):
        for block in value:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind in ("tool_use", "tool_result"):
                result["tools"] += _length(block.get("input") or block.get("content"))
            elif kind in ("text", "input_text", "output_text"):
                result[category] += _length(block.get("text"))
            elif kind in ("thinking", "reasoning"):
                result["assistant"] += _length(block.get("thinking") or block.get("text"))
    return result


def summarize(path: Path, source_id: str) -> dict[str, Any]:
    counts = dict.fromkeys(CATEGORIES, 0)
    # Only identifiers and integer counts are held for streaming last-wins.
    slots: dict[str, dict[str, int]] = {}
    latest_input: int | None = None
    observed_at = ""
    compactions = 0
    for line in iter_jsonl_lines(path):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        timestamp = parse_timestamp(record.get("timestamp"))
        if timestamp:
            observed_at = max(observed_at, timestamp.isoformat())
        values = dict.fromkeys(CATEGORIES, 0)
        slot = ""
        if source_id == "codex_cli":
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record.get("type") == "compacted":
                compactions += 1
            if record.get("type") == "response_item":
                if payload.get("type") == "message":
                    role = payload.get("role")
                    role = "instructions" if role in ("system", "developer") else role
                    values = _content(payload.get("content"), str(role))
                elif payload.get("type") in ("function_call", "custom_tool_call"):
                    values["tools"] = _length(payload.get("arguments") or payload.get("input"))
                elif payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                    values["tools"] = _length(payload.get("output"))
            if record.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                usage = info.get("last_token_usage") if isinstance(info, dict) else None
                if isinstance(usage, dict):
                    candidate = usage.get("input_tokens")
                    if (
                        isinstance(candidate, int)
                        and not isinstance(candidate, bool)
                        and candidate >= 0
                    ):
                        latest_input = candidate
        else:
            if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
                compactions += 1
            message = record.get("message")
            if not isinstance(message, dict) or record.get("type") not in ("user", "assistant"):
                continue
            values = _content(message.get("content"), str(record.get("type")))
            if isinstance(message.get("id"), str):
                slot = message["id"] + ":" + str(record.get("requestId", ""))
            elif isinstance(record.get("uuid"), str):
                slot = record["uuid"]
            usage = message.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
                latest_input = sum(
                    max(0, v)
                    for key in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                    if isinstance(v := usage.get(key), int) and not isinstance(v, bool)
                )
        if slot:
            previous = slots.get(slot, {})
            for category in CATEGORIES:
                counts[category] -= previous.get(category, 0)
            slots[slot] = values
        for category in CATEGORIES:
            counts[category] += values[category]
    return {
        "characters": counts,
        "latest_input_tokens": latest_input,
        "observed_at": observed_at,
        "compactions": compactions,
        "basis": "visible_transcript",
        "includes_inherited_history": source_id == "codex_cli",
    }
