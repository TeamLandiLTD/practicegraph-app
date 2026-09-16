from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

from practicegraph.analysis.model_intelligence import (
    ModelVariant,
    build_model_recommendations,
    model_artifact_to_dict,
    pareto_frontier,
    parse_model_artifact,
)

TODAY = date(2026, 7, 16)


def valid_document() -> dict[str, object]:
    return {
        "schema": "practicegraph.model-intelligence/1",
        "artifact_version": "models-aa-coding-v1.1-2026-07-16",
        "published_on": "2026-07-16",
        "source": {
            "name": "Artificial Analysis",
            "results_url": "https://artificialanalysis.ai/agents/coding-agents",
            "methodology_url": (
                "https://artificialanalysis.ai/methodology/coding-agents-benchmarking"
            ),
            "attribution": "Coding-agent benchmark data: Artificial Analysis",
        },
        "benchmark": {
            "name": "Artificial Analysis Coding Agent Index",
            "version": "1.1",
            "token_unit": "average_total_tokens_per_task",
        },
        "variants": [
            {
                "id": "codex-efficient",
                "tool": "codex",
                "model": "GPT Efficient",
                "effort": "medium",
                "index_tenths": 700,
                "total_tokens_per_task": 2_000_000,
            },
            {
                "id": "codex-balanced",
                "tool": "codex",
                "model": "GPT Balanced",
                "effort": "high",
                "index_tenths": 800,
                "total_tokens_per_task": 3_000_000,
            },
            {
                "id": "codex-capable",
                "tool": "codex",
                "model": "GPT Capable",
                "effort": "xhigh",
                "index_tenths": 900,
                "total_tokens_per_task": 8_000_000,
            },
            {
                "id": "claude-balanced",
                "tool": "claude_code",
                "model": "Claude Balanced",
                "effort": "adaptive",
                "index_tenths": 820,
                "total_tokens_per_task": 4_000_000,
            },
        ],
    }


def test_parse_model_artifact_accepts_and_round_trips_closed_document() -> None:
    document = valid_document()
    artifact = parse_model_artifact(document)

    assert artifact is not None
    assert model_artifact_to_dict(artifact) == document


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema",), "other/1"),
        (("artifact_version",), "has spaces"),
        (("published_on",), "16-07-2026"),
        (("source", "name"), "Someone Else"),
        (("source", "results_url"), "http://artificialanalysis.ai/agents/coding-agents"),
        (("source", "methodology_url"), "https://example.test/methodology"),
        (("source", "attribution"), "Benchmark data"),
        (("benchmark", "name"), "Opaque score"),
        (("benchmark", "version"), "../secret"),
        (("benchmark", "token_unit"), "tokens_per_turn"),
        (("variants", 0, "id"), "Bad ID"),
        (("variants", 0, "tool"), "cursor"),
        (("variants", 0, "model"), "<script>alert(1)</script>"),
        (("variants", 0, "effort"), "extreme"),
        (("variants", 0, "index_tenths"), True),
        (("variants", 0, "index_tenths"), 1001),
        (("variants", 0, "total_tokens_per_task"), 0),
        (("variants", 0, "total_tokens_per_task"), 100_000_001),
    ],
)
def test_parse_model_artifact_rejects_invalid_values(
    path: tuple[str | int, ...], value: object
) -> None:
    document = deepcopy(valid_document())
    target: object = document
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    assert parse_model_artifact(document) is None


@pytest.mark.parametrize("container", ["root", "source", "benchmark", "variant"])
def test_parse_model_artifact_rejects_extra_fields(container: str) -> None:
    document = deepcopy(valid_document())
    target = {
        "root": document,
        "source": document["source"],
        "benchmark": document["benchmark"],
        "variant": document["variants"][0],  # type: ignore[index]
    }[container]
    target["claim"] = "switch now"  # type: ignore[index]

    assert parse_model_artifact(document) is None


def test_parse_model_artifact_rejects_duplicate_ids_and_missing_tool() -> None:
    duplicate = deepcopy(valid_document())
    duplicate["variants"][1]["id"] = "codex-efficient"  # type: ignore[index]
    assert parse_model_artifact(duplicate) is None

    missing_tool = deepcopy(valid_document())
    missing_tool["variants"] = missing_tool["variants"][:2]  # type: ignore[index]
    assert parse_model_artifact(missing_tool) is None


def test_parse_model_artifact_enforces_row_count() -> None:
    too_few = deepcopy(valid_document())
    too_few["variants"] = too_few["variants"][:1]  # type: ignore[index]
    assert parse_model_artifact(too_few) is None

    too_many = deepcopy(valid_document())
    base = too_many["variants"][0]  # type: ignore[index]
    rows = []
    for index in range(25):
        row = deepcopy(base)
        row["id"] = f"codex-row-{index}"
        rows.append(row)
    rows[-1]["tool"] = "claude_code"
    too_many["variants"] = rows
    assert parse_model_artifact(too_many) is None


def test_pareto_frontier_excludes_dominated_variants() -> None:
    variants = (
        ModelVariant("a", "codex", "A", "medium", 700, 2_000_000),
        ModelVariant("dominated", "codex", "D", "high", 690, 3_000_000),
        ModelVariant("b", "codex", "B", "high", 800, 3_000_000),
        ModelVariant("other", "claude_code", "Other", "high", 900, 1_000_000),
    )

    assert [row.id for row in pareto_frontier(variants, "codex")] == ["a", "b"]


def test_balanced_knee_and_alternatives_are_deterministic() -> None:
    artifact = parse_model_artifact(valid_document())
    assert artifact is not None

    result = build_model_recommendations(
        artifact,
        {"codex": {"gpt-current": 9}, "claude_code": {"claude-current": 2}},
        TODAY,
    )

    assert [item.variant_id for item in result] == ["claude-balanced", "codex-balanced"]
    codex = result[1]
    assert [(item.kind, item.variant_id) for item in codex.alternatives] == [
        ("save_tokens", "codex-efficient"),
        ("maximum_capability", "codex-capable"),
    ]
    assert codex.index == "80.0"
    assert codex.tokens == "3.0M tokens/task"
    assert codex.current_model == "gpt-current"
    assert codex.current_model_matched is False
    assert "benchmark" in codex.explanation.lower()
    assert "local savings" not in codex.explanation.lower()


def test_recommendations_filter_tools_and_choose_dominant_local_model() -> None:
    artifact = parse_model_artifact(valid_document())
    assert artifact is not None

    result = build_model_recommendations(
        artifact,
        {"codex": {"gpt-balanced": 4, "gpt-old": 1}, "cursor": {"other": 20}},
        TODAY,
    )

    assert len(result) == 1
    assert result[0].tool == "codex"
    assert result[0].current_model == "gpt-balanced"
    assert result[0].current_model_matched is True


def test_stale_artifact_is_review_needed_after_45_days() -> None:
    document = valid_document()
    document["published_on"] = "2026-05-01"
    artifact = parse_model_artifact(document)
    assert artifact is not None

    result = build_model_recommendations(artifact, {"codex": {"gpt": 1}}, TODAY)

    assert result[0].review_needed is True


def test_zero_range_and_ties_resolve_by_capability_tokens_then_id() -> None:
    variants = (
        ModelVariant("z", "codex", "Z", "high", 800, 2_000_000),
        ModelVariant("a", "codex", "A", "high", 800, 2_000_000),
    )

    frontier = pareto_frontier(variants, "codex")
    assert [row.id for row in frontier] == ["a", "z"]

