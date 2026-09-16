"""The model catalog: available models + effort ladders, served closed."""

import copy
import json

from practicegraph.analysis.model_catalog import EMPTY_CATALOG
from practicegraph.catalog import load_model_catalog
from practicegraph.privacy import leak_findings, lexicon_violations
from synthetic_model_catalog_fixture import (
    SAMPLE_CATALOG,
    SAMPLE_CATALOG_DOCUMENT,
    parse_model_catalog,
)


def test_bundled_catalog_parses_and_its_copy_passes_the_gates() -> None:
    parsed = parse_model_catalog(copy.deepcopy(SAMPLE_CATALOG_DOCUMENT))
    assert parsed is not None
    assert [tool.tool for tool in parsed.tools] == ["claude_code", "codex"]
    for tool in parsed.tools:
        roles = [row.role for row in tool.models]
        assert len(roles) == len(set(roles))
        if tool.efforts:
            assert sum(1 for row in tool.efforts if row.recommended) == 1
        for row in tool.models:
            assert lexicon_violations(f"{row.when} {row.price_note}") == []
            assert leak_findings(f"{row.when} {row.price_note}") == []
        for row in tool.efforts:
            assert lexicon_violations(row.when) == []
            assert leak_findings(row.when) == []
        for practice in tool.practices:
            assert lexicon_violations(f"{practice.title} {practice.body}") == []
            assert leak_findings(f"{practice.title} {practice.body}") == []
        # Switch steps intentionally name the config FILES the reader must
        # edit (settings.json, config.toml) — instructional locations, the
        # same class as the docs shelf, so only the lexicon gate applies.
        for step in tool.switch:
            assert lexicon_violations(step) == []
    assert parsed.closing
    assert lexicon_violations(parsed.closing) == []


def test_productivity_lane_derives_and_leads_with_the_scope_caveat() -> None:
    """PRODUCTIVITY_PROFILE P1: the second lane is DERIVED from the coding
    document (structure can never drift), speaks in the coach report's own
    audience terms, and its facts fold leads with the benchmark-scope
    caveat because these are coding benchmarks read by a non-coding
    audience."""
    from synthetic_model_catalog_fixture import (
        SAMPLE_CATALOG_PRODUCTIVITY,
    )

    catalog = SAMPLE_CATALOG_PRODUCTIVITY
    assert catalog.catalog_version.startswith("catalog-productivity")
    by_tool = {tool.tool: tool for tool in catalog.tools}
    assert "documents" in by_tool["codex"].models[1].when.lower()
    codex_first_fact = by_tool["codex"].practices[0].title
    assert "Scope the benchmarks" in codex_first_fact
    # Same ladder underneath: models, efforts and switch steps unchanged.
    coding = {tool.tool: tool for tool in SAMPLE_CATALOG.tools}
    for tool in ("claude_code", "codex"):
        assert [m.model for m in by_tool[tool].models] == [m.model for m in coding[tool].models]
        assert by_tool[tool].efforts == coding[tool].efforts
        assert by_tool[tool].switch == coding[tool].switch
    for tool in catalog.tools:
        for row in tool.models:
            assert lexicon_violations(row.when) == []
            assert leak_findings(row.when) == []


def test_catalog_parser_refuses_any_deviation() -> None:
    base = copy.deepcopy(SAMPLE_CATALOG_DOCUMENT)

    two_recommended = copy.deepcopy(base)
    two_recommended["tools"][1]["efforts"][0]["recommended"] = True
    assert parse_model_catalog(two_recommended) is None

    none_recommended = copy.deepcopy(base)
    for effort in none_recommended["tools"][1]["efforts"]:
        effort["recommended"] = False
    assert parse_model_catalog(none_recommended) is None

    duplicate_role = copy.deepcopy(base)
    duplicate_role["tools"][0]["models"][1]["role"] = "strongest"
    assert parse_model_catalog(duplicate_role) is None

    bad_tone = copy.deepcopy(base)
    bad_tone["tools"][1]["efforts"][0]["tone"] = "scary"
    assert parse_model_catalog(bad_tone) is None

    wrong_order = copy.deepcopy(base)
    wrong_order["tools"].reverse()
    assert parse_model_catalog(wrong_order) is None

    extra_key = copy.deepcopy(base)
    extra_key["tools"][0]["models"][0]["price_usd"] = 15
    assert parse_model_catalog(extra_key) is None

    assert parse_model_catalog("not a dict") is None
    assert parse_model_catalog({}) is None


def test_load_model_catalog_serves_each_profiles_lane(tmp_path) -> None:
    """D3, suffix files: the productivity lane reads its own cached file
    (model-catalog-productivity.json) and stays empty until its own download —
    the coding lane's cache never bleeds into it."""

    assert load_model_catalog(tmp_path, "productivity") == EMPTY_CATALOG
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    served = copy.deepcopy(SAMPLE_CATALOG_DOCUMENT)
    served["catalog_version"] = "catalog-served-coding"
    (catalog / "model-catalog.json").write_text(
        json.dumps(served),
        encoding="utf-8",
    )
    # A coding cache present, no productivity cache: productivity still
    # stays empty.
    assert load_model_catalog(tmp_path, "productivity") == EMPTY_CATALOG
    served["catalog_version"] = "catalog-served-productivity"
    (catalog / "model-catalog-productivity.json").write_text(
        json.dumps(served),
        encoding="utf-8",
    )
    assert (
        load_model_catalog(tmp_path, "productivity").catalog_version
        == "catalog-served-productivity"
    )


def test_load_model_catalog_is_empty_until_a_download(tmp_path) -> None:
    assert load_model_catalog(tmp_path) == EMPTY_CATALOG

    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "model-catalog.json").write_text("{not json", encoding="utf-8")
    assert load_model_catalog(tmp_path) == EMPTY_CATALOG

    served = copy.deepcopy(SAMPLE_CATALOG_DOCUMENT)
    served["catalog_version"] = "catalog-served-2026-09-01"
    (catalog / "model-catalog.json").write_text(
        json.dumps(served),
        encoding="utf-8",
    )
    assert load_model_catalog(tmp_path).catalog_version == "catalog-served-2026-09-01"
