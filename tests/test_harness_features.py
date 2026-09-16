"""The features scan: classify what each harness's sessions reached for."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from practicegraph.analysis.harness_features import (
    FEATURE_DEFS,
    read_features,
)


def _homes(tmp_path: Path) -> dict[str, str]:
    claude = tmp_path / "claudehome"
    codex = tmp_path / "codexhome"
    (claude / "projects" / "proj").mkdir(parents=True)
    (codex / "sessions").mkdir(parents=True)
    return {
        "PRACTICEGRAPH_CLAUDE_HOME": str(claude),
        "PRACTICEGRAPH_CODEX_HOME": str(codex),
    }


def _claude_line(name: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name,
                                 "input": {"secret": "never travels"}}]},
    }) + "\n"


def test_claude_calls_classify_into_the_documented_taxonomy(tmp_path) -> None:
    env = _homes(tmp_path)
    log = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "proj" / "a.jsonl"
    log.write_text(
        _claude_line("Bash") + _claude_line("Edit") + _claude_line("Agent")
        + _claude_line("WebSearch") + _claude_line("DesignSync")
        + _claude_line("mcp__garmin__get_steps")
        + _claude_line("mcp__Claude_Browser__navigate")
        + _claude_line("ToolSearch")  # plumbing: dropped, not guessed at
        + _claude_line("SomethingUnknown"),
        encoding="utf-8",
    )
    claude, _ = read_features(env)
    got = {use.feature: use.count for use in claude.features}
    assert got == {"files_shell": 2, "subagents": 1, "web": 1,
                   "design": 1, "browser": 1}
    # MCP traffic is counted per server for the connectors page — the
    # internal browser surface is a feature, not a connector.
    assert claude.mcp_calls == (("garmin", 1),)
    assert claude.sessions == 1


def test_codex_events_classify_without_double_counting_patches(tmp_path) -> None:
    env = _homes(tmp_path)
    log = Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "sessions" / "s.jsonl"
    records = [
        {"payload": {"type": "custom_tool_call", "name": "exec"}},
        {"payload": {"type": "function_call", "name": "wait"}},
        {"payload": {"type": "custom_tool_call", "name": "spawn_agent"}},
        # The function name AND its landing event describe ONE patch; only
        # the event counts.
        {"payload": {"type": "function_call", "name": "apply_patch"}},
        {"payload": {"type": "patch_apply_end"}},
        {"payload": {"type": "web_search_end", "query": "never travels"}},
        {"payload": {"type": "context_compacted"}},
        {"payload": {"type": "mcp_tool_call_end",
                     "invocation": {"server": "node_repl", "tool": "js"}}},
    ]
    log.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8",
    )
    _, codex = read_features(env)
    got = {use.feature: use.count for use in codex.features}
    assert got == {"tool_runner": 1, "background": 1, "subagents": 1,
                   "apply_patch": 1, "web": 1, "compaction": 1}
    assert codex.mcp_calls == (("node_repl", 1),)


def test_the_scan_window_skips_old_logs_and_fails_closed(tmp_path) -> None:
    env = _homes(tmp_path)
    old = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "proj" / "old.jsonl"
    old.write_text(_claude_line("Bash"), encoding="utf-8")
    stale = time.time() - 40 * 86400
    os.utime(old, (stale, stale))
    claude, codex = read_features(env)
    assert claude.sessions == 0
    assert claude.features == ()
    assert codex.features == ()
    assert codex.mcp_calls == ()


def test_modern_codex_calls_include_agents_background_web_questions_and_patches(tmp_path):
    env = _homes(tmp_path)
    log = Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "sessions" / "modern.jsonl"
    calls = ["functions.exec", "js", "functions.exec_command", "collaboration.spawn_agent",
             "collaboration.wait_agent", "functions.write_stdin", "clock.sleep",
             "functions.request_user_input_async", "functions.view_image",
             "functions.apply_patch", "mcp__cua_repl.js", "unknown.spawn_agent"]
    rows = [{"payload": {"type": "custom_tool_call", "name": name, "call_id": str(i),
                         "input": "secret text is never exported"}} for i, name in enumerate(calls)]
    rows += [{"payload": {"type": "web_search_call", "id": "web-1",
                          "action": {"query": "private"}}},
             {"payload": {"type": "web_search_end", "call_id": "web-1"}},
             {"payload": {"type": "patch_apply_end", "call_id": "9"}}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf8")
    _, codex = read_features(env)
    assert {x.feature: x.count for x in codex.features} == {
        "tool_runner": 2, "files_shell": 1, "subagents": 2, "background": 2,
        "questions": 1, "images": 1, "apply_patch": 1, "browser": 1, "web": 1,
    }
    assert "secret" not in repr(codex) and "private" not in repr(codex)
    assert codex.work_mix[0].work_type == "coding"


def test_patch_calls_without_legacy_completion_events_still_count(tmp_path):
    env = _homes(tmp_path)
    log = Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "sessions" / "patches.jsonl"
    log.write_text(
        json.dumps({"payload": {"type": "custom_tool_call", "name": "apply_patch"}}),
        encoding="utf8",
    )
    _, codex = read_features(env)
    assert {x.feature: x.count for x in codex.features} == {"apply_patch": 1}


def test_sessions_classify_into_the_work_mix_by_documented_rules(tmp_path) -> None:
    """PRODUCTIVITY_PROFILE P2: one bucket per session, precedence stated
    in the taxonomy — office artifacts, then code edits, then research,
    then any tool use, then conversation alone."""
    env = _homes(tmp_path)
    proj = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "proj"
    (proj / "deck.jsonl").write_text(
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "python make_deck.py out/board.pptx"}}]}})
        + "\n",
        encoding="utf-8",
    )
    (proj / "code.jsonl").write_text(
        _claude_line("Edit"), encoding="utf-8",
    )
    (proj / "research.jsonl").write_text(
        _claude_line("WebSearch"), encoding="utf-8",
    )
    (proj / "tidy.jsonl").write_text(
        _claude_line("Bash"), encoding="utf-8",
    )
    (proj / "talk.jsonl").write_text(
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "text",
                                             "text": "just words"}]}}) + "\n",
        encoding="utf-8",
    )
    claude, codex = read_features(env)
    got = {use.work_type: use.sessions for use in claude.work_mix}
    assert got == {"documents": 1, "coding": 1, "research": 1,
                   "organizing": 1, "writing": 1}
    assert codex.work_mix == ()


def test_codex_patches_classify_as_code_work(tmp_path) -> None:
    env = _homes(tmp_path)
    log = Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "sessions" / "s.jsonl"
    log.write_text(
        json.dumps({"payload": {"type": "patch_apply_end"}}) + "\n",
        encoding="utf-8",
    )
    _, codex = read_features(env)
    assert {use.work_type for use in codex.work_mix} == {"coding"}


def test_feature_copy_passes_the_lexicon_and_leak_gates() -> None:
    from practicegraph.analysis.harness_features import WORK_TYPES
    from practicegraph.privacy import leak_findings, lexicon_violations

    for label, doc in FEATURE_DEFS.values():
        assert lexicon_violations(label + " " + doc) == [], label
        assert leak_findings(label + " " + doc) == [], label
    for work_id, label, doc in WORK_TYPES:
        assert lexicon_violations(label + " " + doc) == [], work_id
        assert leak_findings(label + " " + doc) == [], work_id


def test_every_classified_feature_carries_its_documentation() -> None:
    """The owner's ask, pinned: enumerate the functionality AND document
    what each is. Every id the classifiers can emit must resolve to a
    label and a doc line, or a row would render undocumented."""
    from practicegraph.analysis.harness_features import (
        _CLAUDE_TOOL_FEATURE,
        _CODEX_EVENT_FEATURE,
        _CODEX_NAME_FEATURE,
    )
    emitted = (set(_CLAUDE_TOOL_FEATURE.values())
               | set(_CODEX_NAME_FEATURE.values())
               | set(_CODEX_EVENT_FEATURE.values())
               | {"browser"})
    for feature in emitted:
        label, doc = FEATURE_DEFS[feature]
        assert label and doc


def test_subagent_transcripts_count_as_feature_use_but_never_as_sessions(tmp_path) -> None:
    """Claude Code stores each subagent's transcript under the parent
    session's ``<id>/subagents/`` folder. On the owner's own machine those
    were 162 of 1,140 "sessions" in a month and pushed the work mix toward
    conversation-only - a part of a session is not a session. Their tool
    calls are real feature use and still count."""
    env = _homes(tmp_path)
    proj = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "proj"
    parent = json.dumps({"type": "user", "cwd": "C:/work/Alpha"}) + "\n"
    (proj / "abc.jsonl").write_text(parent + _claude_line("Edit"), encoding="utf-8")
    sub = proj / "abc" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(parent + _claude_line("Bash"), encoding="utf-8")
    (sub / "agent-2.jsonl").write_text(parent + _claude_line("WebSearch"), encoding="utf-8")
    claude, _ = read_features(env)
    assert claude.sessions == 1
    assert {u.work_type: u.sessions for u in claude.work_mix} == {"coding": 1}
    assert [(p.name, p.sessions) for p in claude.projects] == [("Alpha", 1)]
    got = {use.feature: use.count for use in claude.features}
    assert got == {"files_shell": 2, "web": 1}
