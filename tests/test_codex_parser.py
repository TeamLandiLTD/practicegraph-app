"""Fixture-pack conformance for the Codex adapter (FR-SRC-1/4/5/6/8)."""

from __future__ import annotations

from conftest import CODEX_FIXTURE_HOME, FIXTURE_ENV
from practicegraph.events import TurnKind
from practicegraph.sources.codex import ADAPTER

ROLLOUT = (
    CODEX_FIXTURE_HOME
    / "sessions"
    / "2026"
    / "07"
    / "02"
    / "rollout-2026-07-02T10-00-00-33333333-3333-3333-3333-333333333333.jsonl"
)


def test_discovery_env_override() -> None:
    paths = ADAPTER.discover(dict(FIXTURE_ENV))
    assert [p.name for p in paths] == [ROLLOUT.name]
    # The tool's own env var is honored when the override is absent (FR-SRC-2).
    assert ADAPTER.discover({"CODEX_HOME": str(CODEX_FIXTURE_HOME)}) == paths
    assert ADAPTER.discover({"PRACTICEGRAPH_CODEX_HOME": "Z:/nope"}) == []


def test_parse_health_counters_fail_open() -> None:
    health = ADAPTER.parse_file(ROLLOUT).health
    assert health.seen == 19
    assert health.parsed == 16  # incl. exec/patch/fco/compacted + user_message (v8)
    assert health.skipped == 1  # reasoning
    assert health.malformed == 1
    assert health.unsupported == 1  # unknown event kind
    assert health.unknown_field == 0
    assert health.drift_detected


def test_token_normalization_and_pending_counters() -> None:
    events = ADAPTER.parse_file(ROLLOUT).events
    assert len(events) == 4

    assistants = [e for e in events if e.kind is TurnKind.ASSISTANT_TURN]
    users = [e for e in events if e.kind is TurnKind.USER_TURN]
    assert len(assistants) == 2
    assert len(users) == 2

    first = assistants[0]
    assert first.model == "gpt-5-codex"
    # OpenAI-style usage is normalized: input excludes cached tokens.
    assert first.tokens.input == 1000
    assert first.tokens.cached == 4000
    assert first.tokens.output == 600
    assert first.tokens.reasoning == 320
    assert first.tool_calls == 1
    assert first.session_id == "33333333-3333-3333-3333-333333333333"
    # P1, old rollout format: the failed 45s exec and the rejected patch
    # flush onto this turn. Its function_call_output also counts as a run
    # (JSON-blob output has no "execution error:" prefix, so no failure).
    assert first.commands_run == 1  # legacy JSON-blob fco no longer double-counts
    assert first.commands_failed == 1
    assert first.commands_slow == 1
    assert first.rework_edits == 1
    assert first.compactions == 0  # no compaction before this turn

    second = assistants[1]
    assert second.tokens.input == 600
    assert second.tokens.cached == 2000
    assert second.tokens.output == 150
    assert second.interruptions == 1  # turn_aborted attached to next turn
    # P1: the clean 2s exec plus a new-format function_call_output whose
    # output starts with the fixed "execution error:" prefix (probed
    # transiently — the text itself is never stored). No duration exists
    # in the new format, so it can never count as slow.
    assert second.commands_run == 1  # runs come from fco only
    assert second.commands_failed == 1
    assert second.commands_slow == 0
    assert second.rework_edits == 0
    # P2 (parser v6): the "compacted" record is a pending counter flushed
    # onto this next turn — exactly the pending_tool_calls mechanism.
    assert second.compactions == 1

    dump = repr(events)
    assert "SECRET-MARKER" not in dump
    assert "demo.user" not in dump
    assert "example.com" not in dump


