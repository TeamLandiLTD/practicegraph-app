"""Common local event model (FR-SRC-4) and closed vocabularies.

Every vocabulary here is a *closed set* pinned by tests (NFR-PRV-2). The event
structure carries only counters, enums, timezone-aware timestamps, and boolean
presence flags. Raw content, file paths, and identity values MUST never be
stored on an event; parsers record only that such data was present (FR-SRC-4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum


class Tool(StrEnum):
    """AI tools with a supported source adapter (FR-SRC-1)."""

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


class CapabilityClass(StrEnum):
    """FR-SRC-3: closed set of capability classes a source may declare."""

    DEEP_SESSION_LOG = "deep_session_log"
    TELEMETRY_STREAM = "telemetry_stream"
    ADMIN_COMPLIANCE_FEED = "admin_compliance_feed"
    QUOTA_BILLING_FEED = "quota_billing_feed"
    EXPORT_IMPORTER = "export_importer"
    LOCAL_APP_CACHE_PROBE = "local_app_cache_probe"


class TurnKind(StrEnum):
    USER_TURN = "user_turn"
    ASSISTANT_TURN = "assistant_turn"


class EngagementCounter(StrEnum):
    """FR-RPT-7: the only engagement counters that may ever be recorded."""

    REPORT_GENERATED = "report_generated"
    TIP_SHOWN = "tip_shown"
    TIP_ACTED = "tip_acted"
    RECOMMENDATION_DISMISSED = "recommendation_dismissed"
    RECOMMENDATION_NEVER_SUGGEST = "recommendation_never_suggest"
    ENGAGEMENT_UNAVAILABLE = "engagement_unavailable"


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """Token counters for one turn.

    Invariants: ``input`` excludes cached tokens (sources that report cached
    tokens as a subset of input — OpenAI-style usage — are normalized by their
    parser); ``cache_creation`` is the TOTAL of all cache writes, of which
    ``cache_creation_1h`` is the 1-hour-TTL subset (priced at 2x input vs
    1.25x for the 5-minute default). Pricing treats the fields uniformly
    (FR-ANL-1).
    """

    input: int = 0
    output: int = 0
    cached: int = 0
    cache_creation: int = 0
    reasoning: int = 0
    cache_creation_1h: int = 0

    def add(self, other: TokenCounts) -> TokenCounts:
        return TokenCounts(
            input=self.input + other.input,
            output=self.output + other.output,
            cached=self.cached + other.cached,
            cache_creation=self.cache_creation + other.cache_creation,
            reasoning=self.reasoning + other.reasoning,
            cache_creation_1h=self.cache_creation_1h + other.cache_creation_1h,
        )


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """One normalized activity record (FR-SRC-4).

    ``session_id`` is a *local* correlation id; it never crosses the network
    (INV-3 forbids it from the wire schema).
    """

    timestamp: datetime
    session_id: str
    source_id: str
    tool: Tool
    kind: TurnKind
    model: str
    tokens: TokenCounts
    tool_calls: int = 0
    retries: int = 0
    interruptions: int = 0
    # Execution-quality counters (P1): parsers
    # derive these from exit codes / durations / review markers and emit only
    # the integers — command strings and diff bodies never leave the parser.
    rework_edits: int = 0
    commands_run: int = 0
    commands_failed: int = 0
    commands_slow: int = 0
    # The denominator counters (W3.1): commands the
    # sources.classify_command anchor-matched as a git commit / test run.
    # ATTEMPTS, not outcomes (v1 pairs no results), counted from a bounded,
    # transient read of the command string — the string itself never leaves
    # the classifier.
    git_commit_attempts: int = 0
    test_run_attempts: int = 0
    # The artifact counters (W4.1, reviewed contract change): non-scratch
    # files this turn created / created-as-documents / wrote OUTSIDE the
    # working directory (the delivery signal), plus edits to existing files.
    # Classified by sources.classify_written_file from a bounded, transient
    # read of the tool call's path — the path never gains a slot.
    files_created: int = 0
    doc_files_created: int = 0
    export_writes: int = 0
    files_edited: int = 0
    # Context compactions (P2): how often a
    # session ran long enough to compact its context — a structural marker,
    # never the summary text itself.
    compactions: int = 0
    # Environment context (P3). ``interactive``
    # is False for subagent (sidechain) transcript records so behavioral
    # metrics can gate on human-driven turns — cost accounting never filters
    # on it. ``cwd_hash``/``branch_hash`` are 16-hex truncated sha256
    # identity hashes (the ``history.path_identity`` precedent) computed
    # transiently by parsers; the cwd/branch values themselves are NEVER
    # stored ("" = not present).
    interactive: bool = True
    cwd_hash: str = ""
    branch_hash: str = ""
    # Short replies: True on a human turn whose message text was short
    # (0 < stripped length <= SHORT_REPLY_MAX_CHARS in
    # sources/__init__.py). Length only — parsers probe the text transiently
    # and never store or match it (FR-SRC-4, INV-1).
    short_reply: bool = False
    # Positive human-origin signal. True only when a source adapter recognizes
    # an actual human message/response record; main-thread structural traffic
    # remains False. Content is never retained.
    human_initiated: bool = False
    # Fast mode: True when the provider reported the turn ran at its premium
    # speed tier (Claude ``usage.speed == "fast"``). A pricing input only —
    # fast turns bill every token class at the model's fast multiplier.
    fast: bool = False
    content_present: bool = False
    path_present: bool = False
    identity_present: bool = False

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("TurnEvent.timestamp must be timezone-aware")

    def day_key(self) -> date:
        """UTC-anchored processing day (FR-CFG-5)."""
        return self.timestamp.astimezone(UTC).date()
