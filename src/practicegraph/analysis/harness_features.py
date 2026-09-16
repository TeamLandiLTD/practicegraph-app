"""What the harnesses DO for you: the features each client actually uses.

The inventory says what is installed; this reads the session logs and
classifies every tool invocation into a small documented taxonomy — plan
mode, subagents, web research, skills, Claude Design, apply_patch, context
compaction — so the page can say which capabilities of each harness this
person actually reaches for, and how often. Counts only, from the logs'
own records; arguments, file contents and commands never leave the scan.

The taxonomy is closed and every feature carries its own one-line
documentation (the owner's ask: enumerate the functionality AND say what
each is). An invocation that matches nothing is dropped, not guessed at.
MCP traffic is deliberately NOT a feature here — it is counted per server
and handed to the connectors page, which owns those names. The one
exception is Claude Code's built-in browser/preview surface, which rides
the MCP wire but is harness functionality, not a user-declared connector.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from practicegraph.sources.claude_code import ENV_CLAUDE_HOME
from practicegraph.sources.codex import ENV_CODEX_HOME, ENV_CODEX_HOME_NATIVE

WINDOW_DAYS = 30

# The browser/preview MCP servers are the harness's own surface.
_CLAUDE_INTERNAL_BROWSER = {"Claude_Browser", "Claude_Preview"}

# feature id -> (label, one-line documentation). Shared vocabulary; which
# harness can express which feature is decided by the classifiers below.
FEATURE_DEFS: dict[str, tuple[str, str]] = {
    "tool_runner": (
        "Tool orchestration",
        "Scripts that run and combine tools. Nested actions are not always "
        "recorded separately, so one script counts as one invocation.",
    ),
    "images": (
        "Image inspection",
        "Images opened for visual inspection during a task.",
    ),
    "files_shell": (
        "Files and shell",
        "The core loop: reading, editing and writing files, and running "
        "commands in the terminal.",
    ),
    "subagents": (
        "Subagents",
        "Work delegated to parallel agents — spawning them, messaging "
        "them, collecting what they return.",
    ),
    "background": (
        "Background tasks and loops",
        "Long-running work watched from the side: background commands, "
        "monitors, scheduled wake-ups.",
    ),
    "task_lists": (
        "Task lists",
        "The running to-do list the agent keeps while it works through a "
        "multi-step job.",
    ),
    "plan_mode": (
        "Plan mode",
        "The read-first mode that designs an approach and asks for "
        "approval before touching anything.",
    ),
    "web": (
        "Web research",
        "Searches and page fetches the agent ran to ground its answers "
        "in current sources.",
    ),
    "skills": (
        "Skills",
        "Packaged instructions invoked by name — a repeatable workflow "
        "the harness loads on demand.",
    ),
    "design": (
        "Claude Design",
        "The design surface: syncing a visual design system into the "
        "session to build against.",
    ),
    "artifacts": (
        "Artifacts",
        "Pages published from the session — reports and documents with "
        "a shareable link.",
    ),
    "questions": (
        "Questions to you",
        "Moments the agent stopped to ask a real decision instead of "
        "guessing.",
    ),
    "browser": (
        "In-app browser and preview",
        "The built-in browser pane: opening pages, driving them, "
        "screenshotting previews.",
    ),
    "workflows": (
        "Workflows",
        "Scripted multi-agent orchestration — fan-out, verification "
        "panels, pipelines.",
    ),
    "apply_patch": (
        "Patch edits",
        "File changes applied as structured patches rather than free "
        "shell edits.",
    ),
    "planning": (
        "Plan updates",
        "The step-by-step plan the agent maintains and revises while it "
        "works.",
    ),
    "compaction": (
        "Context compaction",
        "The session folded its older context to keep going — a marker "
        "of long, heavy runs.",
    ),
}

# Claude Code: tool_use name -> feature id. Names not listed are dropped
# (mcp__* is handled separately; ToolSearch and the like are harness
# plumbing, not functionality a person chose).
_CLAUDE_TOOL_FEATURE: dict[str, str] = {
    "Bash": "files_shell", "PowerShell": "files_shell", "Read": "files_shell",
    "Edit": "files_shell", "MultiEdit": "files_shell", "Write": "files_shell",
    "Glob": "files_shell", "Grep": "files_shell",
    "NotebookEdit": "files_shell",
    "Agent": "subagents", "Task": "subagents", "SendMessage": "subagents",
    "ListAgents": "subagents",
    "TaskOutput": "background", "TaskStop": "background",
    "Monitor": "background", "ScheduleWakeup": "background",
    "TaskCreate": "task_lists", "TaskUpdate": "task_lists",
    "TodoWrite": "task_lists",
    "EnterPlanMode": "plan_mode", "ExitPlanMode": "plan_mode",
    "exit_plan_mode": "plan_mode",
    "WebSearch": "web", "WebFetch": "web",
    "Skill": "skills",
    "DesignSync": "design",
    "Artifact": "artifacts",
    "AskUserQuestion": "questions",
    "Workflow": "workflows",
}

# Codex: invocation name -> feature id, across custom_tool_call and
# function_call records alike.
_CODEX_NAME_FEATURE: dict[str, str] = {
    "exec": "tool_runner", "js": "tool_runner", "shell": "files_shell",
    "shell_command": "files_shell", "exec_command": "files_shell",
    "write_stdin": "background", "wait": "background", "sleep": "background",
    "spawn_agent": "subagents", "wait_agent": "subagents",
    "send_message": "subagents", "list_agents": "subagents",
    "interrupt_agent": "subagents", "followup_task": "subagents",
    "update_plan": "planning",
    "request_user_input": "questions", "request_user_input_async": "questions",
    "view_image": "images",
    "apply_patch": "apply_patch",
}


def _codex_name_feature(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    if name in ("web.run", "web__run"):
        return "web"
    if name in ("mcp__cua_repl.js", "mcp__cua_repl__js"):
        return "browser"
    if name in ("mcp__codex_app__automation_update", "mcp__codex_app.automation_update"):
        return "background"
    for prefix in ("functions.", "collaboration.", "clock."):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return _CODEX_NAME_FEATURE.get(name)

# Codex: payload types that ARE the feature event. apply_patch also exists
# as a function name. Modern logs may omit the completion event, while
# older logs contain both; merge those two representations per session.
_CODEX_EVENT_FEATURE: dict[str, str] = {
    "patch_apply_end": "apply_patch",
    "web_search_end": "web",
    "web_search_call": "web",
    "context_compacted": "compaction",
}


@dataclass(frozen=True, slots=True)
class FeatureUse:
    feature: str
    count: int


# The work-type taxonomy (PRODUCTIVITY_PROFILE plan §2): what kind of work
# a SESSION was, classified from markers the scan already reads. Closed,
# documented, precedence-ordered; a session that matches nothing at all is
# "writing" — conversation with no tools is drafting and thinking. Each doc
# states the classification rule so the page never has to explain itself.
WORK_TYPES: tuple[tuple[str, str, str], ...] = (
    ("documents", "Document work",
     "Office artifacts moved through the session — presentations, "
     "spreadsheets, documents, PDFs."),
    ("coding", "Code work",
     "Code was edited: patches or file edits, without office artifacts in "
     "play."),
    ("research", "Research",
     "Web searches and page reads led the session, with nothing edited."),
    ("organizing", "Organizing",
     "Files and commands without edits — moving, listing, tidying, "
     "checking."),
    ("writing", "Drafting and thinking",
     "Conversation only: no tools touched — the work was the words."),
)
_OFFICE_MARK = re.compile(
    r"(?i)\.(pptx?|docx?|xlsx?|pdf|csv|pptm|xlsm|odt|odp|ods)\b"
)
_CLAUDE_EDIT_TOOLS = frozenset(
    {"Edit", "MultiEdit", "Write", "NotebookEdit"}
)


def _classify_session(office: bool, edited: bool, web: bool,
                      any_tools: bool) -> str:
    if office:
        return "documents"
    if edited:
        return "coding"
    if web:
        return "research"
    if any_tools:
        return "organizing"
    return "writing"


@dataclass(frozen=True, slots=True)
class WorkTypeUse:
    work_type: str
    sessions: int


@dataclass(frozen=True, slots=True)
class ProjectUse:
    name: str        # the folder's basename — the working directory's leaf
    sessions: int    # session logs in the window that worked there
    last_day: str    # ISO date of the newest of those logs


@dataclass(frozen=True, slots=True)
class HarnessFeatures:
    tool: str
    window_days: int
    sessions: int                      # log files the scan actually read
    features: tuple[FeatureUse, ...]   # non-zero only, heaviest first
    mcp_calls: tuple[tuple[str, int], ...]  # (server, calls) for connectors
    projects: tuple[ProjectUse, ...]   # most recent first; the leaf name only
    work_mix: tuple[WorkTypeUse, ...]  # sessions per work type, largest first


def _homes(env: dict[str, str] | None) -> tuple[Path, Path]:
    resolved = dict(os.environ) if env is None else env
    claude_home = resolved.get(ENV_CLAUDE_HOME)
    claude = Path(claude_home) if claude_home else Path.home() / ".claude"
    codex_home = resolved.get(ENV_CODEX_HOME) or resolved.get(ENV_CODEX_HOME_NATIVE)
    codex = Path(codex_home) if codex_home else Path.home() / ".codex"
    return claude, codex


def _recent_files(root: Path, cutoff: float) -> list[Path]:
    recent: list[Path] = []
    try:
        for path in root.rglob("*.jsonl"):
            try:
                if path.stat().st_mtime >= cutoff:
                    recent.append(path)
            except OSError:
                continue
    except OSError:
        return []
    return recent


def _is_subagent_transcript(path: Path) -> bool:
    """Claude Code writes each subagent's transcript under the parent
    session's ``<session-id>/subagents/`` folder. Those are parts of one
    session, not sessions: their tool calls are real feature use and are
    counted, but they never add a session to the window, a bucket to the
    work mix, or a visit to a project folder."""
    return "subagents" in path.parts[:-1]


def _project_leaf(cwd: object) -> str | None:
    """The working directory's basename, whichever OS wrote the log."""
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    leaf = re.split(r"[\\/]+", cwd.strip().rstrip("\\/"))[-1]
    return leaf if 0 < len(leaf) <= 80 else None


def _note_project(projects: dict[str, tuple[int, float]], leaf: str,
                  mtime: float) -> None:
    sessions, newest = projects.get(leaf, (0, 0.0))
    projects[leaf] = (sessions + 1, max(newest, mtime))


def _scan_claude(root: Path, cutoff: float) -> HarnessFeatures:
    counts: dict[str, int] = {}
    mcp: dict[str, int] = {}
    projects: dict[str, tuple[int, float]] = {}
    mix: dict[str, int] = {}
    files = _recent_files(root, cutoff)
    sessions = 0
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        part_of_parent = _is_subagent_transcript(path)
        leaf: str | None = None
        f_office = f_edit = f_web = f_tools = False
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for index, line in enumerate(handle):
                    # The working folder rides every record; the head of the
                    # file is enough to learn which project this session is.
                    if leaf is None and index < 5:
                        try:
                            head = json.loads(line)
                        except ValueError:
                            head = None
                        if isinstance(head, dict):
                            leaf = _project_leaf(head.get("cwd"))
                    if '"tool_use"' not in line:
                        continue  # cheap pre-filter; the parse decides
                    if not f_office and _OFFICE_MARK.search(line):
                        f_office = True
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if leaf is None:
                        leaf = _project_leaf(record.get("cwd"))
                    message = record.get("message")
                    if not isinstance(message, dict):
                        continue
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if (not isinstance(block, dict)
                                or block.get("type") != "tool_use"):
                            continue
                        name = block.get("name")
                        if not isinstance(name, str):
                            continue
                        f_tools = True
                        if name in _CLAUDE_EDIT_TOOLS:
                            f_edit = True
                        if name.startswith("mcp__"):
                            parts = name.split("__")
                            server = parts[1] if len(parts) > 2 else ""
                            if server in _CLAUDE_INTERNAL_BROWSER:
                                counts["browser"] = counts.get("browser", 0) + 1
                            elif server:
                                mcp[server] = mcp.get(server, 0) + 1
                            continue
                        feature = _CLAUDE_TOOL_FEATURE.get(name)
                        if feature is not None:
                            counts[feature] = counts.get(feature, 0) + 1
                            if feature == "web":
                                f_web = True
        except OSError:
            continue
        if part_of_parent:
            continue
        sessions += 1
        if leaf is not None:
            _note_project(projects, leaf, mtime)
        bucket = _classify_session(f_office, f_edit, f_web, f_tools)
        mix[bucket] = mix.get(bucket, 0) + 1
    return _pack("claude_code", counts, mcp, sessions, projects, mix)


