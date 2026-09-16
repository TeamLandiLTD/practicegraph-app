"""OpenAI Codex CLI rollout-log adapter (FR-SRC-1).

Codex writes one JSONL "rollout" file per session under
``<codex home>/sessions/`` (date-partitioned) and ``archived_sessions/``.
Lines carry a top-level ``type`` (session_meta, turn_context, response_item,
event_msg, ...) with a ``payload``.

Accounting rules cross-checked against CodexBar's parser (parser v2):
- The CLI can emit several ``token_count`` events repeating the same
  ``last_token_usage`` for one turn. Per-turn deltas are therefore derived
  from ``total_token_usage`` against a per-file baseline whenever totals are
  present; ``last_token_usage`` is only trusted when totals are absent.
  Totals that go backwards (compaction/restart) reset the baseline.
- ``cached_input_tokens`` is a SUBSET of ``input_tokens`` (OpenAI semantics);
  normalized so ``TokenCounts.input`` excludes cached tokens. The alias
  ``cache_read_input_tokens`` is accepted (drift guard).
- The model comes from ``turn_context`` (authoritative).
- Zero-delta token counts produce no event.

Parser v4 (P1 execution quality): older rollouts report command outcomes via
``exec_command_end`` (exit_code, duration) and ``patch_apply_end`` (success);
newer rollouts (mid-2025+) report them only as ``function_call_output``
response items, whose ``output`` string is probed transiently for the fixed
``execution error:`` prefix (no structural duration exists there). Both
become pending counters flushed onto the next turn event, like
``pending_tool_calls`` — command strings and output are never stored.

Parser v6 (P2 session character): a top-level ``"type":"compacted"`` record
(real-log shape verified 2026-07-05) marks a context compaction; it becomes
a pending counter flushed onto the next turn event exactly like
``pending_tool_calls``. The compacted payload is never read.

Parser v7 (P4 economics): some ``token_count`` payloads carry the provider's
rate-limit gauge (``rate_limits.primary`` — shape verified 2026-07-05). The
reading becomes a :data:`~practicegraph.sources.RateLimitGauge` on the parse
result — integers only (percent in tenths, window minutes), independent of
whether the token delta produced a turn event. LOCAL-ONLY forever.

Parser v8 (waved-through approvals): the ``user_message`` event_msg carries
the human's message string and follows the ``response_item`` user message
that already synthesized the user turn (real-log order verified
2026-07-05), so it is un-ignored ONLY to probe the string's length
transiently and mark that most recent user turn ``short_reply`` — emitting
a second turn here would double-count human turns. Context injections
(instructions, environment) ride user-role response_items WITHOUT a
user_message event and are never marked. Caveat, accepted: slash-command
expansions can inflate some codex user_message strings past the human's
actual keystrokes, so a handful of "go"-class replies read long — the flag
under-counts, never over-counts.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from practicegraph.events import CapabilityClass, TokenCounts, Tool, TurnEvent, TurnKind
from practicegraph.sources import (
    SHORT_REPLY_MAX_CHARS,
    ParseHealth,
    ParseResult,
    QuotaSnapshot,
    RateLimitGauge,
    candidate_homes,
    classify_command,
    iter_jsonl_lines,
    parse_timestamp,
)
from practicegraph.sources.replay import ReplayPlan, prepare


def _identity_hash16(value: object) -> str:
    """Hash an environment identity transiently; never retain the value."""
    if isinstance(value, str) and value:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return ""

ENV_CODEX_HOME = "PRACTICEGRAPH_CODEX_HOME"
# Tool-specific env honored per FR-SRC-2's resolution chain.
ENV_CODEX_HOME_NATIVE = "CODEX_HOME"

_KNOWN_TOP_LEVEL_KEYS = frozenset({"timestamp", "type", "payload"})

_IGNORED_RESPONSE_ITEMS = frozenset(
    {
        "reasoning",
        "local_shell_call",
        "local_shell_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "web_search_call",
        "ghost_commit",
    }
)

_IGNORED_EVENT_MSGS = frozenset(
    {
        "agent_message",
        "agent_message_delta",
        "agent_reasoning",
        "agent_reasoning_delta",
        "agent_reasoning_section_break",
        # "user_message" is handled (v8): its string is length-probed to mark
        # the already-synthesized user turn short_reply — never re-emitted.
        "task_started",
        "task_complete",
        "exec_command_begin",
        "exec_command_output_delta",
        "mcp_tool_call_begin",
        "mcp_tool_call_end",
        "patch_apply_begin",
        "turn_diff",
        "background_event",
    }
)

# P1 execution-quality threshold (named constant, SIGNALS_EXECUTION_PLAN §P1).
SLOW_COMMAND_MIN_S = 30  # a command at/over this duration counts as slow


# The shell tool's `arguments` payload is a JSON-encoded object carrying the
# command. Larger than this and we skip rather than decode — a classifier must
# never be the reason a pathological line costs memory (FR-SRC-5 fail-open).
_ARGUMENTS_DECODE_MAX_BYTES = 4096


# Bounded scan window for apply_patch arguments: enough to count the file
# markers of a normal patch; a giant patch is counted from its head only
# (an undercount, never an error).
_PATCH_SCAN_MAX_CHARS = 8192


def _patch_file_counts(arguments: object) -> tuple[int, int]:
    """(files_created, files_edited) from an apply_patch call's markers —
    "*** Add File" / "*** Update File" — scanned in a bounded window of the
    JSON-encoded arguments. No path is extracted; markers are only counted.
    Codex patches carry repo-relative paths, so doc/export classification is
    deliberately not attempted here (stated undercount)."""
    if not isinstance(arguments, str) or not arguments:
        return (0, 0)
    head = arguments[:_PATCH_SCAN_MAX_CHARS]
    return (head.count("*** Add File"), head.count("*** Update File"))


def _classify_arguments(arguments: object) -> tuple[int, int]:
    """(git_commit, test_run) flags for a shell function_call. The JSON is
    decoded transiently, only the `command` / `cmd` value reaches the shared bounded
    classifier, and nothing of either survives this scope. Older rollout
    formats (local_shell_call era) carry no function_call and simply count
    zero — an ordinary undercount the reading's floors tolerate, chosen over
    a second hook that could double-count on formats carrying both shapes
    (the function_call_output run-counter precedent)."""
    if (
        not isinstance(arguments, str)
        or not arguments
        or len(arguments) > _ARGUMENTS_DECODE_MAX_BYTES
    ):
        return (0, 0)
    try:
        decoded = json.loads(arguments)
    except ValueError:
        return (0, 0)
    if not isinstance(decoded, dict):
        return (0, 0)
    command = decoded.get("command")
    return classify_command(command if command is not None else decoded.get("cmd"))


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _pct_tenths(value: object) -> int | None:
    """``used_percent`` (e.g. 87.5) as integer tenths (875), half-up. None for
    anything that is not a non-negative number (bools excluded) — the gauge is
    then simply not recorded (fail-open, FR-SRC-5)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value < 0:
        return None
    return int(value * 10 + 0.5)


