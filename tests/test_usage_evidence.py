"""Regression contracts for replay ownership, counted context, and local drill-down."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from practicegraph.analysis.usage import reading
from practicegraph.history import ingest, snapshot_for_day
from practicegraph.sources.codex import ADAPTER
from practicegraph.sources.context import summarize
from practicegraph.sources.replay import prepare
from practicegraph.store import Store

DAY = date(2026, 9, 7)
NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _records(session, parent="", n=2):
    meta = {"id": session, "cwd": "/home/PRIVATE/project"}
    if parent:
        meta["forked_from_id"] = parent
    records = [
        {"type": "session_meta", "payload": meta},
        {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
    ]
    for i in range(1, n + 1):
        records.extend(
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"PRIVATE prompt {i}"}],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "user_message", "message": f"PRIVATE prompt {i}"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "apply_patch",
                        "arguments": f"*** Update File: PRIVATE{i}.py\n+x",
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": i * 100,
                                "output_tokens": i * 20,
                                "reasoning_output_tokens": i * 5,
                            },
                            "last_token_usage": {
                                "input_tokens": 100,
                                "output_tokens": 20,
                                "reasoning_output_tokens": 5,
                            },
                        },
                        "rate_limits": {
                            "account_id": "PRIVATE_ACCOUNT",
                            "limit_id": "codex",
                            "primary": {
                                "used_percent": 30,
                                "window_minutes": 300,
                                "resets_at": 1788782400,
                            },
                            "secondary": {"used_percent": 40, "window_minutes": 10080},
                        },
                    },
                },
            ]
        )
    for i, r in enumerate(records):
        r["timestamp"] = f"2026-09-07T{'09' if parent else '08'}:{i:02}:00Z"
    return records


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_unpriced_model_counts_survive_usage_projection(tmp_path):
    records = _records("unknown-model")
    records[1]["payload"]["model"] = "unknown-synthetic-model"
    logs = tmp_path / "codex" / "sessions"
    _write(logs / "unpriced.jsonl", records)
    store = Store(tmp_path / "state.db")
    store.migrate()
    ingest({"PRACTICEGRAPH_CODEX_HOME": str(logs.parent),
            "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "empty")}, store, NOW)
    model = reading(store, DAY)["summary"]["models"][0]
    assert model["assistant_turns"] > 0
    assert model["unpriced_turns"] == model["assistant_turns"]
    assert model["cost_micro_usd"] == 0


def test_fork_replay_keeps_new_work_and_baseline_independent_of_file_order(tmp_path):
    parent = _write(tmp_path / "z-parent.jsonl", _records("parent"))
    child = _write(tmp_path / "a-child.jsonl", _records("child", "parent", 3))
    for paths in ([child, parent], [parent, child]):
        result = ADAPTER.parse_many(paths)
        assert sum(e.tokens.input + e.tokens.output for e in result.events) == 360
        assert sum(e.tool_calls for e in result.events) == 3
        assert sum(e.files_edited for e in result.events) == 3
        assert sum(e.human_initiated for e in result.events) == 3
        assert len(result.gauges) == 6
        assert {s.window_kind for s in result.quota_snapshots} == {"primary", "secondary"}
        assert all(s.account_hash and "PRIVATE" not in repr(s) for s in result.quota_snapshots)


def test_copied_session_header_and_null_reasoning_do_not_break_replay(tmp_path):
    originals = _records("parent")
    originals.insert(3, {"type": "response_item", "timestamp": "2026-09-07T08:02:30Z",
                         "payload": {"type": "reasoning", "encrypted_content": "opaque"}})
    parent = _write(tmp_path / "parent.jsonl", originals)
    child_records = _records("child", "parent", 3)
    child_records.insert(3, {"type": "response_item", "timestamp": "2026-09-07T09:02:30Z",
                             "payload": {"type": "reasoning", "encrypted_content": "opaque",
                                         "content": None}})
    child_records.insert(1, originals[0])
    child = _write(tmp_path / "child.jsonl", child_records)
    events = ADAPTER.parse_many([child, parent]).events
    assert sum(e.tokens.input + e.tokens.output for e in events) == 360
    assert sum(e.tokens.input for e in events if e.session_id == "child") == 100


def test_matching_totals_after_a_new_prompt_are_new_work(tmp_path):
    parent = _write(tmp_path / "parent.jsonl", _records("parent", n=3))
    records = _records("child", "parent", 3)
    records[-4]["payload"]["content"][0]["text"] = "A different task"
    child = _write(tmp_path / "child.jsonl", records)
    # Parent and child independently reach the same cumulative total, after divergence.
    assert (
        sum(e.tokens.input + e.tokens.output for e in ADAPTER.parse_many([parent, child]).events)
        == 480
    )


def test_missing_ambiguous_or_cyclic_parent_never_discards_usage(tmp_path):
    child = _write(tmp_path / "child.jsonl", _records("child", "parent", 3))
    assert prepare([child])[child].status == "parent_unavailable"
    parent = _write(tmp_path / "parent.jsonl", _records("parent", "child", 2))
    assert all(p.records == 0 for p in prepare([parent, child]).values())
    duplicate = _write(tmp_path / "duplicate.jsonl", _records("parent"))
    assert prepare([parent, child, duplicate])[child].records == 0


def test_ingest_recomputes_child_when_parent_appears_or_disappears(tmp_path):
    logs = tmp_path / "codex" / "sessions"
    child = _write(logs / "a-child.jsonl", _records("child", "parent", 3))
    env = {
        "PRACTICEGRAPH_CODEX_HOME": str(logs.parent),
        "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "empty"),
    }
    store = Store(tmp_path / "state.db")
    store.migrate()
    ingest(env, store, NOW)
    assert reading(store, DAY)["unresolved_forks"] == 1
    parent = _write(logs / "z-parent.jsonl", _records("parent"))
    for _ in range(2):
        ingest(env, store, NOW)
        usage = reading(store, DAY)
        assert usage["summary"]["tokens_total"] == 360  # reasoning is not added again
        assert usage["summary"]["assistant_turns"] == 3
        assert usage["unresolved_forks"] == 0
        assert len(usage["families"]) == 1
        assert usage["families"][0]["assistant_turns"] == 3
    _write(child, _records("child", "parent", 4))
    ingest(env, store, NOW)
    assert reading(store, DAY)["summary"]["tokens_total"] == 480
    parent.unlink()
    ingest(env, store, NOW)
    assert reading(store, DAY)["summary"]["tokens_total"] == 480
    assert reading(store, DAY)["unresolved_forks"] == 1
    assert snapshot_for_day(store, DAY, []).total_assistant_turns == 4
    assert store.quota_details_for_day(DAY.isoformat())[0][5]
    assert "PRIVATE" not in repr(store.session_evidence_rows())
    child.unlink()
    ingest(env, store, NOW)
    assert store.session_evidence_rows() == []


def test_session_detail_is_count_only_and_period_scoped(tmp_path):
    path = _write(tmp_path / "logs" / "sessions" / "parent.jsonl", _records("parent"))
    store = Store(tmp_path / "state.db")
    store.migrate()
    ingest(
        {
            "PRACTICEGRAPH_CODEX_HOME": str(path.parent.parent),
            "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "none"),
        },
        store,
        NOW,
    )
    result = reading(store, DAY, "today")
    assert result["drivers"]["without_test_attempt"] == 1
    identifier = result["sessions"][0]["id"]
    detail = reading(store, DAY, "today", selected=identifier)
    assert detail["context"]["latest_input_tokens"] == 100
    assert sum(c["characters"] for c in detail["context"]["components"]) > 0
    assert "PRIVATE" not in json.dumps(detail)
    assert "parent" not in detail["session"].values()
    with pytest.raises(KeyError):
        reading(store, date(2026, 9, 8), "today", selected=identifier)
    with pytest.raises(ValueError):
        reading(store, DAY, "arbitrary")
    store.hygiene("2026-09-08")
    assert store.session_evidence_rows() == []


def test_codex_custom_patch_and_namespaced_commands_are_counted(tmp_path):
    records = _records("custom", n=1)
    records[4]["payload"] = {"type": "custom_tool_call", "name": "functions.apply_patch",
                              "input": "*** Update File: PRIVATE.py\n+x"}
    records.insert(5, {"type": "response_item", "timestamp": "2026-09-07T08:04:30Z",
                       "payload": {"type": "function_call", "name": "functions.exec_command",
                                   "arguments": json.dumps({"cmd": "pytest -q"})}})
    path = _write(tmp_path / "custom.jsonl", records)
    event = ADAPTER.parse_file(path).events[-1]
    assert event.files_edited == 1
    assert event.tool_calls == 2
    assert event.test_run_attempts == 1


def test_claude_streaming_composition_is_last_wins(tmp_path):
    base = {
        "type": "assistant",
        "timestamp": "2026-09-07T08:00:00Z",
        "requestId": "r",
        "message": {
            "id": "m",
            "content": [{"type": "text", "text": "abc"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 90},
        },
    }
    later = json.loads(json.dumps(base))
    later["message"]["content"][0]["text"] = "abcd"
    path = _write(tmp_path / "claude.jsonl", [base, later])
    result = summarize(path, "claude_code_cli")
    assert result["characters"]["assistant"] == 4
    assert result["latest_input_tokens"] == 100


def test_previous_period_carries_an_aligned_daily_series(tmp_path):
    logs = tmp_path / "codex" / "sessions"
    _write(logs / "now.jsonl", _records("now"))
    store = Store(tmp_path / "state.db")
    store.migrate()
    ingest({"PRACTICEGRAPH_CODEX_HOME": str(logs.parent),
            "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "empty")}, store, NOW)
    usage = reading(store, DAY, "7d")
    previous = usage["previous"]
    assert len(previous["series"]) == len(usage["series"]) == 7
    assert previous["series"][0]["day"] == previous["from_day"]
    assert previous["series"][-1]["day"] == previous["to_day"]
    assert all(point["cost_micro_usd"] == 0 for point in previous["series"])
    assert reading(store, DAY, "all")["previous"] is None
