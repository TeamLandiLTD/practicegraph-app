"""Claude Code CLI session-log adapter (FR-SRC-1).

Claude Code writes one JSONL file per session under ``<config dir>/projects``
(subagent transcripts nest deeper, under ``.../subagents/``). Records carry a
``type`` discriminator; ``user`` and ``assistant`` records become TurnEvents.

Accounting rules cross-checked against CodexBar's parser (parser v2):
- Streaming writes the same API call repeatedly with *cumulative* usage under
  the same ``message.id + requestId`` — records are deduplicated last-wins,
  never summed.
- The same API call can appear in both a parent and a subagent transcript —
  a key already consumed by an earlier file is dropped (files are parsed in
  sorted order, so parent transcripts win).
- Anthropic usage semantics: ``input_tokens`` excludes cache reads/creation.

Parser v4 (P1 execution quality): user records carry tool outcomes as
``message.content`` blocks of type ``tool_result``; each block counts as a
command run and its ``is_error`` flag marks a failure. ``toolUseResult`` is
probed structurally for ``userModified`` (rework). No structural exit code
or duration exists in current logs — a ``durationMs`` probe is kept only as
a harmless forward-compat check. Counters only; result text is never read.

Parser v5 (P2 session character): a compaction lands as a user record with
top-level ``isCompactSummary: true`` (real-log shape verified 2026-07-05);
the flag becomes ``compactions=1`` on that turn — the summary text itself
is never read.

Parser v6 (P3 environment context): every record in a main transcript
carries ``isSidechain: false`` while subagent (sidechain) transcript files
carry ``isSidechain: true`` on every line (real-log shape verified
2026-07-05) — the flag becomes ``interactive`` (absent defaults to
interactive). ``cwd``/``gitBranch`` are reduced to 16-hex truncated sha256
identity hashes (the ``history.path_identity`` precedent) so cardinality
can be counted without the values ever being stored.

Parser v7 (waved-through approvals): human turns are user records whose
``message.content`` is a STRING (tool results arrive as lists — real-log
shape verified 2026-07-05); such a turn gets ``short_reply=True`` when the
stripped text length is 1..SHORT_REPLY_MAX_CHARS. Length only — the text is
probed transiently and never stored or matched.

Only counters, enums, timestamps, and presence flags leave this module —
message content, cwd values, and identity fields are inspected transiently
and never stored (FR-SRC-4, INV-1).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from practicegraph.events import CapabilityClass, TokenCounts, Tool, TurnEvent, TurnKind
from practicegraph.sources import (
    SHORT_REPLY_MAX_CHARS,
    ParseHealth,
    ParseResult,
    candidate_homes,
    classify_command,
    classify_written_file,
    iter_jsonl_lines,
    parse_timestamp,
)

ENV_CLAUDE_HOME = "PRACTICEGRAPH_CLAUDE_HOME"
# The tool's own multi-root override (comma-separated), honored per FR-SRC-2.
ENV_CLAUDE_CONFIG_DIR = "CLAUDE_CONFIG_DIR"
# Test seam: overrides the macOS "~/Library/Application Support/Claude" base so
# the Claude Desktop discovery can be exercised on any OS (mirrors the
# PRACTICEGRAPH_PROFILES_ROOT seam in sources/__init__.py).
ENV_CLAUDE_DESKTOP_SUPPORT = "PRACTICEGRAPH_CLAUDE_DESKTOP"

# Claude Desktop embeds coding sessions on macOS under its Application Support
# directory, each nested one session dir deep with its own ``.claude/projects``
# store (verified against CodexBar docs/claude.md — the reference parser). The
# CLI-only roots (~/.claude, ~/.config/claude) miss these entirely, so on a Mac
# where the user drives Claude *Desktop* (not the CLI) PracticeGraph would see
# nothing. These two subtrees carry the same JSONL shape; cross-file dedup
# (message.id + requestId) makes any overlap with a shared CLI store harmless.
# Metadata-only ``claude-code-sessions`` dirs that merely point at CLI JSONL by
# cliSessionId contribute no *.jsonl of their own, so they are naturally inert.
_CLAUDE_DESKTOP_SESSION_SUBDIRS = (
    "local-agent-mode-sessions",
    "claude-code-sessions",
)


def _claude_desktop_support_base(env: dict[str, str], home: Path) -> Path | None:
    """The Claude Desktop Application Support directory to scan, or None when
    it does not apply. An explicit override (test seam) always wins; otherwise
    this is a macOS-only location."""
    override = env.get(ENV_CLAUDE_DESKTOP_SUPPORT)
    if override:
        return Path(override)
    import sys

    if sys.platform != "darwin":
        return None
    return home / "Library" / "Application Support" / "Claude"


def _claude_desktop_project_roots(env: dict[str, str], home: Path) -> list[Path]:
    """Discover ``.claude/projects`` stores nested under Claude Desktop's
    session directories (``<support>/<session-subdir>/**/.claude/projects``).
    Returns [] when Claude Desktop is absent or not applicable."""
    base = _claude_desktop_support_base(env, home)
    if base is None or not base.is_dir():
        return []
    roots: list[Path] = []
    for subdir in _CLAUDE_DESKTOP_SESSION_SUBDIRS:
        session_base = base / subdir
        if not session_base.is_dir():
            continue
        # One session directory deep (the ``**`` in the CodexBar glob): each
        # holds its own ``.claude/projects`` tree. Find ``.claude`` dirs at any
        # depth, then take their ``projects`` child — a single-component rglob
        # avoids relying on multi-component rglob-pattern semantics.
        for claude_dir in session_base.rglob(".claude"):
            projects_dir = claude_dir / "projects"
            if projects_dir.is_dir():
                roots.append(projects_dir)
    return roots

_IGNORED_TYPES = frozenset(
    {
        "summary",
        "system",
        "progress",
        "file-history-snapshot",
        "queued-command",
    }
)

_KNOWN_RECORD_KEYS = frozenset(
    {
        "parentUuid",
        "logicalParentUuid",
        "isSidechain",
        "userType",
        "cwd",
        "sessionId",
        "version",
        "gitBranch",
        "slug",
        "type",
        "message",
        "uuid",
        "timestamp",
        "requestId",
        "isApiErrorMessage",
        "isMeta",
        "isCompactSummary",
        "toolUseResult",
        "thinkingMetadata",
        "todos",
        "usage",
    }
)

_INTERRUPTION_MARKER = "[Request interrupted"

# P1 execution-quality thresholds (named constants, SIGNALS_EXECUTION_PLAN §P1).
SLOW_COMMAND_MIN_S = 30  # a command at/over this duration counts as slow


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _identity_hash16(value: object) -> str:
    """P3: 16-hex truncated sha256 identity hash of an environment value
    (the ``history.path_identity`` precedent). Computed transiently — the
    value itself never leaves this function (FR-SRC-4). Non-strings and
    empty strings map to "" (absent)."""
    if isinstance(value, str) and value:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return ""


def _content_text_probe(content: object) -> str:
    """Flatten message content for marker checks. Transient only — the result
    must never be stored on an event or in any output (INV-1)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return " ".join(parts)
    return ""