@dataclass(frozen=True, slots=True)
class _Usage:
    input: int
    cached: int
    output: int
    reasoning: int

    def minus(self, other: _Usage) -> _Usage | None:
        """Per-field difference; None marks divergence (any field regressed)."""
        fields = (
            self.input - other.input,
            self.cached - other.cached,
            self.output - other.output,
            self.reasoning - other.reasoning,
        )
        if any(value < 0 for value in fields):
            return None
        return _Usage(*fields)

    @property
    def is_zero(self) -> bool:
        return self.input == 0 and self.cached == 0 and self.output == 0


def _usage_from(obj: object) -> _Usage | None:
    if not isinstance(obj, dict):
        return None
    cached = obj.get("cached_input_tokens", obj.get("cache_read_input_tokens"))
    return _Usage(
        input=_as_int(obj.get("input_tokens")),
        cached=_as_int(cached),
        output=_as_int(obj.get("output_tokens")),
        reasoning=_as_int(obj.get("reasoning_output_tokens")),
    )


class CodexAdapter:
    source_id = "codex_cli"
    tool = Tool.CODEX
    capability = CapabilityClass.DEEP_SESSION_LOG
    parser_version = 13  # v13: conservative fork replay and both quota windows

    def discover(self, env: dict[str, str]) -> list[Path]:
        """FR-SRC-2: agent override > profile scan (service context) >
        CODEX_HOME > per-user default (~/.codex). Any ``*.jsonl`` under
        sessions/ (at any depth) plus archived_sessions/."""
        override = env.get(ENV_CODEX_HOME) or env.get(ENV_CODEX_HOME_NATIVE)
        if override:
            codex_homes = [Path(override)]
        else:
            profile_dirs = candidate_homes(env)
            if profile_dirs is not None:
                codex_homes = [home / ".codex" for home in profile_dirs]
            else:
                codex_homes = [Path.home() / ".codex"]
        files: set[Path] = set()
        for home in codex_homes:
            sessions = home / "sessions"
            if sessions.is_dir():
                files.update(sessions.rglob("*.jsonl"))
            archived = home / "archived_sessions"
            if archived.is_dir():
                files.update(archived.glob("*.jsonl"))
        return sorted(files, key=str)

    def parse_file_seen(self, path: Path, seen_keys: set[str]) -> ParseResult:
        # Fork dependencies are reconciled by parse_many / history.ingest.
        # Unrelated sessions must never collide merely on cumulative totals.
        return self.parse_file(path)

    def parse_many(self, paths: list[Path]) -> ParseResult:
        health = ParseHealth()
        events: list[TurnEvent] = []
        gauges: list[RateLimitGauge] = []
        snapshots: list[QuotaSnapshot] = []
        plans = prepare(paths)
        for path in paths:
            try:
                result = self.parse_file(path, plans.get(path))
            except OSError:
                health.malformed += 1
                continue
            events.extend(result.events)
            gauges.extend(result.gauges)
            snapshots.extend(result.quota_snapshots)
            health.merge(result.health)
        return ParseResult(events=tuple(events), health=health, gauges=tuple(gauges),
                           quota_snapshots=tuple(snapshots))

    def parse_file(self, path: Path, replay: ReplayPlan | None = None) -> ParseResult:
        health = ParseHealth()
        events: list[TurnEvent] = []
        gauges: list[RateLimitGauge] = []
        snapshots: list[QuotaSnapshot] = []
        replay_remaining = replay.records if replay else 0
        session_id = path.stem
        meta_seen = False
        model = "unknown"
        totals_baseline: _Usage | None = None
        pending_tool_calls = 0
        pending_retries = 0
        pending_interruptions = 0
        pending_rework_edits = 0
        pending_commands_run = 0
        pending_commands_failed = 0
        pending_commands_slow = 0
        pending_compactions = 0
        pending_git_commits = 0
        pending_test_runs = 0
        pending_files_created = 0
        pending_files_edited = 0
        last_user_index: int | None = None  # v8: the turn a user_message marks
        cwd_hash = ""

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

            if not _KNOWN_TOP_LEVEL_KEYS.issuperset(record.keys()):
                health.unknown_field += 1

            record_type = record.get("type")
            payload = record.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            timestamp = parse_timestamp(record.get("timestamp"))

            if record_type != "session_meta" and replay_remaining > 0:
                replay_remaining -= 1
                if record_type == "turn_context":
                    value = payload.get("model") or payload.get("model_name")
                    if isinstance(value, str) and value:
                        model = value
                elif record_type == "event_msg" and payload.get("type") == "token_count":
                    _, totals_baseline = self._turn_delta(payload, totals_baseline)
                health.skipped += 1
                continue

            if record_type == "session_meta":
                if meta_seen:
                    health.skipped += 1
                    continue  # copied parent metadata cannot rename the child
                meta_id = payload.get("id") or payload.get("session_id")
                if isinstance(meta_id, str) and meta_id:
                    session_id = meta_id
                    meta_seen = True
                cwd_hash = _identity_hash16(payload.get("cwd")) or cwd_hash
                health.parsed += 1
            elif record_type == "turn_context":
                cwd_hash = _identity_hash16(payload.get("cwd")) or cwd_hash
                context_model = payload.get("model") or payload.get("model_name")
                if isinstance(context_model, str) and context_model:
                    model = context_model
                health.parsed += 1
            elif record_type == "response_item":
                item_type = payload.get("type")
                if item_type == "message":
                    if timestamp is None:
                        health.malformed += 1
                        continue
                    if payload.get("role") == "user":
                        last_user_index = len(events)
                        events.append(
                            TurnEvent(
                                timestamp=timestamp,
                                session_id=session_id,
                                source_id=self.source_id,
                                tool=self.tool,
                                kind=TurnKind.USER_TURN,
                                model="unknown",
                                tokens=TokenCounts(),
                                cwd_hash=cwd_hash,
                                content_present=bool(payload.get("content")),
                                path_present=bool(cwd_hash),
                                identity_present=False,
                            )
                        )
                    # Assistant message text is ignored: token accounting for
                    # assistant turns arrives via token_count events.
                    health.parsed += 1
                elif item_type in ("function_call", "custom_tool_call"):
                    pending_tool_calls += 1
                    name = payload.get("name")
                    name = name.rsplit(".", 1)[-1] if isinstance(name, str) else ""
                    # W3.1 denominator: newer rollouts carry the command only
                    # here, JSON-encoded in `arguments`. Decoded transiently
                    # (bounded — a pathological payload is skipped, zeros) for
                    # the shell tools alone, classified, discarded. The name
                    # set is real-log calibrated: 2026-07-29 probe of this
                    # machine's rollouts found "shell_command" (5,220 calls)
                    # and "exec_command" (627) where the spec-era name was
                    # "shell" — the original filter classified NOTHING on
                    # current formats.
                    if name in ("shell", "shell_command", "exec_command"):
                        commit, test = _classify_arguments(
                            payload.get("arguments")
                        )
                        pending_git_commits += commit
                        pending_test_runs += test
                    # W4.1 artifacts: apply_patch is codex's file-writing
                    # surface where it appears; markers counted, paths never
                    # read (docs and exports stay 0 — stated undercount).
                    elif name == "apply_patch":
                        created, edited = _patch_file_counts(
                            payload.get("input") if item_type == "custom_tool_call"
                            else payload.get("arguments")
                        )
                        pending_files_created += created
                        pending_files_edited += edited
                    health.parsed += 1
                elif item_type in ("function_call_output", "custom_tool_call_output"):
                    # The run counter, uniform across rollout formats (and
                    # semantics-parallel to Claude's tool_result counting):
                    # one function_call_output = one tool run. Failure is the
                    # fixed "execution error:" prefix, probed transiently —
                    # only the boolean leaves this scope; legacy exit codes
                    # arrive via exec_command_end as enrichment.
                    output = payload.get("output")
                    if isinstance(output, str):
                        pending_commands_run += 1
                        if output.startswith("execution error:"):
                            pending_commands_failed += 1
                    health.parsed += 1
                elif isinstance(item_type, str) and item_type in _IGNORED_RESPONSE_ITEMS:
                    health.skipped += 1
                else:
                    health.unsupported += 1
            elif record_type == "event_msg":
                event_type = payload.get("type")
                if event_type == "token_count":
                    if timestamp is None:
                        health.malformed += 1
                        continue
                    # P4 (parser v7): some token_count payloads carry the
                    # provider's rate-limit gauge. Recorded independently of
                    # the token delta (a duplicate/zero-delta count can still
                    # bring a fresh reading) — two integers, nothing else.
                    rate_limits = payload.get("rate_limits")
                    rate_limits = rate_limits if isinstance(rate_limits, dict) else {}
                    for window_kind in ("primary", "secondary"):
                        window = (rate_limits.get(window_kind)
                                  if isinstance(rate_limits, dict) else None)
                        if not isinstance(window, dict):
                            continue
                        used_tenths = _pct_tenths(window.get("used_percent"))
                        if used_tenths is not None:
                            minutes = _as_int(window.get("window_minutes"))
                            gauges.append(
                                (timestamp, used_tenths, minutes)
                            )
                            snapshots.append(
                                QuotaSnapshot(timestamp, used_tenths, minutes, window_kind,
                                              _reset_time(window.get("resets_at")),
                                              _identity_hash16(rate_limits.get("account_id")),
                                              _identity_hash16(rate_limits.get("limit_id")))
                            )
                    delta, totals_baseline = self._turn_delta(payload, totals_baseline)
                    if delta is not None and not delta.is_zero:
                        events.append(
                            TurnEvent(
                                timestamp=timestamp,
                                session_id=session_id,
                                source_id=self.source_id,
                                tool=self.tool,
                                kind=TurnKind.ASSISTANT_TURN,
                                model=model,
                                # OpenAI semantics: cached is a subset of input.
                                tokens=TokenCounts(
                                    input=max(0, delta.input - delta.cached),
                                    output=delta.output,
                                    cached=delta.cached,
                                    reasoning=delta.reasoning,
                                ),
                                tool_calls=pending_tool_calls,
                                retries=pending_retries,
                                interruptions=pending_interruptions,
                                rework_edits=pending_rework_edits,
                                commands_run=pending_commands_run,
                                commands_failed=pending_commands_failed,
                                commands_slow=pending_commands_slow,
                                compactions=pending_compactions,
                                git_commit_attempts=pending_git_commits,
                                test_run_attempts=pending_test_runs,
                                files_created=pending_files_created,
                                files_edited=pending_files_edited,
                                cwd_hash=cwd_hash,
                                content_present=False,
                                path_present=bool(cwd_hash),
                                identity_present=False,
                            )
                        )
                        pending_tool_calls = 0
                        pending_retries = 0
                        pending_interruptions = 0
                        pending_rework_edits = 0
                        pending_commands_run = 0
                        pending_commands_failed = 0
                        pending_commands_slow = 0
                        pending_compactions = 0
                        pending_git_commits = 0
                        pending_test_runs = 0
                        pending_files_created = 0
                        pending_files_edited = 0
                    health.parsed += 1
                elif event_type == "exec_command_end":
                    # Enrichment only (exit code, duration): the RUN itself is
                    # counted by the call's function_call_output, present in
                    # every rollout format — incrementing here too would count
                    # the same shell call twice. Command lines and output are
                    # never inspected.
                    if _as_int(payload.get("exit_code")) != 0:
                        pending_commands_failed += 1
                    duration = payload.get("duration")
                    secs = _as_int(duration.get("secs")) if isinstance(duration, dict) else 0
                    if secs >= SLOW_COMMAND_MIN_S:
                        pending_commands_slow += 1
                    health.parsed += 1
                elif event_type == "patch_apply_end":
                    # A rejected patch is the rework-parity signal with Claude's
                    # userModified (P1): the edit needed another pass.
                    if payload.get("success") is False:
                        pending_rework_edits += 1
                    health.parsed += 1
                elif event_type == "user_message":
                    # v8: the human's message string, arriving AFTER the
                    # response_item that synthesized the user turn. Probed
                    # transiently for length ONLY (the _content_text_probe
                    # precedent) — nothing but the boolean survives, and no
                    # second turn is emitted (that would double-count).
                    text = payload.get("message")
                    if last_user_index is not None and isinstance(text, str):
                        events[last_user_index] = dataclasses.replace(
                            events[last_user_index],
                            human_initiated=bool(text.strip()),
                            short_reply=(
                                0 < len(text.strip()) <= SHORT_REPLY_MAX_CHARS
                            ),
                        )
                    health.parsed += 1
                elif event_type in ("error", "stream_error"):
                    pending_retries += 1
                    health.parsed += 1
                elif isinstance(event_type, str) and event_type in _IGNORED_EVENT_MSGS:
                    health.skipped += 1
                else:
                    health.unsupported += 1
            elif record_type == "turn_aborted":
                pending_interruptions += 1
                health.parsed += 1
            elif record_type == "compacted":
                # P2: a context compaction, flushed onto the next turn event
                # like pending_tool_calls. Only the count survives.
                pending_compactions += 1
                health.parsed += 1
            else:
                health.unsupported += 1

        return ParseResult(events=tuple(events), health=health, gauges=tuple(gauges),
                           quota_snapshots=tuple(snapshots))

    @staticmethod
    def _turn_delta(
        payload: dict[str, Any], baseline: _Usage | None
    ) -> tuple[_Usage | None, _Usage | None]:
        """Derive this turn's usage and the new totals baseline."""
        info = payload.get("info")
        if not isinstance(info, dict):
            return None, baseline
        last = _usage_from(info.get("last_token_usage"))
        totals = _usage_from(info.get("total_token_usage"))
        if totals is None:
            return last, baseline
        if baseline is None:
            return (last if last is not None else totals), totals
        delta = totals.minus(baseline)
        if delta is None:
            # Totals regressed (compaction/restart): trust `last`, rebase.
            return last, totals
        return delta, totals


ADAPTER = CodexAdapter()


def _reset_time(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds")
        except (ValueError, OverflowError, OSError):
            return ""
    parsed = parse_timestamp(value)
    return parsed.astimezone(UTC).isoformat(timespec="seconds") if parsed else ""