def test_user_message_probe_marks_short_replies_without_new_turns() -> None:
    """Parser v8: the user_message event (which trails the response_item that
    synthesized the user turn) is length-probed transiently — a "go"-class
    string marks that most recent user turn short_reply. No second turn is
    ever emitted (the fixture still parses exactly 2 user turns), the text
    itself never lands on an event, and the first user turn (a long ask with
    no short user_message) stays unmarked."""
    result = ADAPTER.parse_file(ROLLOUT)
    users = [e for e in result.events if e.kind is TurnKind.USER_TURN]
    assert len(users) == 2  # the probe never double-counts human turns
    assert users[0].short_reply is False  # long ask
    assert users[1].short_reply is True  # "go" (2 chars <= threshold)
    assert all(
        e.short_reply is False
        for e in result.events
        if e.kind is TurnKind.ASSISTANT_TURN
    )
    assert '"go"' not in repr(result.events)  # length only, never the text


def test_only_codex_user_turns_are_human_initiated() -> None:
    events = ADAPTER.parse_file(ROLLOUT).events
    # Only the user turn corroborated by a user_message event is positive.
    # User-role context injections fail closed as non-human.
    assert [event.human_initiated for event in events] == [False, False, True, False]
    assert len({event.cwd_hash for event in events}) == 1
    assert len(events[0].cwd_hash) == 16
    assert "demo-app" not in repr(events)


def test_rate_limit_gauges_ride_the_parse_result() -> None:
    """P4 (parser v7): ``rate_limits.primary`` on a token_count becomes one
    gauge reading — used percent in integer tenths plus the window length —
    on the ParseResult, never on a TurnEvent. Only the two lines carrying
    rate_limits gauge (the duplicate token_count has none), the secondary
    window is ignored, and nothing else of the payload survives."""
    result = ADAPTER.parse_file(ROLLOUT)
    assert [(used, window) for _ts, used, window in result.gauges] == [
        (325, 300),  # 32.5% -> tenths
        (40, 10080),  # weekly reading is independently retained
        (410, 300),  # 41.0% — the fixture day's latest reading
    ]
    assert [ts.isoformat() for ts, _used, _window in result.gauges] == [
        "2026-07-02T10:00:40+00:00",
        "2026-07-02T10:00:40+00:00",
        "2026-07-02T10:03:00+00:00",
    ]


def test_repeated_token_count_does_not_double_count() -> None:
    """The CLI can emit token_count twice for one turn (same totals). The
    totals-delta guard must swallow the repeat — exactly 2 assistant turns,
    even though the fixture carries 3 token_count events."""
    events = ADAPTER.parse_file(ROLLOUT).events
    assistants = [e for e in events if e.kind is TurnKind.ASSISTANT_TURN]
    assert len(assistants) == 2
    total_output = sum(e.tokens.output for e in assistants)
    assert total_output == 750  # 600 + 150, the duplicate contributed nothing


def test_denominator_classifies_shell_function_calls(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """W3.1: the shell tool's JSON-encoded arguments decode transiently; the
    argv wrapper ("bash -lc <payload>") classifies by its payload; a non-shell
    tool and an oversized/garbled arguments string count zero (fail-open)."""
    import json

    lines = [
        {"timestamp": "2026-07-01T09:00:00.000Z", "type": "session_meta",
         "payload": {"id": "sess-denominator"}},
        {"timestamp": "2026-07-01T09:00:01.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": json.dumps({
                         "command": ["bash", "-lc",
                                     "pytest -q && git commit -m done"]})}},
        {"timestamp": "2026-07-01T09:00:01.500Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell_command",
                     "arguments": json.dumps({
                         "command": "git commit -m current-format"})}},
        {"timestamp": "2026-07-01T09:00:02.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "apply_patch",
                     "arguments": json.dumps({"command": "git commit -m no"})}},
        {"timestamp": "2026-07-01T09:00:03.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": "{not json"}},
        {"timestamp": "2026-07-01T09:00:04.000Z", "type": "event_msg",
         "payload": {"type": "token_count",
                     "info": {"total_token_usage": {
                         "input_tokens": 100, "output_tokens": 20}}}},
    ]
    path = tmp_path / "rollout-denominator.jsonl"
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )

    events = ADAPTER.parse_file(path).events
    assistants = [e for e in events if e.kind is TurnKind.ASSISTANT_TURN]
    assert len(assistants) == 1
    turn = assistants[0]
    assert turn.tool_calls == 4
    assert turn.git_commit_attempts == 2  # legacy 'shell' AND current 'shell_command'
    assert turn.test_run_attempts == 1