class ClaudeCodeAdapter:
    source_id = "claude_code_cli"
    tool = Tool.CLAUDE_CODE
    capability = CapabilityClass.DEEP_SESSION_LOG
    parser_version = 10  # v10: artifact counters (W4.1)

    def discover(self, env: dict[str, str]) -> list[Path]:
        """FR-SRC-2: agent override > profile scan (service context) >
        CLAUDE_CONFIG_DIR (comma-separated multi-root) > per-user defaults
        (~/.claude and ~/.config/claude). On macOS the CLI roots are joined by
        Claude *Desktop*'s embedded session stores (see
        ``_claude_desktop_project_roots``), so a Desktop-only user is still
        seen. An explicit ``PRACTICEGRAPH_CLAUDE_HOME`` pin means "only here"
        and skips the Desktop scan (the ``PRACTICEGRAPH_CLAUDE_DESKTOP`` seam
        can still inject it for tests)."""
        roots: list[Path] = []
        override = env.get(ENV_CLAUDE_HOME)
        config_dir = env.get(ENV_CLAUDE_CONFIG_DIR)
        profile_dirs = candidate_homes(env)
        if override:
            roots.append(Path(override) / "projects")
        elif profile_dirs is not None:
            for home in profile_dirs:
                roots.append(home / ".claude" / "projects")
                roots.append(home / ".config" / "claude" / "projects")
                roots.extend(_claude_desktop_project_roots(env, home))
        elif config_dir:
            for entry in config_dir.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                root = Path(entry)
                roots.append(root if root.name == "projects" else root / "projects")
            roots.extend(_claude_desktop_project_roots(env, Path.home()))
        else:
            roots.append(Path.home() / ".claude" / "projects")
            roots.append(Path.home() / ".config" / "claude" / "projects")
            roots.extend(_claude_desktop_project_roots(env, Path.home()))
        # The test seam can drive the Desktop scan even under an explicit
        # single-root pin (override), so honor it there too when set.
        if override and env.get(ENV_CLAUDE_DESKTOP_SUPPORT):
            roots.extend(_claude_desktop_project_roots(env, Path.home()))
        files: set[Path] = set()
        for root in roots:
            if root.is_dir():
                files.update(root.rglob("*.jsonl"))
        return sorted(files, key=str)

    def parse_many(self, paths: list[Path]) -> ParseResult:
        """Parse in sorted order with cross-file deduplication (parent
        transcripts precede subagent transcripts lexicographically)."""
        health = ParseHealth()
        events: list[TurnEvent] = []
        seen_keys: set[str] = set()
        for path in paths:
            try:
                result = self._parse_file(path, seen_keys)
            except OSError:
                health.malformed += 1
                continue
            events.extend(result.events)
            health.merge(result.health)
        return ParseResult(events=tuple(events), health=health)

    def parse_file(self, path: Path) -> ParseResult:
        return self._parse_file(path, set())

    def parse_file_seen(self, path: Path, seen_keys: set[str]) -> ParseResult:
        return self._parse_file(path, set(seen_keys))

    def _parse_file(self, path: Path, seen_keys: set[str]) -> ParseResult:
        health = ParseHealth()
        events: list[TurnEvent | None] = []
        slot_by_key: dict[str, int] = {}
        fallback_session = path.stem

        for line in iter_jsonl_lines(path):
            health.seen += 1
            try:
                record = json.loads(line)
            except ValueError:
                health.malformed += 1
                continue
            if not isinstance(record, dict):
                health.malformed += 1
                continue

            record_type = record.get("type")
            if record_type not in ("user", "assistant"):
                if isinstance(record_type, str) and record_type in _IGNORED_TYPES:
                    health.skipped += 1
                else:
                    health.unsupported += 1
                continue

            if not _KNOWN_RECORD_KEYS.issuperset(record.keys()):
                health.unknown_field += 1

            timestamp = parse_timestamp(record.get("timestamp"))
            if timestamp is None:
                health.malformed += 1
                continue

            message = record.get("message")
            message = message if isinstance(message, dict) else {}
            session_raw = record.get("sessionId")
            session_id = session_raw if isinstance(session_raw, str) else fallback_session

            if record_type == "assistant":
                key = self._dedup_key(record, message)
                if key is not None and key in seen_keys:
                    # Already consumed by an earlier (parent) transcript.
                    health.skipped += 1
                    continue
                event = self._assistant_event(record, message, timestamp, session_id)
                if key is not None and key in slot_by_key:
                    # Cumulative streaming chunk: the last record wins.
                    events[slot_by_key[key]] = event
                    health.skipped += 1
                    continue
                if key is not None:
                    slot_by_key[key] = len(events)
                events.append(event)
                health.parsed += 1
            else:
                events.append(self._user_event(record, message, timestamp, session_id))
                health.parsed += 1

        seen_keys.update(slot_by_key.keys())
        final_events = tuple(event for event in events if event is not None)
        return ParseResult(
            events=final_events, health=health, dedup_keys=tuple(sorted(slot_by_key))
        )

    @staticmethod
    def _dedup_key(record: dict[str, Any], message: dict[str, Any]) -> str | None:
        message_id = message.get("id")
        request_id = record.get("requestId")
        if isinstance(message_id, str) and isinstance(request_id, str):
            return f"{message_id}:{request_id}"
        return None

    def _assistant_event(
        self,
        record: dict[str, Any],
        message: dict[str, Any],
        timestamp: datetime,
        session_id: str,
    ) -> TurnEvent:
        usage = message.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        # Anthropic usage semantics: input_tokens already excludes cache reads
        # and cache creation, which arrive in their own fields. The nested
        # cache_creation object carries the 1h-TTL share (priced at 2x input;
        # CodexBar-verified shape), clamped to the total.
        cache_creation_total = _as_int(usage.get("cache_creation_input_tokens"))
        creation_detail = usage.get("cache_creation")
        creation_1h = 0
        if isinstance(creation_detail, dict):
            creation_1h = min(
                _as_int(creation_detail.get("ephemeral_1h_input_tokens")),
                cache_creation_total,
            )
        tokens = TokenCounts(
            input=_as_int(usage.get("input_tokens")),
            output=_as_int(usage.get("output_tokens")),
            cached=_as_int(usage.get("cache_read_input_tokens")),
            cache_creation=cache_creation_total,
            cache_creation_1h=creation_1h,
        )
        content = message.get("content")
        tool_calls = 0
        git_commits = 0
        test_runs = 0
        files_created = 0
        doc_files = 0
        exports = 0
        files_edited = 0
        if isinstance(content, list):
            for block in content:
                if not (
                    isinstance(block, dict) and block.get("type") == "tool_use"
                ):
                    continue
                tool_calls += 1
                name = block.get("name")
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    continue
                # W3.1 denominator: Bash commands only, classified through the
                # shared bounded classifier — the command string is read
                # transiently and discarded (docs/DENOMINATOR_SPIKE.md §2).
                if name == "Bash":
                    commit, test = classify_command(tool_input.get("command"))
                    git_commits += commit
                    test_runs += test
                # W4.1 artifacts: file-writing tools, classified through the
                # bounded path classifier — suffix class and destination only,
                # the path itself discarded.
                elif name == "Write":
                    created, doc, exported = classify_written_file(
                        tool_input.get("file_path"), record.get("cwd")
                    )
                    files_created += created
                    doc_files += doc
                    exports += exported
                elif name in ("Edit", "MultiEdit", "NotebookEdit"):
                    files_edited += 1
        model = message.get("model")
        return TurnEvent(
            timestamp=timestamp,
            session_id=session_id,
            source_id=self.source_id,
            tool=self.tool,
            kind=TurnKind.ASSISTANT_TURN,
            model=model if isinstance(model, str) and model else "unknown",
            tokens=tokens,
            tool_calls=tool_calls,
            git_commit_attempts=git_commits,
            test_run_attempts=test_runs,
            files_created=files_created,
            doc_files_created=doc_files,
            export_writes=exports,
            files_edited=files_edited,
            retries=1 if record.get("isApiErrorMessage") is True else 0,
            # P3: sidechain (subagent) records are not human-driven turns;
            # absent flags default to interactive (fail-open).
            interactive=record.get("isSidechain") is not True,
            cwd_hash=_identity_hash16(record.get("cwd")),
            branch_hash=_identity_hash16(record.get("gitBranch")),
            content_present=bool(content),
            path_present=bool(record.get("cwd")),
            identity_present=False,
        )

    def _user_event(
        self,
        record: dict[str, Any],
        message: dict[str, Any],
        timestamp: datetime,
        session_id: str,
    ) -> TurnEvent:
        content = message.get("content")
        interrupted = _INTERRUPTION_MARKER in _content_text_probe(content)
        rework, commands_run, failed, slow = self._execution_counters(
            content, record.get("toolUseResult")
        )
        # Waved approvals (v7): a human turn is a user record whose content is
        # a STRING (tool results ride lists). Only the length is inspected,
        # transiently — the reply text never leaves this scope (FR-SRC-4).
        short_reply = (
            isinstance(content, str)
            and 0 < len(content.strip()) <= SHORT_REPLY_MAX_CHARS
        )
        human_initiated = (
            isinstance(content, str)
            and bool(content.strip())
            and not interrupted
            and record.get("isCompactSummary") is not True
            and record.get("isSidechain") is not True
        )
        return TurnEvent(
            timestamp=timestamp,
            session_id=session_id,
            source_id=self.source_id,
            tool=self.tool,
            kind=TurnKind.USER_TURN,
            model="unknown",
            tokens=TokenCounts(),
            interruptions=1 if interrupted else 0,
            rework_edits=rework,
            commands_run=commands_run,
            commands_failed=failed,
            commands_slow=slow,
            # P2: the compact-summary record is itself the compaction marker.
            compactions=1 if record.get("isCompactSummary") is True else 0,
            # P3: same environment-context rules as assistant records.
            interactive=record.get("isSidechain") is not True,
            cwd_hash=_identity_hash16(record.get("cwd")),
            branch_hash=_identity_hash16(record.get("gitBranch")),
            short_reply=short_reply,
            human_initiated=human_initiated,
            content_present=bool(content),
            path_present=bool(record.get("cwd")),
            identity_present=False,
        )

    @staticmethod
    def _execution_counters(
        content: object, tool_use_result: object
    ) -> tuple[int, int, int, int]:
        """P1 counters for a user record: (rework_edits, commands_run,
        commands_failed, commands_slow). Structural fields only — result
        text, patches, and paths are never read. Each ``tool_result``
        content block is one command run; its ``is_error`` flag marks a
        failure. Current logs carry no structural duration, so slow stays 0
        unless an int ``durationMs`` ever (re)appears in ``toolUseResult``.
        Absent/unknown shapes are all zeros, never an error (FR-SRC-5
        fail-open)."""
        commands_run = 0
        failed = 0
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    commands_run += 1
                    if block.get("is_error") is True:
                        failed += 1
        rework = 0
        slow = 0
        if isinstance(tool_use_result, dict):
            rework = 1 if tool_use_result.get("userModified") is True else 0
            if _as_int(tool_use_result.get("durationMs")) >= SLOW_COMMAND_MIN_S * 1000:
                slow = 1
        return rework, commands_run, failed, slow


ADAPTER = ClaudeCodeAdapter()
