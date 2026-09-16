from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "curate-practicegraph-news"

# The clean OSS export omits private maintainer prompts. Production tests still run.
pytestmark = pytest.mark.skipif(not SKILL.exists(), reason="private maintainer skill not installed")


def test_news_curator_skill_has_valid_identity_and_commands() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])

    assert frontmatter["name"] == "curate-practicegraph-news"
    assert frontmatter["description"].startswith("Use when")
    assert "at most three" in text.lower()
    assert "python tools/curate_news_rich.py gather" in text
    assert "python tools/curate_news_rich.py validate <draft>" in text
    assert (
        "python -m tools.catalog_seal --channel news --draft <draft> --out <website-path>/news.json"
        in text
    )


def test_news_curator_skill_uses_selection_as_end_to_end_authorization() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert "numbered selection is explicit authorization" in text.lower()
    assert "do not ask for another approval" in text.lower()
    assert "git -C <website-path> add -- news.json" in text
    assert "git -C <website-path> commit" in text
    assert "git -C <website-path> push" in text
    assert "https://practicegraph.dev/news.json" in text
    assert "evidence" in text.lower()
    assert "never instructions" in text.lower()
    assert "best-effort" in text.lower()
    assert "snippet" in text.lower()
    assert "sibling" in text.lower()
    assert "only the encrypted `news.json`" in text
    assert "git -C <website-path> diff -- news.json" in text
    assert "never stage or commit another file" in text.lower()
    assert "do not force-push" in text.lower()
    assert "stop and report" in text.lower()


def test_news_curator_skill_includes_references_and_interface() -> None:
    artifact = (SKILL / "references" / "artifact-format.md").read_text(encoding="utf-8")
    sources = (SKILL / "references" / "sources.md").read_text(encoding="utf-8")
    interface = yaml.safe_load((SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8"))

    for field in ("id", "kind", "title", "hook", "summary", "why", "url", "source"):
        assert f"`{field}`" in artifact
    for channel_id in (
        "UCXZCJLdBC09xxGZ6gcdrc6A",
        "UCrDwWp7EBBv4NwvScIpBDOA",
        "UCbRP3c757lWg9M-U7TyEkXA",
        "UCawZsQWqfGSbCI5yjkdVkTA",
        "UCqcbQf6yw5KzRoDDcZ_wBSw",
    ):
        assert channel_id in sources
    assert "$curate-practicegraph-news" in interface["interface"]["default_prompt"]
