"""The closed wire schema (INV-3, FR-EMT-1) and its validator.

Everything that crosses the network conforms to this module: enumerated fields
only — counters, closed enums, estimates, version identifiers, a coarse
platform cohort. Free text is forbidden. Unknown fields are rejected, not
ignored. The same validator runs on the endpoint before queueing (an emit that
fails validation is a bug, not a payload) and on the server at ingest.

Validation errors are closed codes; the offending value or unknown field name
is never echoed (FR-API-3).
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any

from practicegraph.events import EngagementCounter, Tool
from practicegraph.privacy import leak_findings

# Schema v2 (2026-07-03, governed change per NFR-PRV-2): adds the closed
# work_type_sessions map and the maturity signal->level map. v1 payloads from
# older agents remain valid; the server supports both (FR-EMT-3 operational
# rule: server support deploys with/before the v2-emitting agent).
EMIT_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1, 2)

# Closed vocabularies for the v2 fields. Kept as literals here (wire is the
# contract source of truth); the analysis enums are pinned equal by tests.
WORK_TYPE_KEYS: tuple[str, ...] = ("build", "investigate", "converse", "unknown")
MATURITY_SIGNAL_KEYS: tuple[str, ...] = (
    "cache_reuse",
    "tool_usage",
    "multi_tool",
    "consistency",
)
MATURITY_LEVEL_VALUES: tuple[str, ...] = (
    "not_yet",
    "emerging",
    "developing",
    "leading",
)


class ModelFamily(StrEnum):
    """Closed model families. Raw model strings from local logs are quasi-free
    text and MUST NOT cross the wire; they are mapped to this closed set.
    (Extended 2026-07-03 with the Claude 5 tier — pre-GA, in-lockstep enum
    extension; post-GA additions would ride a schema version bump.)"""

    CLAUDE_FABLE = "claude_fable"
    CLAUDE_MYTHOS = "claude_mythos"
    CLAUDE_OPUS = "claude_opus"
    CLAUDE_SONNET = "claude_sonnet"
    CLAUDE_HAIKU = "claude_haiku"
    GPT_5_CODEX = "gpt_5_codex"
    GPT_5_MINI = "gpt_5_mini"
    GPT_5 = "gpt_5"
    GPT_4 = "gpt_4"
    O_SERIES = "o_series"
    OTHER = "other"


# Families whose spend counts as "premium" in waste analysis (FR-ANL-4).
PREMIUM_FAMILIES: tuple[str, ...] = ("claude_fable", "claude_mythos", "claude_opus")

_FAMILY_PREFIXES: tuple[tuple[str, ModelFamily], ...] = (
    ("claude-fable", ModelFamily.CLAUDE_FABLE),
    ("claude-mythos", ModelFamily.CLAUDE_MYTHOS),
    ("claude-opus", ModelFamily.CLAUDE_OPUS),
    ("claude-sonnet", ModelFamily.CLAUDE_SONNET),
    ("claude-haiku", ModelFamily.CLAUDE_HAIKU),
    ("claude-3-5-haiku", ModelFamily.CLAUDE_HAIKU),
    ("gpt-5.3-codex", ModelFamily.GPT_5_CODEX),
    ("gpt-5.4-mini", ModelFamily.GPT_5_MINI),
    ("gpt-5.4-nano", ModelFamily.GPT_5_MINI),
    ("gpt-5-codex", ModelFamily.GPT_5_CODEX),
    ("gpt-5-mini", ModelFamily.GPT_5_MINI),
    ("gpt-5-nano", ModelFamily.GPT_5_MINI),
    ("gpt-5", ModelFamily.GPT_5),
    ("gpt-4", ModelFamily.GPT_4),
    ("o3", ModelFamily.O_SERIES),
    ("o4", ModelFamily.O_SERIES),
)


def model_family(model: str) -> ModelFamily:
    for prefix, family in _FAMILY_PREFIXES:
        if model.startswith(prefix):
            return family
    return ModelFamily.OTHER


PLATFORMS: tuple[str, ...] = ("windows", "macos", "linux", "other")

TOKEN_KEYS: tuple[str, ...] = ("input", "output", "cached", "cache_creation", "reasoning")

AGENT_HEALTH_KEYS: tuple[str, ...] = (
    "seen",
    "parsed",
    "skipped",
    "malformed",
    "unknown_field",
    "unsupported",
    "sources_detected",
)

ENGAGEMENT_KEYS: tuple[str, ...] = tuple(counter.value for counter in EngagementCounter)

TOOL_VALUES: tuple[str, ...] = tuple(tool.value for tool in Tool)

_TOP_LEVEL_KEYS_V1 = frozenset(
    {
        "schema_version",
        "emit_id",
        "org_id",
        "day",
        "platform",
        "agent_version",
        "rate_card_version",
        "is_estimate",
        "contributors",
        "sessions",
        "assistant_turns",
        "user_turns",
        "tool_calls",
        "retries",
        "interruptions",
        "tokens",
        "estimated_cost_micro_usd",
        "unpriced_turns",
        "spend_by_family",
        "tools_observed",
        "engagement",
        "agent_health",
        "content_present",
        "identity_present",
    }
)
_TOP_LEVEL_KEYS_V2 = _TOP_LEVEL_KEYS_V1 | {"work_type_sessions", "maturity"}

_COUNTER_FIELDS = (
    "contributors",
    "sessions",
    "assistant_turns",
    "user_turns",
    "tool_calls",
    "retries",
    "interruptions",
    "estimated_cost_micro_usd",
    "unpriced_turns",
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ORG_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Closed validation error codes. Details (names/values) are never included.
ERR_TYPE = "type_error"
ERR_UNKNOWN_FIELD = "unknown_field"
ERR_MISSING_FIELD = "missing_field"
ERR_FORMAT = "format_error"
ERR_ENUM = "enum_error"
ERR_INVARIANT = "invariant_violation"
ERR_VALUE_LEAK = "value_leak"


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_closed_int_map(
    value: object, allowed_keys: tuple[str, ...], errors: list[str], exact: bool
) -> None:
    if not isinstance(value, dict):
        errors.append(ERR_TYPE)
        return
    keys = set(value.keys())
    allowed = set(allowed_keys)
    if not keys.issubset(allowed):
        errors.append(ERR_UNKNOWN_FIELD)
    if exact and keys != allowed:
        errors.append(ERR_MISSING_FIELD)
    for item in value.values():
        if not _is_count(item):
            errors.append(ERR_TYPE)
            break


def validate_emit(payload: object) -> list[str]:
    """Full closed-schema validation. Returns a de-duplicated, ordered list of
    closed error codes; empty means valid. Schema-version gating is a separate,
    earlier step (see :func:`schema_version_supported`, FR-EMT-3)."""
    if not isinstance(payload, dict):
        return [ERR_TYPE]
    errors: list[str] = []
    version = payload.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return [ERR_INVARIANT]
    expected_keys = _TOP_LEVEL_KEYS_V2 if version == 2 else _TOP_LEVEL_KEYS_V1
    keys = set(payload.keys())
    if not keys.issubset(expected_keys):
        errors.append(ERR_UNKNOWN_FIELD)
    if expected_keys - keys:
        errors.append(ERR_MISSING_FIELD)
        # Missing fields make most member checks meaningless; report and stop.
        return _dedupe(errors)

    for field, pattern in (
        ("emit_id", _UUID_RE),
        ("day", _DAY_RE),
        ("org_id", _ORG_RE),
        ("agent_version", _VERSION_RE),
        ("rate_card_version", _VERSION_RE),
    ):
        value = payload[field]
        if not isinstance(value, str) or not pattern.match(value):
            errors.append(ERR_FORMAT)
    if isinstance(payload["day"], str) and _DAY_RE.match(payload["day"]):
        try:
            date.fromisoformat(payload["day"])
        except ValueError:
            errors.append(ERR_FORMAT)

    if payload["platform"] not in PLATFORMS:
        errors.append(ERR_ENUM)

    # Validated invariants (FR-EMT-1).
    if payload["content_present"] is not False or payload["identity_present"] is not False:
        errors.append(ERR_INVARIANT)
    if payload["is_estimate"] is not True:
        errors.append(ERR_INVARIANT)
    if payload["contributors"] != 1:
        errors.append(ERR_INVARIANT)

    for field in _COUNTER_FIELDS:
        if not _is_count(payload[field]):
            errors.append(ERR_TYPE)

    _check_closed_int_map(payload["tokens"], TOKEN_KEYS, errors, exact=True)
    _check_closed_int_map(payload["agent_health"], AGENT_HEALTH_KEYS, errors, exact=True)
    _check_closed_int_map(payload["engagement"], ENGAGEMENT_KEYS, errors, exact=True)

    spend = payload["spend_by_family"]
    if isinstance(spend, dict):
        family_values = {family.value for family in ModelFamily}
        if not set(spend.keys()).issubset(family_values):
            errors.append(ERR_ENUM)
        if not all(_is_count(v) for v in spend.values()):
            errors.append(ERR_TYPE)
    else:
        errors.append(ERR_TYPE)

    tools = payload["tools_observed"]
    if isinstance(tools, list):
        if not all(isinstance(t, str) and t in TOOL_VALUES for t in tools):
            errors.append(ERR_ENUM)
        if len(tools) != len(set(map(str, tools))):
            errors.append(ERR_FORMAT)
    else:
        errors.append(ERR_TYPE)

    if version == 2:
        _check_closed_int_map(
            payload["work_type_sessions"], WORK_TYPE_KEYS, errors, exact=True
        )
        maturity = payload["maturity"]
        if isinstance(maturity, dict):
            if set(maturity.keys()) != set(MATURITY_SIGNAL_KEYS):
                errors.append(ERR_MISSING_FIELD)
            if not all(v in MATURITY_LEVEL_VALUES for v in maturity.values()):
                errors.append(ERR_ENUM)
        else:
            errors.append(ERR_TYPE)

    # Defense in depth: no string value anywhere may contain content patterns.
    for value in _iter_strings(payload):
        if leak_findings(value):
            errors.append(ERR_VALUE_LEAK)
            break

    return _dedupe(errors)


def schema_version_supported(payload: object) -> bool:
    """FR-EMT-3: version gate, checked BEFORE full validation."""
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("schema_version"), int)
        and payload["schema_version"] in SUPPORTED_SCHEMA_VERSIONS
    )


def _iter_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_iter_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_iter_strings(item))
    return found


def _dedupe(errors: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for code in errors:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered
