"""Validated, reproducible coding-agent benchmark guidance."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

SCHEMA = "practicegraph.model-intelligence/1"
RESULTS_URL = "https://artificialanalysis.ai/agents/coding-agents"
METHODOLOGY_URL = (
    "https://artificialanalysis.ai/methodology/coding-agents-benchmarking"
)
ATTRIBUTION = "Coding-agent benchmark data: Artificial Analysis"
BENCHMARK_NAME = "Artificial Analysis Coding Agent Index"
TOKEN_UNIT = "average_total_tokens_per_task"
SUPPORTED_TOOLS = frozenset({"codex", "claude_code"})
EFFORTS = frozenset(
    {"default", "low", "medium", "high", "xhigh", "max", "adaptive", "not_reported"}
)

_ROOT_KEYS = frozenset(
    {"schema", "artifact_version", "published_on", "source", "benchmark", "variants"}
)
_SOURCE_KEYS = frozenset({"name", "results_url", "methodology_url", "attribution"})
_BENCHMARK_KEYS = frozenset({"name", "version", "token_unit"})
_VARIANT_KEYS = frozenset(
    {"id", "tool", "model", "effort", "index_tenths", "total_tokens_per_task"}
)
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_VERSION = re.compile(r"[a-z0-9][a-z0-9.-]{0,79}\Z")
_BENCHMARK_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+()_-]{0,79}\Z")


@dataclass(frozen=True)
class ModelVariant:
    id: str
    tool: str
    model: str
    effort: str
    index_tenths: int
    total_tokens_per_task: int


@dataclass(frozen=True)
class ModelArtifact:
    artifact_version: str
    published_on: date
    benchmark_version: str
    variants: tuple[ModelVariant, ...]


@dataclass(frozen=True)
class ModelAlternative:
    kind: Literal["save_tokens", "maximum_capability"]
    variant_id: str
    model: str
    effort: str
    index: str
    tokens: str


@dataclass(frozen=True)
class ModelRecommendation:
    tool: str
    variant_id: str
    model: str
    effort: str
    index: str
    tokens: str
    explanation: str
    alternatives: tuple[ModelAlternative, ...]
    current_model: str
    current_model_matched: bool
    source: str
    source_url: str
    methodology_url: str
    benchmark_version: str
    published_on: str
    review_needed: bool
    # The pinned default the tool's own config starts a session on, judged
    # against this artifact: which of the three casts it matches
    # ("strongest" / "everyday" / "fast"), or None when it is not pinned or
    # not in the benchmark set. The artifact is the judge, never a
    # client-side name heuristic.
    default_model: str | None = None
    default_effort: str | None = None
    default_role: str | None = None
    default_matched: bool = False


def _closed_dict(value: object, keys: frozenset[str]) -> Mapping[str, object] | None:
    if not isinstance(value, dict) or set(value) != keys:
        return None
    return value


def _plain_string(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return None
    return value


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def parse_model_artifact(raw: object) -> ModelArtifact | None:
    """Parse a closed model-intelligence document, returning None on any invalid input."""
    root = _closed_dict(raw, _ROOT_KEYS)
    if root is None or root["schema"] != SCHEMA:
        return None
    artifact_version = _plain_string(root["artifact_version"], _VERSION)
    published_on = _parse_date(root["published_on"])
    source = _closed_dict(root["source"], _SOURCE_KEYS)
    benchmark = _closed_dict(root["benchmark"], _BENCHMARK_KEYS)
    rows = root["variants"]
    if artifact_version is None or published_on is None or source is None or benchmark is None:
        return None
    if source != {
        "name": "Artificial Analysis",
        "results_url": RESULTS_URL,
        "methodology_url": METHODOLOGY_URL,
        "attribution": ATTRIBUTION,
    }:
        return None
    benchmark_version = _plain_string(benchmark["version"], _BENCHMARK_VERSION)
    if (
        benchmark["name"] != BENCHMARK_NAME
        or benchmark["token_unit"] != TOKEN_UNIT
        or benchmark_version is None
        or not isinstance(rows, list)
        or not 2 <= len(rows) <= 24
    ):
        return None

    variants: list[ModelVariant] = []
    ids: set[str] = set()
    tools: set[str] = set()
    for raw_row in rows:
        row = _closed_dict(raw_row, _VARIANT_KEYS)
        if row is None:
            return None
        row_id = _plain_string(row["id"], _SLUG)
        model = _plain_string(row["model"], _MODEL)
        tool = row["tool"]
        effort = row["effort"]
        index_tenths = row["index_tenths"]
        tokens = row["total_tokens_per_task"]
        if (
            row_id is None
            or row_id in ids
            or tool not in SUPPORTED_TOOLS
            or model is None
            or effort not in EFFORTS
            or type(index_tenths) is not int
            or not 0 <= index_tenths <= 1000
            or type(tokens) is not int
            or not 1 <= tokens <= 100_000_000
        ):
            return None
        ids.add(row_id)
        tools.add(str(tool))
        variants.append(
            ModelVariant(row_id, str(tool), model, str(effort), index_tenths, tokens)
        )
    if tools != SUPPORTED_TOOLS:
        return None
    return ModelArtifact(artifact_version, published_on, benchmark_version, tuple(variants))


def model_artifact_to_dict(artifact: ModelArtifact) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "artifact_version": artifact.artifact_version,
        "published_on": artifact.published_on.isoformat(),
        "source": {
            "name": "Artificial Analysis",
            "results_url": RESULTS_URL,
            "methodology_url": METHODOLOGY_URL,
            "attribution": ATTRIBUTION,
        },
        "benchmark": {
            "name": BENCHMARK_NAME,
            "version": artifact.benchmark_version,
            "token_unit": TOKEN_UNIT,
        },
        "variants": [
            {
                "id": row.id,
                "tool": row.tool,
                "model": row.model,
                "effort": row.effort,
                "index_tenths": row.index_tenths,
                "total_tokens_per_task": row.total_tokens_per_task,
            }
            for row in artifact.variants
        ],
    }


def pareto_frontier(
    variants: Sequence[ModelVariant], tool: str
) -> tuple[ModelVariant, ...]:
    candidates = [row for row in variants if row.tool == tool]
    frontier = [
        row
        for row in candidates
        if not any(
            other.index_tenths >= row.index_tenths
            and other.total_tokens_per_task <= row.total_tokens_per_task
            and (
                other.index_tenths > row.index_tenths
                or other.total_tokens_per_task < row.total_tokens_per_task
            )
            for other in candidates
        )
    ]
    return tuple(
        sorted(
            frontier,
            key=lambda row: (row.total_tokens_per_task, -row.index_tenths, row.id),
        )
    )


def _balanced(frontier: Sequence[ModelVariant]) -> ModelVariant:
    min_index = min(row.index_tenths for row in frontier)
    max_index = max(row.index_tenths for row in frontier)
    min_tokens = min(row.total_tokens_per_task for row in frontier)
    max_tokens = max(row.total_tokens_per_task for row in frontier)
    index_range = max_index - min_index
    token_range = max_tokens - min_tokens

    def key(row: ModelVariant) -> tuple[float, int, int, str]:
        performance_gap = (max_index - row.index_tenths) / index_range if index_range else 0.0
        token_gap = (
            (row.total_tokens_per_task - min_tokens) / token_range if token_range else 0.0
        )
        return (
            performance_gap * performance_gap + token_gap * token_gap,
            -row.index_tenths,
            row.total_tokens_per_task,
            row.id,
        )

    return min(frontier, key=key)


def _format_index(value: int) -> str:
    return f"{value / 10:.1f}"


def _format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M tokens/task"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K tokens/task"
    return f"{value} tokens/task"


def _alternative(
    kind: Literal["save_tokens", "maximum_capability"], row: ModelVariant
) -> ModelAlternative:
    return ModelAlternative(
        kind, row.id, row.model, row.effort, _format_index(row.index_tenths),
        _format_tokens(row.total_tokens_per_task),
    )


def _normalized_model(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _matches(candidate: str, variant_model: str) -> bool:
    left = _normalized_model(candidate)
    right = _normalized_model(variant_model)
    return bool(left) and (left == right or left in right or right in left)


def build_model_recommendations(
    artifact: ModelArtifact,
    usage: Mapping[str, Mapping[str, int]],
    today: date,
    defaults: Mapping[str, tuple[str | None, str | None]] | None = None,
) -> tuple[ModelRecommendation, ...]:
    """`defaults` maps tool -> (pinned model, pinned effort) read from the
    tool's own config; the artifact judges which cast the pin matches."""
    recommendations: list[ModelRecommendation] = []
    for tool in sorted(SUPPORTED_TOOLS & usage.keys()):
        model_turns = {model: turns for model, turns in usage[tool].items() if turns > 0}
        if not model_turns:
            continue
        frontier = pareto_frontier(artifact.variants, tool)
        if not frontier:
            continue
        selected = _balanced(frontier)
        current_model = min(model_turns, key=lambda model: (-model_turns[model], model))
        current_normalized = _normalized_model(current_model)
        matched = any(
            _normalized_model(row.model) == current_normalized
            or _normalized_model(row.model) in current_normalized
            or current_normalized in _normalized_model(row.model)
            for row in artifact.variants
            if row.tool == tool
        )
        save_tokens = min(
            frontier,
            key=lambda row: (row.total_tokens_per_task, -row.index_tenths, row.id),
        )
        maximum_capability = min(
            frontier, key=lambda row: (-row.index_tenths, row.total_tokens_per_task, row.id)
        )
        alternatives: list[ModelAlternative] = []
        if save_tokens.id != selected.id:
            alternatives.append(_alternative("save_tokens", save_tokens))
        if maximum_capability.id not in {selected.id, save_tokens.id}:
            alternatives.append(_alternative("maximum_capability", maximum_capability))
        index = _format_index(selected.index_tenths)
        tokens = _format_tokens(selected.total_tokens_per_task)
        # The pinned default, judged by this artifact: which of the three
        # casts does the pin match, if any.
        default_model, default_effort = (defaults or {}).get(tool, (None, None))
        default_role: str | None = None
        default_matched = False
        if default_model is not None:
            casts = (
                ("strongest", maximum_capability.model),
                ("everyday", selected.model),
                ("fast", save_tokens.model),
            )
            for role_id, cast_model in casts:
                if _matches(default_model, cast_model):
                    default_role = role_id
                    default_matched = True
                    break
            if not default_matched:
                default_matched = any(
                    _matches(default_model, row.model)
                    for row in artifact.variants
                    if row.tool == tool
                )
        recommendations.append(
            ModelRecommendation(
                tool=tool,
                variant_id=selected.id,
                model=selected.model,
                effort=selected.effort,
                index=index,
                tokens=tokens,
                explanation=f"Balanced benchmark tradeoff: {index} index at {tokens}.",
                alternatives=tuple(alternatives),
                current_model=current_model,
                current_model_matched=matched,
                source="Artificial Analysis",
                source_url=RESULTS_URL,
                methodology_url=METHODOLOGY_URL,
                benchmark_version=artifact.benchmark_version,
                published_on=artifact.published_on.isoformat(),
                review_needed=(today - artifact.published_on).days > 45,
                default_model=default_model,
                default_effort=default_effort,
                default_role=default_role,
                default_matched=default_matched,
            )
        )
    return tuple(recommendations)