class _PairedEvents(TypedDict):
    calls: set[str]
    events: set[str]
    call_count: int
    event_count: int


def _note_codex_feature(
    counts: dict[str, int], paired: dict[str, _PairedEvents],
    feature: str, payload: dict[str, object], event: bool = False,
) -> None:
    if feature not in paired:
        counts[feature] = counts.get(feature, 0) + 1
        return
    pair = paired[feature]
    identity = payload.get("call_id") or payload.get("id")
    if isinstance(identity, str) and identity:
        (pair["events"] if event else pair["calls"]).add(identity)
    elif event:
        pair["event_count"] += 1
    else:
        pair["call_count"] += 1


def _scan_codex(root: Path, cutoff: float) -> HarnessFeatures:
    counts: dict[str, int] = {}
    mcp: dict[str, int] = {}
    projects: dict[str, tuple[int, float]] = {}
    mix: dict[str, int] = {}
    files = _recent_files(root, cutoff)
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        leaf: str | None = None
        f_office = f_edit = f_web = f_tools = False
        # Call IDs join modern records. Legacy id-less call/completion pairs
        # use the larger count rather than counting every edit/search twice.
        paired: dict[str, _PairedEvents] = {
            key: {"calls": set(), "events": set(), "call_count": 0, "event_count": 0}
            for key in ("apply_patch", "web")
        }
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if leaf is None and "cwd" in payload:
                        leaf = _project_leaf(payload.get("cwd"))
                    kind = payload.get("type")
                    if kind in ("custom_tool_call", "function_call"):
                        f_tools = True
                        if not f_office and _OFFICE_MARK.search(line):
                            f_office = True
                        name = payload.get("name")
                        feature = _codex_name_feature(name)
                        if feature is not None:
                            _note_codex_feature(counts, paired, feature, payload)
                            f_edit |= feature == "apply_patch"
                            f_web |= feature == "web"
                    elif kind == "mcp_tool_call_end":
                        f_tools = True
                        invocation = payload.get("invocation")
                        server = (invocation.get("server")
                                  if isinstance(invocation, dict) else None)
                        if isinstance(server, str) and server:
                            mcp[server] = mcp.get(server, 0) + 1
                    elif kind in _CODEX_EVENT_FEATURE:
                        feature = _CODEX_EVENT_FEATURE[kind]
                        _note_codex_feature(
                            counts, paired, feature, payload, event=kind != "web_search_call",
                        )
                        if feature == "web":
                            f_web = True
                        elif feature == "apply_patch":
                            f_edit = True
                        f_tools = True
        except OSError:
            continue
        for feature, pair in paired.items():
            calls, events = pair["calls"], pair["events"]
            observed = len(calls | events)
            # Id-less legacy completions can correspond to identified calls.
            observed = max(observed, len(calls) + pair["call_count"],
                           len(events) + pair["event_count"])
            if observed:
                counts[feature] = counts.get(feature, 0) + observed
        if leaf is not None:
            _note_project(projects, leaf, mtime)
        bucket = _classify_session(f_office, f_edit, f_web, f_tools)
        mix[bucket] = mix.get(bucket, 0) + 1
    return _pack("codex", counts, mcp, len(files), projects, mix)


