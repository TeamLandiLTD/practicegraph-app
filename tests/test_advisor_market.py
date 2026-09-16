"""Advisor market artifact: strict closed validation of the curated market +
verdict document (docs/ADVISOR_PLAN.md Phase 2). Inbound copy is untrusted —
closed schema, integer bounds, copy caps, lexicon + leak scans, closed
families/tools, and expiry ordering all enforced by the parser."""

from __future__ import annotations

import copy
from datetime import date

from practicegraph.analysis.advisor_market import (
    LEGACY_SCHEMA as SCHEMA,
)
from practicegraph.analysis.advisor_market import (
    advisor_artifact_to_dict,
    market_stale,
    parse_advisor_artifact,
)


def _good() -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "artifact_version": "advisor-2026-07-19-abc123def0",
        "as_of": "2026-07-19",
        "expires": "2026-08-02",
        "source": {
            "name": "Artificial Analysis",
            "url": "https://artificialanalysis.ai/models",
            "attribution": "Model benchmark and price data: Artificial Analysis",
        },
        "models": [
            {
                "family": "claude_fable",
                "aa_slug": "claude-fable-5",
                "released_on": "2026-06-30",
                "coding_index_tenths": 900,
                "agentic_index_tenths": 880,
                "price_in_micro": 10_000_000,
                "price_out_micro": 50_000_000,
                "price_cache_read_micro": 1_000_000,
                "median_tps_tenths": 550,
                "ttft_ms": 1800,
                "context_window": 1_000_000,
            },
            {
                "family": "claude_haiku",
                "aa_slug": "claude-haiku-4.5",
                "released_on": "2025-10-01",
                "coding_index_tenths": 620,
                "agentic_index_tenths": 560,
                "price_in_micro": 1_000_000,
                "price_out_micro": 5_000_000,
                "price_cache_read_micro": 100_000,
                "median_tps_tenths": 1400,
                "ttft_ms": 700,
                "context_window": 200_000,
            },
        ],
        "verdicts": [
            {
                "id": "budget-first-ladder",
                "tier": "use",
                "families": ["claude_haiku"],
                "tools": ["claude_code"],
                "verdict": "Route mechanical work to the budget family.",
                "boundary": "Escalate when a diff would not land as-is.",
                "steelman": "The premium family keeps the best first-pass rate on feature work.",
                "action": "Set the budget family as the harness default for routine edits.",
                "experiment": "Route one week of rename work down and compare rework.",
                "as_of": "2026-07-19",
                "expires": "2026-08-16",
            }
        ],
    }


def _mutated(path: tuple[object, ...], value: object) -> dict[str, object]:
    document = copy.deepcopy(_good())
    target: object = document
    for step in path[:-1]:
        target = target[step]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    return document


def test_round_trips_through_the_serializer() -> None:
    artifact = parse_advisor_artifact(_good())
    assert artifact is not None
    assert parse_advisor_artifact(advisor_artifact_to_dict(artifact)) == artifact
    assert artifact.models[0].family == "claude_fable"
    assert artifact.verdicts[0].tier == "use"
    assert artifact.verdicts[0].families == ("claude_haiku",)


def test_optional_copy_fields_accept_empty() -> None:
    document = _mutated(("verdicts", 0, "steelman"), "")
    document["verdicts"][0]["experiment"] = ""  # type: ignore[index]
    artifact = parse_advisor_artifact(document)
    assert artifact is not None
    assert artifact.verdicts[0].steelman == ""
    assert artifact.verdicts[0].experiment == ""


def test_structural_deviations_are_rejected() -> None:
    extra = _good()
    extra["surprise"] = 1
    assert parse_advisor_artifact(extra) is None
    assert parse_advisor_artifact(_mutated(("schema",), "practicegraph.advisor/9")) is None
    assert parse_advisor_artifact(_mutated(("source", "name"), "Someone Else")) is None
    assert parse_advisor_artifact(_mutated(("models",), [])) is None
    assert parse_advisor_artifact(_mutated(("as_of",), "2026-7-1")) is None
    assert parse_advisor_artifact(_mutated(("expires",), "2026-07-01")) is None  # < as_of


def test_model_rows_enforce_closed_families_and_integer_bounds() -> None:
    good = _good()
    dupe = copy.deepcopy(good)
    dupe["models"][1]["family"] = "claude_fable"  # type: ignore[index]
    assert parse_advisor_artifact(dupe) is None
    assert parse_advisor_artifact(_mutated(("models", 0, "family"), "other")) is None
    assert parse_advisor_artifact(_mutated(("models", 0, "family"), "gpt_6")) is None
    assert parse_advisor_artifact(_mutated(("models", 0, "price_in_micro"), True)) is None
    assert parse_advisor_artifact(
        _mutated(("models", 0, "coding_index_tenths"), 1001)
    ) is None
    assert parse_advisor_artifact(
        _mutated(("models", 0, "aa_slug"), "Not A Slug")
    ) is None


def test_verdicts_enforce_tiers_joins_copy_and_dates() -> None:
    assert parse_advisor_artifact(_mutated(("verdicts", 0, "tier"), "ship-it")) is None
    # A verdict may only reference families the artifact prices.
    assert parse_advisor_artifact(
        _mutated(("verdicts", 0, "families"), ["claude_sonnet"])
    ) is None
    assert parse_advisor_artifact(_mutated(("verdicts", 0, "tools"), [])) is None
    assert parse_advisor_artifact(_mutated(("verdicts", 0, "verdict"), "")) is None
    assert parse_advisor_artifact(
        _mutated(("verdicts", 0, "verdict"), "x" * 161)
    ) is None
    # Lexicon scan: FORBIDDEN_LEXICON terms cannot ride in on curated copy.
    assert parse_advisor_artifact(
        _mutated(("verdicts", 0, "verdict"), "This model is addictive.")
    ) is None
    # Leak scan: URLs never appear inside copy (attribution is structural).
    assert parse_advisor_artifact(
        _mutated(("verdicts", 0, "boundary"), "See https://example.org for more.")
    ) is None
    assert parse_advisor_artifact(
        _mutated(("verdicts", 0, "expires"), "2026-07-01")  # < verdict as_of
    ) is None
    two = copy.deepcopy(_good())
    two["verdicts"].append(copy.deepcopy(two["verdicts"][0]))  # type: ignore[attr-defined]
    assert parse_advisor_artifact(two) is None  # duplicate id


def test_market_stale_is_a_pure_date_gate() -> None:
    artifact = parse_advisor_artifact(_good())
    assert artifact is not None
    assert not market_stale(artifact, date(2026, 8, 2))
    assert market_stale(artifact, date(2026, 8, 3))
