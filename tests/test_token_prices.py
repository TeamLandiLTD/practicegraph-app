from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tools.token_prices import build

from practicegraph import catalog
from practicegraph.analysis.token_prices import parse_token_prices, reading, snapshot
from practicegraph.config import resolve
from practicegraph.store import Store

NOW = datetime(2026, 9, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def research():
    return json.loads((Path(__file__).parent / "fixtures/token-prices-research.json").read_text())


def advance(research, version="prices-2026-09-12"):
    row = copy.deepcopy(research)
    row["edition_version"] = version
    row["observed_at"] = "2026-09-12T00:00:00Z"
    for offer in row["offers"]:
        offer["observed_at"] = row["observed_at"]
    return row


def test_research_needs_production_conversion_and_first_observation(research):
    assert parse_token_prices(research) is None
    doc = build(research)
    assert parse_token_prices(doc) == doc
    assert doc["history"] == []
    assert reading(doc, NOW)["changes"] == []
    assert build(research, doc) == doc


def with_benchmarks(research):
    doc = advance(research)
    doc["schema"] = "practicegraph.token-prices.research/2"
    source = {
        **doc["sources"][0],
        "id": "aa-test",
        "url": "https://artificialanalysis.ai/models/example",
        "kind": "independent",
    }
    doc["sources"].append(source)
    doc["benchmarks"] = [
        {
            "id": "test-score",
            "model_id": doc["models"][0]["id"],
            "task": "general",
            "version": "4.3",
            "score": "42.5",
            "reasoning_effort": "high",
            "harness": None,
            "observed_at": doc["observed_at"],
            "source_ids": [source["id"]],
            "notes": "Synthetic test score.",
        }
    ]
    doc["offer_evidence"] = [
        {
            "offer_id": doc["offers"][0]["id"],
            "reasoning": True,
            "modes": ["high"],
            "benchmark_ids": ["test-score"],
            "match": "documented",
            "observed_at": doc["observed_at"],
            "source_ids": doc["offers"][0]["source_ids"],
            "notes": "Synthetic provider compatibility evidence.",
        }
    ]
    return doc


def test_v2_preserves_v1_history_and_roundtrips(research):
    old = build(research)
    new = build(with_benchmarks(research), old)
    assert new["schema"] == "practicegraph.token-prices/2"
    assert new["history"][0]["schema"] == "practicegraph.token-prices.research/1"
    assert parse_token_prices(new) == new


@pytest.mark.parametrize(
    "field,value",
    [
        ("score", "101"),
        ("score", None),
        ("task", "coding"),
        ("source_ids", []),
        ("model_id", "nonexistent"),
        ("observed_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_rejects_invalid_benchmark_evidence(research, field, value):
    doc = build(with_benchmarks(research))
    doc["benchmarks"][0][field] = value
    assert parse_token_prices(doc) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("modes", ["low"]),
        ("reasoning", None),
        ("benchmark_ids", ["missing"]),
        ("match", "assumed"),
        ("source_ids", ["aa-test"]),
        ("offer_id", "missing"),
    ],
)
def test_rejects_invalid_provider_benchmark_join(research, field, value):
    doc = build(with_benchmarks(research))
    doc["offer_evidence"][0][field] = value
    assert parse_token_prices(doc) is None


def test_benchmark_source_must_be_artificial_analysis(research):
    doc = build(with_benchmarks(research))
    doc["sources"][-1]["url"] = "https://artificialanalysis.ai.example.com/models/test"
    assert parse_token_prices(doc) is None


@pytest.mark.parametrize("bad", [True, 0, -1, "-0.1", "1e3", "NaN", [], {}])
def test_invalid_prices_fail_closed(research, bad):
    doc = build(research)
    doc["offers"][0]["pricing"]["input"] = bad
    assert parse_token_prices(doc) is None


def test_unknown_and_free_are_distinct(research):
    doc = build(research)
    doc["offers"][0]["pricing"].update(input=None, cache_read="0")
    assert parse_token_prices(doc) == doc  # Previously accepted catalogs remain readable.
    assert doc["offers"][0]["pricing"]["input"] is None
    assert doc["offers"][0]["pricing"]["cache_read"] == "0"
    research["offers"][0]["pricing"].update(input=None)
    with pytest.raises(ValueError, match="sourced input and output"):
        build(research)
    research["offers"][0]["pricing"].update(input="0", output="0")
    assert build(research)["offers"][0]["pricing"]["input"] == "0"


def test_current_catalog_rejects_models_without_priced_offers(research):
    research["models"].append({**research["models"][0], "id": "unpriced"})
    with pytest.raises(ValueError, match="at least one priced offer"):
        build(research)


def test_price_freshness_does_not_redate_identity_evidence(research):
    doc = build(research)
    o = doc["offers"][0]
    doc["observed_at"] = o["observed_at"] = "2026-09-12T00:00:00Z"
    doc["sources"][0]["checked_at"] = "2026-07-01T00:00:00Z"
    doc["sources"].append({
        **doc["sources"][0], "id": "price-new", "checked_at": "2026-09-12T00:00:00Z",
    })
    o["pricing"]["source_ids"] = ["price-new"]
    assert reading(doc, NOW)["offer_states"][o["id"]] == "current"
    assert doc["sources"][0]["checked_at"] == "2026-07-01T00:00:00Z"


def test_dated_changes_and_changed_conditions(research):
    prior = build(research)
    new = advance(research)
    new["offers"][0]["pricing"].update(input="0.1", conditions="A different minimum applies.")
    doc = build(new, prior)
    result = reading(doc, NOW)
    assert result["changes"][0]["rates"]["input"]["after"] == "0.1"
    assert result["changes"][0]["conditions_changed"]
    assert doc["history"] == [snapshot(prior)]


def test_feed_size_trims_oldest_history_without_rewriting_retained_editions(research, monkeypatch):
    import tools.token_prices as builder

    from practicegraph.content import canonical_bytes

    first = build(research)
    second = build(advance(research), first)
    third = advance(research, "third")
    third["observed_at"] = "2026-09-13T00:00:00Z"
    full = build(third, second)
    bounded = {**full, "history": [snapshot(second)]}
    monkeypatch.setattr(builder, "MAX_CONTENT_BYTES", len(canonical_bytes(bounded)))
    result = build(third, second)
    assert result["history"] == [snapshot(second)]
    assert second["history"] == [snapshot(first)]
    assert parse_token_prices(result) == result
    monkeypatch.setattr(builder, "MAX_CONTENT_BYTES", 1)
    with pytest.raises(ValueError, match="exceeds 4 MiB"):
        build(third, second)


def test_reject_identity_reuse_and_rewritten_history(research):
    prior = build(research)
    new = advance(research)
    new["offers"][0]["precision"] = "changed"
    with pytest.raises(ValueError):
        build(new, prior)
    doc = build(advance(research), prior)
    doc["history"][0]["edition_version"] = doc["edition_version"]
    assert parse_token_prices(doc) is None
    new = copy.deepcopy(research)
    new["offers"][0]["pricing"]["input"] = "100"
    with pytest.raises(ValueError):
        build(new, prior)


def test_future_expired_and_stale_quotes(research):
    doc = build(research)
    o = doc["offers"][0]
    o["effective_from"] = "2026-10-01T00:00:00Z"
    assert reading(doc, NOW)["offer_states"][o["id"]] == "announced"
    o["effective_from"] = None
    o["effective_to"] = "2026-09-11T00:00:00Z"
    assert reading(doc, NOW)["offer_states"][o["id"]] == "expired"
    o["effective_to"] = None
    assert reading(doc, NOW + timedelta(days=5))["offer_states"][o["id"]] == "stale"


def test_malformed_source_and_extra_keys_rejected(research):
    doc = build(research)
    doc["sources"][0]["url"] = "javascript:alert(1)"
    assert parse_token_prices(doc) is None
    doc = build(research)
    doc["run_command"] = "no"
    assert parse_token_prices(doc) is None


def test_pull_keeps_last_valid_edition_and_throttles(research, tmp_path, monkeypatch):
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    doc = build(research)
    monkeypatch.setattr(catalog, "_get_catalog_json", lambda *_: doc)
    assert catalog.pull_public_token_prices(store, config, NOW) == "pulled"
    assert catalog.load_token_prices(tmp_path) == doc


def test_manual_retry_fetches_during_cooldown_and_keeps_validation(research, tmp_path, monkeypatch):
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    doc = build(research)
    calls = []
    def fetch(*args):
        calls.append(args)
        return doc
    monkeypatch.setattr(catalog, "_get_catalog_json", fetch)
    assert catalog.pull_public_token_prices(store, config, NOW) == "pulled"
    assert catalog.pull_public_token_prices(store, config, NOW, manual=True) == "pulled"
    assert len(calls) == 2
    assert catalog.pull_public_token_prices(store, config, NOW, manual=True) == "skipped_recently"
    assert catalog.load_token_prices(tmp_path)["observed_at"] == doc["observed_at"]
    monkeypatch.setattr(catalog, "_get_catalog_json", lambda *_: "invalid_signature")
    assert catalog.pull_public_token_prices(
        store, config, NOW + timedelta(seconds=31), manual=True,
    ) == "invalid_signature"
    assert catalog.load_token_prices(tmp_path) == doc
    assert catalog.pull_public_token_prices(store, config, NOW) == "skipped_recently"
    monkeypatch.setattr(catalog, "_get_catalog_json", lambda *_: {"bad": True})
    assert (
        catalog.pull_public_token_prices(store, config, NOW + timedelta(days=1))
        == "invalid_artifact"
    )
    assert catalog.load_token_prices(tmp_path) == doc


def test_enterprise_skip_and_source_overrides(tmp_path):
    env = {
        "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
        "PRACTICEGRAPH_API_BASE_URL": "https://org.example",
    }
    cfg = resolve(env)
    store = Store.in_data_dir(tmp_path)
    assert catalog.pull_public_token_prices(store, cfg, NOW) == "skipped_enterprise_configured"
    custom = resolve({**env, "PRACTICEGRAPH_CONTENT_BASE_URL": "https://content.example"})
    assert custom.token_prices_source_url == "https://content.example/token-prices.json"
    explicit = resolve(
        {
            **env,
            "PRACTICEGRAPH_CONTENT_BASE_URL": "https://content.example",
            "PRACTICEGRAPH_TOKEN_PRICES_URL": "https://prices.example/edition.json",
        }
    )
    assert explicit.token_prices_source_url == "https://prices.example/edition.json"


def test_decimal_formatting_does_not_create_a_price_change(research):
    research["offers"][0]["pricing"]["input"] = "0.1"
    old = build(research)
    new = advance(research)
    new["offers"][0]["pricing"]["input"] = "0.10"
    assert reading(build(new, old), NOW)["changes"] == []


def test_identity_cannot_be_reused_after_an_absent_edition(research):
    old = build(research)
    second = advance(research)
    second["offers"][0]["id"] = "different-offer"
    middle = build(second, old)
    third = advance(research, "prices-2026-09-12-02")
    third["observed_at"] = "2026-09-12T01:00:00Z"
    third["offers"][0]["observed_at"] = third["observed_at"]
    third["offers"][0]["api_model_id"] = "changed-identity"
    with pytest.raises(ValueError):
        build(third, middle)
