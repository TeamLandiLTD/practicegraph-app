"""Fixture-pack conformance for the Claude Code adapter (FR-SRC-1/4/5/6/8)."""

from __future__ import annotations

import re
from pathlib import Path

from conftest import CLAUDE_FIXTURE_HOME, FIXTURE_ENV
from practicegraph.events import TurnKind
from practicegraph.sources.claude_code import ADAPTER

SESSION_1 = (
    CLAUDE_FIXTURE_HOME
    / "projects"
    / "C--demo-app"
    / "11111111-1111-1111-1111-111111111111.jsonl"
)


def test_parser_version_is_pinned() -> None:
    """The denominator (W3.1) bumped BOTH parsers: commit/test attempt
    counters classified from a bounded, transient read of command text —
    integers only, the string never stored. A bump invalidates every cursor,
    so the next scan is a full rescan and history backfills from the logs
    still on disk."""
    from practicegraph.sources.codex import ADAPTER as CODEX_ADAPTER

    assert ADAPTER.parser_version == 11
    assert CODEX_ADAPTER.parser_version == 13


def test_discovery_env_override_and_ordering() -> None:
    paths = ADAPTER.discover(dict(FIXTURE_ENV))
    assert [p.name for p in paths] == [
        "11111111-1111-1111-1111-111111111111.jsonl",
        "22222222-2222-2222-2222-222222222222.jsonl",
        "77777777-7777-7777-7777-777777777777.jsonl",  # sidechain transcript
        "99999999-9999-9999-9999-999999999999.jsonl",  # subagent transcript
    ]
    assert ADAPTER.discover({"PRACTICEGRAPH_CLAUDE_HOME": "Z:/definitely/not/here"}) == []


def test_discovery_honors_tool_multi_root_env(tmp_path: Path) -> None:
    """CLAUDE_CONFIG_DIR is comma-separated multi-root (FR-SRC-2). The config
    dir path also joins Claude Desktop's session stores, so pin that seam to an
    empty dir: otherwise a Mac running Claude Desktop leaks real sessions in."""
    projects_root = str(CLAUDE_FIXTURE_HOME / "projects")
    no_desktop = {"PRACTICEGRAPH_CLAUDE_DESKTOP": str(tmp_path)}
    via_config = ADAPTER.discover(
        {"CLAUDE_CONFIG_DIR": str(CLAUDE_FIXTURE_HOME), **no_desktop}
    )
    via_projects = ADAPTER.discover(
        {"CLAUDE_CONFIG_DIR": projects_root + ",Z:/absent", **no_desktop}
    )
    assert via_config == via_projects
    assert len(via_config) == 4