_MAX_PROJECTS = 20


def _pack(tool: str, counts: dict[str, int], mcp: dict[str, int],
          sessions: int,
          projects: dict[str, tuple[int, float]],
          mix: dict[str, int]) -> HarnessFeatures:
    ordered = tuple(
        FeatureUse(feature, count)
        for feature, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0]))
        if feature in FEATURE_DEFS and count > 0
    )
    servers = tuple(sorted(mcp.items(), key=lambda item: (-item[1], item[0])))
    worked = tuple(
        ProjectUse(
            leaf, count,
            time.strftime("%Y-%m-%d", time.localtime(newest)),
        )
        for leaf, (count, newest) in sorted(
            projects.items(), key=lambda item: (-item[1][1], item[0]))
    )[:_MAX_PROJECTS]
    work_ids = {work_id for work_id, _, _ in WORK_TYPES}
    mixed = tuple(
        WorkTypeUse(work_type, count)
        for work_type, count in sorted(
            mix.items(), key=lambda item: (-item[1], item[0]))
        if work_type in work_ids and count > 0
    )
    return HarnessFeatures(tool, WINDOW_DAYS, sessions, ordered, servers,
                           worked, mixed)


def read_features(env: dict[str, str] | None = None,
                  now: float | None = None) -> tuple[HarnessFeatures, ...]:
    """Scan the last WINDOW_DAYS of both harnesses' session logs and
    classify what each client's functionality was used for."""
    claude, codex = _homes(env)
    cutoff = (now if now is not None else time.time()) - WINDOW_DAYS * 86400
    return (
        _scan_claude(claude / "projects", cutoff),
        _scan_codex(codex / "sessions", cutoff),
    )
