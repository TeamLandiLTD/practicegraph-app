"""Source adapter framework (FR-SRC).

Adapters read the local activity logs of AI tools without modifying those
tools (FR-SRC-1), normalize records into :class:`~practicegraph.events.TurnEvent`,
and tolerate drift fail-open (FR-SRC-5): unknown record kinds, unknown fields,
and malformed lines are *counted*, never fatal, and the literal unknown
token/name is never recorded (FR-SRC-6).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from practicegraph.events import CapabilityClass, Tool, TurnEvent

ENV_SCAN_PROFILES = "PRACTICEGRAPH_SCAN_PROFILES"
ENV_PROFILES_ROOT = "PRACTICEGRAPH_PROFILES_ROOT"

# Short human replies are a length observation; they cannot establish whether
# someone reviewed the work. Shared by both adapters; only the boolean leaves the
# parser — the text itself is probed transiently (the _content_text_probe /
# _INTERRUPTION_MARKER precedent) and NEVER stored or matched (FR-SRC-4).
# Calibrated on 50 real Claude main transcripts 2026-07-05: human replies
# (string-content user records) ran chars p25=31 p50=293 p75=519 p90=6452;
# 17% were <=20 chars ("go", "yes", "do it", "1") — the cutoff isolates the
# reflexive-approval class without touching the median reply.
SHORT_REPLY_MAX_CHARS = 20

# Service-account and template profiles that never hold developer tool logs.
_EXCLUDED_PROFILE_NAMES = frozenset(
    {"public", "default", "default user", "all users", "wdagutilityaccount", "defaultapppool"}
)

# --- The denominator classifier (W3.1, docs/DENOMINATOR_SPIKE.md §2) --------
#
# The ONE place any adapter may look at command text, and only like this: at
# most COMMAND_CLASSIFY_MAX_BYTES of the string, split on shell separators,
# each segment tested against anchored patterns, then discarded. The string is
# never stored, logged, hashed, or error-reported; only two 0/1 flags leave
# this function. Two closed classes — growing this vocabulary is a plan
# amendment, not a code edit.
COMMAND_CLASSIFY_MAX_BYTES = 160

# Compound commands are the norm ("cd repo && git commit -m ..."), so each
# separator-delimited segment gets its own anchored test. The separator set is
# structural shell syntax, not content interpretation.
_COMMAND_SEPARATORS = re.compile(r"(?:&&|\|\||[;|\n])")
_GIT_COMMIT = re.compile(r"^\s*git\s+commit\b")
_TEST_RUN = re.compile(
    r"^\s*(?:uv\s+run\s+)?(?:python[0-9.]*\s+-m\s+)?"
    r"(?:pytest\b|ctest\b|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b"
    r"|npx\s+(?:vitest|jest)\b|cargo\s+test\b|go\s+test\b|dotnet\s+test\b)"
)
# argv-vector commands ("bash -lc <payload>") carry the classifiable text in
# the shell payload slot; anything else joins as-is and the anchors decide.
_SHELL_WRAPPERS = frozenset({"bash", "sh", "zsh", "dash"})
_SHELL_FLAGS = frozenset({"-c", "-lc", "-cl"})


# --- The artifact classifier (W4.1) -----------------------------------------
#
# Same discipline as the command classifier above: a bounded, transient read
# of a file PATH from a tool call, reduced to closed-vocabulary counters. The
# path is never stored, logged, or hashed; only class flags leave. Extension
# sets are closed; growing them is a plan amendment. Calibrated on the real
# history 2026-07-29: 1,631 Write calls split 681 code / 648 doc / 132 data,
# with 226 landing outside the project (the delivery signal).
FILE_CLASSIFY_MAX_CHARS = 512

_DOC_SUFFIXES = frozenset({".md", ".txt", ".rst", ".docx", ".pdf", ".html", ".htm", ".adoc"})
_DATA_SUFFIXES = frozenset(
    {".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".ini", ".cfg", ".lock", ".xlsx"}
)
# Anything with a known code/config suffix or an unknown one counts as a
# plain file; the reading only distinguishes documents (the class commit
# gating misses most) and destination.
_SCRATCH_MARKERS = ("/tmp/", "/temp/", "scratchpad", "/pytest-of-")


def classify_written_file(file_path: object, cwd: object) -> tuple[int, int, int]:
    """(created, doc, exported) flags for one file-writing tool call.

    created=0 means the write was scratch (temp dirs, scratchpads) and does
    not count as an artifact at all. doc marks the closed document-suffix
    set. exported marks a non-scratch write landing OUTSIDE the recorded
    working directory — the closest structural signal to a delivery a log
    can carry. Absent or oversized shapes are zeros, never an error."""
    if not isinstance(file_path, str) or not file_path:
        return (0, 0, 0)
    if len(file_path) > FILE_CLASSIFY_MAX_CHARS:
        return (0, 0, 0)
    path = file_path.lower().replace("\\", "/")
    if any(marker in path for marker in _SCRATCH_MARKERS):
        return (0, 0, 0)
    name = path.rsplit("/", 1)[-1]
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    doc = 1 if suffix in _DOC_SUFFIXES else 0
    exported = 0
    if isinstance(cwd, str) and cwd:
        base = cwd.lower().replace("\\", "/").rstrip("/") + "/"
        if not path.startswith(base):
            exported = 1
    return (1, doc, exported)


def classify_command(command: object) -> tuple[int, int]:
    """(git_commit, test_run) flags — each 0/1 per command invocation, so a
    compound line counts once per class, never once per segment. Absent or
    unrecognized shapes are (0, 0), never an error (FR-SRC-5)."""
    if isinstance(command, list):
        parts = [p for p in command if isinstance(p, str)]
        if (
            len(parts) >= 3
            and parts[0].rsplit("/", 1)[-1] in _SHELL_WRAPPERS
            and parts[1] in _SHELL_FLAGS
        ):
            command = parts[2]
        else:
            command = " ".join(parts)
    if not isinstance(command, str) or not command:
        return (0, 0)
    head = command[:COMMAND_CLASSIFY_MAX_BYTES]
    commit = 0
    test = 0
    for segment in _COMMAND_SEPARATORS.split(head):
        if _GIT_COMMIT.match(segment):
            commit = 1
        if _TEST_RUN.match(segment):
            test = 1
    return (commit, test)


def candidate_homes(env: dict[str, str]) -> list[Path] | None:
    """Legacy profile-scanning flags cannot merge other people's activity.

    Explicit per-tool roots remain supported; default discovery is per-user.
    """
    return None


@dataclass(slots=True)
class ParseHealth:
    """Per-source parse-health counters (FR-SRC-6). Counts only — no names."""

    seen: int = 0
    parsed: int = 0
    skipped: int = 0  # recognized record kinds we deliberately ignore
    malformed: int = 0  # unparseable lines / records missing required fields
    unknown_field: int = 0  # records carrying field names we do not recognize
    unsupported: int = 0  # record kinds we do not recognize at all

    @property
    def drift_detected(self) -> bool:
        return self.malformed > 0 or self.unknown_field > 0 or self.unsupported > 0

    def merge(self, other: ParseHealth) -> None:
        self.seen += other.seen
        self.parsed += other.parsed
        self.skipped += other.skipped
        self.malformed += other.malformed
        self.unknown_field += other.unknown_field
        self.unsupported += other.unsupported


# One provider rate-limit gauge reading (P4, SIGNALS_EXECUTION_PLAN §P4):
# (timestamp, used_pct_tenths, window_minutes). Deliberately NOT a TurnEvent
# field — gauges are point-in-time readings, not turn counters — and the data
# is LOCAL-ONLY forever (never on the wire; enforced by the field-name scan).
RateLimitGauge = tuple[datetime, int, int]


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    timestamp: datetime
    used_pct_tenths: int
    window_minutes: int
    window_kind: str = ""
    resets_at: str = ""
    account_hash: str = ""
    pool_hash: str = ""


@dataclass(frozen=True, slots=True)
class ParseResult:
    events: tuple[TurnEvent, ...]
    health: ParseHealth
    # Turn identity keys this file consumed (cross-file dedup, persisted for
    # incremental re-parses — FR-SRC-7). Empty for sources without dedup.
    dedup_keys: tuple[str, ...] = ()
    # Rate-limit gauge readings found in this file (P4). Defaults empty so
    # sources without the signal are untouched.
    gauges: tuple[RateLimitGauge, ...] = ()
    quota_snapshots: tuple[QuotaSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceHealthRow:
    """Health of one source lane, as surfaced in reports and doctor (FR-SRC-6)."""

    source_id: str
    capability: CapabilityClass
    parser_version: int
    health: ParseHealth


class SourceAdapter(Protocol):
    """A capability-classified source of TurnEvents (FR-SRC-3)."""

    source_id: str
    tool: Tool
    capability: CapabilityClass
    parser_version: int

    def discover(self, env: dict[str, str]) -> list[Path]:
        """Locate readable log files; deterministic order; [] when absent."""
        ...

    def parse_file(self, path: Path) -> ParseResult:
        """Parse one file fail-open (FR-SRC-5)."""
        ...

    def parse_file_seen(self, path: Path, seen_keys: set[str]) -> ParseResult:
        """Parse one file, skipping turns whose identity key was already
        consumed by another file (incremental cross-file dedup)."""
        ...

    def parse_many(self, paths: list[Path]) -> ParseResult:
        """Parse a set of files with any cross-file reconciliation the source
        needs (e.g. parent/subagent transcript deduplication). Fail-open."""
        ...


def parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are interpreted as UTC so the
    result is always timezone-aware (FR-CFG-5). None on anything unparseable."""
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def iter_jsonl_lines(path: Path) -> Iterator[str]:
    """Read a JSONL file tolerantly: encoding errors are replaced, blank lines
    dropped. Raises OSError only if the file itself cannot be read."""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield line