def _seed_claude_desktop(support: object) -> None:
    """Build a fake Claude Desktop Application Support tree: each session
    subdir holds a session directory with its own ``.claude/projects`` store
    (the ``**`` in CodexBar's ``<support>/<subdir>/**/.claude/projects`` glob).
    A metadata-only session dir with no JSONL is included to prove it is inert.
    """
    from pathlib import Path

    base = Path(str(support))
    # local-agent-mode-sessions/<uuid>/.claude/projects/<proj>/<session>.jsonl
    agent = base / "local-agent-mode-sessions" / "sess-a" / ".claude" / "projects" / "proj-1"
    agent.mkdir(parents=True)
    (agent / "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl").write_text("", encoding="utf-8")
    # claude-code-sessions/<uuid>/.claude/projects/<proj>/<session>.jsonl
    code = base / "claude-code-sessions" / "sess-b" / ".claude" / "projects" / "proj-2"
    code.mkdir(parents=True)
    (code / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.jsonl").write_text("", encoding="utf-8")
    # A metadata-only session (no .claude/projects) must contribute nothing.
    (base / "claude-code-sessions" / "sess-meta").mkdir(parents=True)
    (base / "claude-code-sessions" / "sess-meta" / "meta.json").write_text("{}", encoding="utf-8")


def test_discovery_includes_claude_desktop_sessions(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """On macOS, Claude Desktop embeds coding sessions under its Application
    Support dir; a CLI-only scan misses them (verified against CodexBar
    docs/claude.md). The PRACTICEGRAPH_CLAUDE_DESKTOP seam drives the scan on
    any OS. Both nested stores are found; a metadata-only dir stays inert."""
    support = tmp_path / "ClaudeSupport"
    _seed_claude_desktop(support)

    # No CLI roots configured, only the Desktop seam -> exactly the two nested
    # JSONL files, deterministically ordered.
    env = {
        "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "absent-cli"),
        "PRACTICEGRAPH_CLAUDE_DESKTOP": str(support),
    }
    names = [p.name for p in ADAPTER.discover(env)]
    # discover() sorts by full path string: "claude-code-sessions" sorts before
    # "local-agent-mode-sessions" (c < l), so bbbb precedes aaaa.
    assert names == [
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.jsonl",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa.jsonl",
    ]


def test_discovery_claude_desktop_absent_is_empty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A pointed-but-absent Desktop support dir yields nothing (fail-open)."""
    env = {
        "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "absent-cli"),
        "PRACTICEGRAPH_CLAUDE_DESKTOP": str(tmp_path / "no-such-support"),
    }
    assert ADAPTER.discover(env) == []


def test_parse_health_counters_fail_open() -> None:
    """FR-SRC-5: malformed and unknown records are counted, never fatal."""
    health = ADAPTER.parse_file(SESSION_1).health
    assert health.seen == 13
    assert health.parsed == 10
    assert health.skipped == 1  # summary record
    assert health.malformed == 1  # truncated JSON line
    assert health.unsupported == 1  # unknown record kind
    assert health.unknown_field == 1  # record with an unrecognized field
    assert health.drift_detected


def test_events_carry_counters_not_content() -> None:
    events = ADAPTER.parse_file(SESSION_1).events
    assert len(events) == 10

    assistants = [e for e in events if e.kind is TurnKind.ASSISTANT_TURN]
    users = [e for e in events if e.kind is TurnKind.USER_TURN]
    assert len(assistants) == 4
    assert len(users) == 6

    first = assistants[0]
    assert first.model == "claude-sonnet-4-20250514"
    assert first.tokens.input == 1200
    assert first.tokens.cached == 800
    assert first.tokens.cache_creation == 200
    assert first.tokens.output == 350
    assert first.session_id == "11111111-1111-1111-1111-111111111111"
    assert first.timestamp.tzinfo is not None

    tool_turn = assistants[1]
    assert tool_turn.tool_calls == 2

    retry_turn = assistants[2]
    assert retry_turn.retries == 1

    interrupted = users[-1]
    assert interrupted.interruptions == 1

    # P1 execution counters: every tool_result content block is a command
    # run; is_error marks the failure; a user-modified edit is rework —
    # integers only, result text and patches never stored. Real logs carry
    # no structural duration, so slow is always 0 here.
    read_result = users[1]
    assert read_result.commands_run == 1
    assert read_result.commands_failed == 0
    bash_result = users[2]
    assert bash_result.commands_run == 1
    assert bash_result.commands_failed == 1  # is_error: true
    assert bash_result.commands_slow == 0  # no durationMs exists in real logs
    assert bash_result.rework_edits == 0  # string toolUseResult: fail-open
    edit_result = users[3]
    assert edit_result.rework_edits == 1  # toolUseResult.userModified
    assert edit_result.commands_run == 1  # its tool_result block still counts
    assert edit_result.commands_failed == 0

    # P2: the isCompactSummary user record IS the compaction marker (v5) —
    # the flag becomes a counter; the summary text is never stored.
    compact_summary = users[4]
    assert compact_summary.compactions == 1
    assert sum(e.compactions for e in events) == 1  # only that one record

    # Presence flags are set; the values themselves are never stored (FR-SRC-4).
    ask = users[0]
    assert ask.content_present
    assert ask.path_present
    assert not ask.identity_present

    # v7: no fixture human turn is "go"-class short — the long ask, the
    # tool-result lists, the compact summary, and the 29-char interruption
    # marker all stay unmarked.
    assert all(event.short_reply is False for event in events)

    # Only the external string-content ask is verified human attention.
    # Tool results, compacted summaries, interruptions, and assistant output
    # are structural records and must remain excluded.
    assert [event.human_initiated for event in events] == [
        True, False, False, False, False, False, False, False, False, False
    ]

    dump = repr(events)
    assert "SECRET-MARKER" not in dump
    assert "demo.user" not in dump
    assert "example.com" not in dump
    assert "sk-demo" not in dump


def test_cumulative_streaming_chunks_dedup_last_wins() -> None:
    """Streaming repeats message.id+requestId with cumulative usage; the last
    record wins and usage is never summed (CodexBar-verified semantics)."""
    session_2 = SESSION_1.parent / "22222222-2222-2222-2222-222222222222.jsonl"
    result = ADAPTER.parse_file(session_2)
    assert result.health.seen == 3
    assert result.health.parsed == 2  # one user + one final assistant
    assert result.health.skipped == 1  # superseded cumulative chunk
    assistant = [e for e in result.events if e.kind is TurnKind.ASSISTANT_TURN]
    assert len(assistant) == 1
    assert assistant[0].tokens.output == 20  # final chunk, not 5, not 25


def test_cross_file_dedup_prefers_parent_transcript() -> None:
    """The same API call recorded in a parent and a subagent transcript is
    counted once; sorted order makes the parent win."""
    paths = ADAPTER.discover(dict(FIXTURE_ENV))
    result = ADAPTER.parse_many(paths)
    opus_turns = [e for e in result.events if e.model.startswith("claude-opus")]
    assert len(opus_turns) == 1
    assert result.health.seen == 21
    assert result.health.parsed == 16
    assert result.health.skipped == 3  # summary + streaming chunk + subagent dup


def _approval_run_lines(session: str, reply_text: str, reply_gap_s: int) -> str:
    """A long delegated run: 8 tooled assistant turns (each with its automated
    tool-result user record), an untooled summary/ask, then the human reply."""
    import json as _json

    def record(kind: str, minute: int, second: int, **extra: object) -> str:
        base: dict[str, object] = {
            "type": kind,
            "sessionId": session,
            "isSidechain": False,
            "timestamp": f"2026-07-02T09:{minute:02d}:{second:02d}.000Z",
        }
        base.update(extra)
        return _json.dumps(base)

    usage = {"input_tokens": 10, "output_tokens": 10}
    lines: list[str] = [
        record("user", 0, 0,
               message={"role": "user",
                        "content": "Refactor the demo module carefully please"}),
    ]
    for index in range(8):
        lines.append(
            record(
                "assistant", index + 1, 0,
                message={
                    "id": f"msg_{session}_{index}", "role": "assistant",
                    "model": "claude-sonnet-4-20250514",
                    "content": [{"type": "tool_use", "id": f"t{index}",
                                 "name": "Bash", "input": {}}],
                    "usage": usage,
                },
                requestId=f"req_{session}_{index}",
            )
        )
        lines.append(
            record(
                "user", index + 1, 30,
                message={"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{index}",
                     "content": "ok"}]},
            )
        )
    lines.append(
        record(
            "assistant", 9, 0,
            message={
                "id": f"msg_{session}_sum", "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text",
                             "text": "All done - say go to proceed."}],
                "usage": usage,
            },
            requestId=f"req_{session}_sum",
        )
    )
    lines.append(
        record(
            "user", 9, reply_gap_s,
            message={"role": "user", "content": reply_text},
        )
    )
    return "\n".join(lines) + "\n"


def test_waved_approval_scenario_end_to_end(tmp_path) -> None:
    """The waved-through signature, from raw log lines to the metric: a long
    tooled stretch -> untooled summary -> a 3-second "go" is WAVED; the same
    run answered with a 500-char review 3 seconds later is merely fast — the
    short_reply flag (parser v7) is what separates them. Marks travel through
    history._marks_from_events, the exact ingest path."""
    from practicegraph.analysis.focus import compute_metrics
    from practicegraph.events import TurnKind
    from practicegraph.history import _marks_from_events
    from practicegraph.store import MarkRow

    waved_file = tmp_path / "aaaa-waved.jsonl"
    waved_file.write_text(
        _approval_run_lines("aaaa-waved", "go", reply_gap_s=3), encoding="utf-8"
    )
    considered_file = tmp_path / "bbbb-considered.jsonl"
    considered_file.write_text(
        _approval_run_lines("bbbb-considered", "x" * 500, reply_gap_s=3),
        encoding="utf-8",
    )

    waved_events = ADAPTER.parse_file(waved_file).events
    considered_events = ADAPTER.parse_file(considered_file).events
    # Parser flags: only the "go" human turn is short; automated tool-result
    # records (list content) and the 500-char review never are.
    assert [e.short_reply for e in waved_events if e.kind is TurnKind.USER_TURN][
        -1
    ] is True
    assert sum(e.short_reply for e in waved_events) == 1
    assert all(e.short_reply is False for e in considered_events)

    marks: list[MarkRow] = [
        row[1:] for row in _marks_from_events(waved_events + considered_events)
    ]
    metrics = compute_metrics(marks)
    assert metrics.approval_moments == 2  # both runs ended in a closed ask
    assert metrics.waved_through == 1  # only the "go" was waved through


SIDECHAIN = (
    CLAUDE_FIXTURE_HOME
    / "projects"
    / "C--demo-app"
    / "subagents"
    / "77777777-7777-7777-7777-777777777777.jsonl"
)

_HASH16 = re.compile(r"^[0-9a-f]{16}$")


def test_sidechain_transcripts_parse_non_interactive_with_env_hashes() -> None:
    """P3: every record of a sidechain transcript carries isSidechain:true
    (real-log shape verified 2026-07-05) -> interactive=False; main
    transcripts carry isSidechain:false -> interactive=True. cwd/gitBranch
    become 16-hex identity hashes — the values themselves never land on an
    event (FR-SRC-4)."""
    sidechain = ADAPTER.parse_file(SIDECHAIN).events
    assert len(sidechain) == 4
    assert all(event.interactive is False for event in sidechain)

    main = ADAPTER.parse_file(SESSION_1).events
    assert all(event.interactive is True for event in main)

    for event in (*sidechain, *main):
        assert _HASH16.match(event.cwd_hash)
        assert _HASH16.match(event.branch_hash)
    # Different environment values hash to different identities; the same
    # value hashes identically (cardinality without the value, INV-1).
    assert {e.branch_hash for e in main} != {e.branch_hash for e in sidechain}
    assert len({e.cwd_hash for e in main}) == 1

    dump = repr(sidechain)
    assert "SECRET-MARKER" not in dump  # branch/cwd values never stored
    assert "demo.user" not in dump
    assert "worktree" not in dump


def test_denominator_classifies_bash_commit_and_test_commands(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """W3.1: Bash tool_use commands classify
    through the shared bounded matcher — a compound line counts once per
    class, a commit mentioned in text is not a commit run, non-Bash tools
    never reach the classifier, and only integers leave the parser."""
    import json

    blocks = [
        {
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": 'cd "/repo" && git commit -m "x"'},
        },
        {
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": "uv run python -m pytest -q"},
        },
        {
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": 'echo "git commit"'},
        },
        {
            "type": "tool_use",
            "name": "Edit",
            "input": {"command": "git commit -m nope"},
        },
    ]
    record = {
        "type": "assistant",
        "timestamp": "2026-07-01T09:00:00.000Z",
        "sessionId": "22222222-2222-2222-2222-222222222222",
        "message": {
            "role": "assistant",
            "model": "claude-fable-5",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": blocks,
        },
    }
    path = tmp_path / "denominator.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    (event,) = ADAPTER.parse_file(path).events
    assert event.tool_calls == 4
    assert event.git_commit_attempts == 1
    assert event.test_run_attempts == 1


def test_classify_command_bounds_and_separators() -> None:
    """The classifier contract (spike §2): bounded read, separator-anchored
    segments, argv handling — and the bound is a hard stop, not a window."""
    from practicegraph.sources import (
        COMMAND_CLASSIFY_MAX_BYTES,
        classify_command,
    )

    assert classify_command("git commit -m x") == (1, 0)
    assert classify_command("pytest -q | tee out.txt") == (0, 1)
    assert classify_command("npm test; git commit -m x") == (1, 1)
    assert classify_command("pnpm run test") == (0, 1)
    assert classify_command("gitk && legit commit") == (0, 0)
    # A commit buried past the byte bound never classifies: the classifier
    # reads a prefix, not the command.
    padded = "echo " + "a" * COMMAND_CLASSIFY_MAX_BYTES + " && git commit -m x"
    assert classify_command(padded) == (0, 0)
    # argv vectors: the shell payload slot classifies; a bare argv joins.
    assert classify_command(["bash", "-lc", "git commit -m x"]) == (1, 0)
    assert classify_command(["git", "commit", "-m", "x"]) == (1, 0)
    # Non-string shapes are zeros, never an error.
    assert classify_command(None) == (0, 0)
    assert classify_command(42) == (0, 0)
    assert classify_command([1, 2]) == (0, 0)


def test_artifact_counters_classify_writes_by_kind_and_place(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """W4.1: Write calls classify by suffix class and destination — scratch
    never counts, exports mark out-of-project writes, edits count separately
    — and only integers leave the parser."""
    import json

    record = {
        "type": "assistant",
        "timestamp": "2026-07-01T09:00:00.000Z",
        "sessionId": "33333333-3333-3333-3333-333333333333",
        "cwd": "C:/work/repo",
        "message": {
            "role": "assistant",
            "model": "claude-fable-5",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [
                {"type": "tool_use", "name": "Write",
                 "input": {"file_path": "C:/work/repo/notes/plan.md"}},
                {"type": "tool_use", "name": "Write",
                 "input": {"file_path": "C:/work/repo/src/app.py"}},
                {"type": "tool_use", "name": "Write",
                 "input": {"file_path": "C:/Users/u/Downloads/report.pdf"}},
                {"type": "tool_use", "name": "Write",
                 "input": {"file_path": "C:/Users/u/AppData/Local/Temp/x.md"}},
                {"type": "tool_use", "name": "Edit",
                 "input": {"file_path": "C:/work/repo/src/app.py"}},
            ],
        },
    }
    path = tmp_path / "artifacts.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    (event,) = ADAPTER.parse_file(path).events
    assert event.files_created == 3  # temp write excluded
    assert event.doc_files_created == 2  # plan.md + report.pdf
    assert event.export_writes == 1  # the Downloads delivery
    assert event.files_edited == 1


def test_fast_flag_reads_usage_speed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Fast mode rides usage.speed: only "fast" sets the pricing flag;
    "standard" and an absent field are standard."""
    import json

    def record(minute: int, usage_extra: dict[str, str]) -> str:
        return json.dumps({
            "type": "assistant",
            "timestamp": f"2026-09-18T09:0{minute}:00.000Z",
            "sessionId": "33333333-3333-3333-3333-333333333333",
            "requestId": f"req_{minute}",
            "message": {
                "id": f"msg_{minute}",
                "role": "assistant",
                "model": "claude-opus-5",
                "usage": {"input_tokens": 10, "output_tokens": 5, **usage_extra},
                "content": [{"type": "text", "text": "ok"}],
            },
        })

    path = tmp_path / "speed.jsonl"
    path.write_text(
        "\n".join([
            record(1, {"speed": "fast"}),
            record(2, {"speed": "standard"}),
            record(3, {}),
        ]) + "\n",
        encoding="utf-8",
    )

    events = sorted(ADAPTER.parse_file(path).events, key=lambda e: e.timestamp)
    assert [e.fast for e in events] == [True, False, False]
