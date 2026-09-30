"""Field-guide practices: a catalog artifact under the same copy discipline
as the product's own catalogs (FR-FOC-8 lexicon, no leaks, closed schema)."""

from __future__ import annotations

from practicegraph.analysis.practices import (
    BUNDLED_PRACTICES,
    MAX_PRACTICES,
    parse_practices_artifact,
    practices_artifact,
    relevant_practices,
)
from practicegraph.privacy import leak_findings, lexicon_violations


def test_bundled_practices_pass_their_own_validator() -> None:
    parsed = parse_practices_artifact(practices_artifact())
    assert parsed is not None
    assert len(parsed) == len(BUNDLED_PRACTICES)
    for practice in BUNDLED_PRACTICES:
        assert lexicon_violations(practice.title + practice.body) == []
        assert leak_findings(practice.title + practice.body) == []


def test_artifact_validation_rejects_bad_copy() -> None:
    def entry(**overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "id": "sample-practice",
            "tool": "any",
            "title": "A fine title",
            "body": "A fine body.",
        }
        base.update(overrides)
        return base

    def artifact(entries: list[dict[str, object]]) -> dict[str, object]:
        return {"practices_version": "practices-x", "entries": entries}

    assert parse_practices_artifact(artifact([entry()])) is not None
    # A server cannot push copy the product itself may not write (FR-FOC-8).
    assert parse_practices_artifact(
        artifact([entry(body="Boost your dopamine baseline!")])
    ) is None
    assert parse_practices_artifact(
        artifact([entry(body="see https://evil.example/track")])
    ) is None
    assert parse_practices_artifact(artifact([entry(tool="excel")])) is None
    assert parse_practices_artifact(artifact([entry(finding="made_up")])) is None
    assert parse_practices_artifact(artifact([entry(surprise=1)])) is None
    assert parse_practices_artifact(artifact([entry(), entry()])) is None  # dup ids
    assert parse_practices_artifact(
        artifact([entry(id=f"p-{i}") for i in range(MAX_PRACTICES + 1)])
    ) is None
    assert parse_practices_artifact({"entries": []}) is None


def test_relevant_practices_filter_and_highlight() -> None:
    ranked = relevant_practices(
        BUNDLED_PRACTICES,
        tools_observed={"claude_code"},
        triggered_findings={"low_cache_reuse"},
    )
    assert ranked, "claude_code + any practices apply"
    ids = [practice.practice_id for practice, _relevant in ranked]
    assert "pin-reasoning-effort" not in ids  # codex-only practice filtered out
    top_practice, top_relevant = ranked[0]
    assert top_practice.practice_id == "keep-sessions-warm"  # matched finding first
    assert top_relevant is True
    assert len(ranked) <= 6
