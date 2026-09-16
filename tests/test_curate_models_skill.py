from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".agents" / "skills" / "curate-practicegraph-models"

pytestmark = pytest.mark.skipif(not SKILL.exists(), reason="private maintainer skill not installed")


def test_curator_skill_enforces_human_review_and_explicit_approval() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert "name: curate-practicegraph-models" in text
    assert "description: Use when" in text
    assert "manual" in text.lower()
    assert "explicit approval" in text.lower()
    assert "python tools/curate_models.py preview" in text
    assert "python -m tools.catalog_seal --channel" in text
    assert "normalize into a private staging directory" in text
    assert "Do not scrape" in text
    assert "Do not commit, push, or deploy" in text
    assert "references/artifact-format.md" in text


def test_curator_skill_has_discoverable_ui_metadata_and_exact_reference() -> None:
    metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
    reference = (SKILL / "references" / "artifact-format.md").read_text(
        encoding="utf-8"
    )

    assert "$curate-practicegraph-models" in metadata
    assert "practicegraph.model-intelligence/1" in reference
    assert "https://artificialanalysis.ai/agents/coding-agents" in reference
    assert "average_total_tokens_per_task" in reference
    assert "codex" in reference and "claude_code" in reference

