"""The documentation shelf artifact: closed schema, https-only, bundled default."""

import copy
import json

from practicegraph.analysis.docs import EMPTY_DOCS
from practicegraph.catalog import load_docs
from practicegraph.privacy import leak_findings, lexicon_violations
from synthetic_docs_fixture import (
    SAMPLE_DOCS,
    SAMPLE_DOCS_DOCUMENT,
    parse_docs_artifact,
)


def test_bundled_shelf_parses_and_is_organized_into_sections() -> None:
    parsed = parse_docs_artifact(copy.deepcopy(SAMPLE_DOCS_DOCUMENT))
    assert parsed is not None
    assert parsed == SAMPLE_DOCS
    assert len(parsed.sections) >= 3
    for section in parsed.sections:
        assert section.links, section.title
        for link in section.links:
            assert link.url.startswith("https://")


def test_bundled_copy_passes_the_lexicon_and_leak_gates() -> None:
    for section in SAMPLE_DOCS.sections:
        assert lexicon_violations(section.title) == []
        for link in section.links:
            for text in (link.title, link.why):
                assert lexicon_violations(text) == [], text
                assert leak_findings(text) == [], text


def test_non_https_link_refuses_the_whole_document() -> None:
    document = copy.deepcopy(SAMPLE_DOCS_DOCUMENT)
    document["sections"][0]["links"][0]["url"] = "http://example.com/docs"
    assert parse_docs_artifact(document) is None


def test_unknown_keys_and_free_text_are_refused() -> None:
    extra_key = copy.deepcopy(SAMPLE_DOCS_DOCUMENT)
    extra_key["injected"] = "x"
    assert parse_docs_artifact(extra_key) is None

    control_chars = copy.deepcopy(SAMPLE_DOCS_DOCUMENT)
    control_chars["sections"][0]["title"] = "Claude\nCode"
    assert parse_docs_artifact(control_chars) is None

    assert parse_docs_artifact("not a dict") is None
    assert parse_docs_artifact({}) is None


def test_productivity_shelf_derives_without_developer_links(tmp_path) -> None:
    """PRODUCTIVITY_PROFILE P1: the second lane drops the developer-facing
    links (changelog, source repo) and keeps the rest; it is derived from
    the coding shelf so the lanes cannot drift."""
    from practicegraph.catalog import load_docs as load
    from synthetic_docs_fixture import SAMPLE_DOCS_PRODUCTIVITY

    titles = {link.title for section in SAMPLE_DOCS_PRODUCTIVITY.sections for link in section.links}
    assert "Changelog" not in titles
    assert "CLI repository" not in titles
    assert "Example reference 1" in titles
    assert load(tmp_path, "productivity") == EMPTY_DOCS
    assert load(tmp_path, "coding") == EMPTY_DOCS


def test_load_docs_is_empty_until_a_downloadd_shelf(tmp_path) -> None:
    # Absent cache -> explicit empty state.
    assert load_docs(tmp_path) == EMPTY_DOCS
    # Invalid cache -> explicit empty state.
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "docs.json").write_text("{not json", encoding="utf-8")
    assert load_docs(tmp_path) == EMPTY_DOCS
    # A valid cached shelf wins.
    served = copy.deepcopy(SAMPLE_DOCS_DOCUMENT)
    served["docs_version"] = "docs-served-2026-09-01"
    (catalog / "docs.json").write_text(json.dumps(served), encoding="utf-8")
    assert load_docs(tmp_path).docs_version == "docs-served-2026-09-01"
