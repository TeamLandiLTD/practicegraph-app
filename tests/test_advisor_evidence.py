"""Synthetic evidence: partial feeds, citation safety, and supported recommendations."""

from __future__ import annotations

import copy
import dataclasses
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tools import curate_advisor
from tools.content_release import validate_draft

from practicegraph import catalog
from practicegraph.analysis.advisor import build_advisor_board
from practicegraph.analysis.advisor_market import (
    METRICS,
    SCHEMA,
    advisor_artifact_to_dict,
    parse_advisor_artifact,
)
from practicegraph.analysis.advisor_receipts import build_receipts
from practicegraph.config import resolve
from practicegraph.report.shell import _advisor_card
from practicegraph.store import ContributionRow, Store

DAY = date(2026, 9, 6)
BASIS = "Standard API, USD per million tokens, up to 200K context"


def _measurement(value: object, **changes: object) -> dict[str, Any]:
    return {
        "value": value, "source_name": "Provider A",
        "source_url": "https://provider.example.org/docs/pricing",
        "observed_on": "2026-09-05", "basis": BASIS, **changes,
    }


def _document() -> dict[str, Any]:
    return {
        "schema": SCHEMA, "artifact_version": "advisor-2026-09-05-test",
        "as_of": "2026-09-05", "expires": "2026-09-30",
        "models": [
            {
                "family": family, "model_id": identifier,
                "metrics": {
                    "price_in_micro": _measurement(price_in),
                    "price_out_micro": _measurement(price_out),
                    "coding_index_tenths": None,
                },
            }
            for family, identifier, price_in, price_out in (
                ("claude_fable", "claude-fable-test", 10_000_000, 50_000_000),
                ("claude_haiku", "claude-haiku-test", 1_000_000, 5_000_000),
            )
        ],
        "verdicts": [],
    }


def _verdict() -> dict[str, Any]:
    return {
        "id": "small-trial", "tier": "try", "families": ["claude_haiku"],
        "tools": ["claude_code"], "verdict": "Try a small routine edit.",
        "boundary": "Compare the result before changing the default.",
        "steelman": "", "action": "Review the generated diff.", "experiment": "",
        "as_of": "2026-09-05", "expires": "2026-09-30",
        "evidence": [{"family": "claude_haiku", "metric": "price_out_micro"}],
    }


def _receipts(**extra: int):
    rows = []
    for day, cost in (("2026-09-03", 34_000_000), ("2026-09-04", 33_000_000),
                      ("2026-09-05", 33_000_000)):
        values = dict.fromkeys(ContributionRow._fields, 0)
        values.update(assistant_turns=20, cost_micro_usd=cost)
        if day == "2026-09-03":
            values.update(input_tokens=1_000_000, output_tokens=2_000_000, **extra)
        rows.append((day, "claude_code", "claude-fable-5",
                     [values[key] for key in ContributionRow._fields]))
    return build_receipts(rows, DAY)


def _board(document=None, *, today=DAY, **tokens):
    artifact = parse_advisor_artifact(document if document is not None else _document())
    assert artifact is not None
    return build_advisor_board(_receipts(**tokens), artifact, today)


def test_partial_multi_source_edition_round_trips_without_inventing_metrics():
    document = _document()
    document["models"][0]["metrics"]["median_tps_tenths"] = _measurement(
        1234, source_name="Benchmark Lab", source_url="https://bench.example.org/model/test",
        basis="Output throughput, provider A, standard inference",
    )
    artifact = parse_advisor_artifact(document)
    assert artifact is not None
    assert artifact.models[0].coding_index_tenths is None
    assert artifact.models[0].released_on is None
    assert artifact.models[0].median_tps_tenths == 1234
    serialized = advisor_artifact_to_dict(artifact)
    assert serialized["schema"] == SCHEMA and "source" not in serialized
    assert parse_advisor_artifact(serialized) == artifact
    assert set(serialized["models"][0]["metrics"]) == set(METRICS)


@pytest.mark.parametrize("value", [True, -1, 1.5, "100", [], {}, float("nan"), None])
def test_unknown_is_explicit_and_malformed_known_values_are_rejected(value):
    document = _document()
    document["models"][0]["metrics"]["price_in_micro"]["value"] = value
    assert parse_advisor_artifact(document) is None


@pytest.mark.parametrize("url", [
    "http://example.org/x", "javascript:alert(1)", "https://user:secret@example.org/x",
    "https://example.org/x?key=secret", "https://example.org/x#key", "https://localhost/x",
    "https://127.0.0.1/x", "https://[::1]/x", "https://10.1.2.3/x",
    "https://example.local/x", "https://example.org:8443/x", "https://example.org:bad/x",
    "https://example.org/\npath", "https://example.org/%0dpath", "https://example.org\\evil/x",
])
def test_source_urls_cannot_carry_credentials_local_targets_or_active_content(url):
    document = _document()
    document["models"][0]["metrics"]["price_in_micro"]["source_url"] = url
    assert parse_advisor_artifact(document) is None


@pytest.mark.parametrize("change", ["missing-source", "future-observation", "unknown-metric",
                                   "empty-model", "bad-family", "future-release"])
def test_evidence_structure_and_dates_are_closed(change):
    document = _document()
    row = document["models"][0]
    if change == "missing-source":
        del row["metrics"]["price_in_micro"]["source_name"]
    elif change == "future-observation":
        row["metrics"]["price_in_micro"]["observed_on"] = "2026-09-06"
    elif change == "unknown-metric":
        row["metrics"]["intelligence_index"] = _measurement(90)
    elif change == "empty-model":
        row["metrics"] = {"price_in_micro": None}
    elif change == "bad-family":
        row["family"] = []
    else:
        row["metrics"]["released_on"] = _measurement("2026-09-06")
    assert parse_advisor_artifact(document) is None


def test_prices_support_a_trial_without_benchmark_or_release_date():
    board = _board()
    assert board.market_state == "partial"
    take = board.takes[0]
    assert take.take_id == "premium-routing" and take.tier == "try"
    assert take.impact_micro_usd == 89_000_000
    assert take.attribution == "Market data: Provider A"
    assert take.expires == "2026-09-19"
    assert {item.metric for item in take.evidence} == {"price_in_micro", "price_out_micro"}
    assert all(item.observed_on == "2026-09-05" for item in take.evidence)


def test_only_cited_prices_get_attribution_and_benchmarks_do_not_choose_candidates():
    document = _document()
    cheap = document["models"][1]
    cheap["metrics"]["coding_index_tenths"] = _measurement(
        1000, source_name="Benchmark Lab", source_url="https://bench.example.org/score",
        basis="Synthetic Coding Index, harness test A",
    )
    extra = copy.deepcopy(cheap)
    extra.update(family="claude_sonnet", model_id="claude-sonnet-test")
    extra["metrics"] = {
        "price_in_micro": _measurement(500_000), "price_out_micro": _measurement(2_500_000),
    }
    document["models"].append(extra)
    take = _board(document).takes[0]
    assert "Sonnet" in take.action
    assert "Benchmark Lab" not in take.attribution
    assert all(item.metric != "coding_index_tenths" for item in take.evidence)


@pytest.mark.parametrize("metric", ["price_in_micro", "price_out_micro"])
def test_missing_required_price_suppresses_routing(metric):
    document = _document()
    document["models"][1]["metrics"][metric] = None
    assert _board(document).takes[0].take_id == "calm-default"


def test_cache_rate_is_required_only_when_cached_tokens_were_used():
    assert _board().takes[0].take_id == "premium-routing"
    assert _board(cached_tokens=1_000_000).takes[0].take_id == "calm-default"
    document = _document()
    document["models"][1]["metrics"]["price_cache_read_micro"] = _measurement(100_000)
    take = _board(document, cached_tokens=1_000_000).takes[0]
    assert take.impact_micro_usd == 88_900_000
    assert "price_cache_read_micro" in {item.metric for item in take.evidence}


def test_cache_write_prices_are_never_assumed_to_equal_input_prices():
    assert _board(cache_creation_tokens=1_000).takes[0].take_id == "calm-default"


def test_verified_zero_price_is_free_not_missing_or_zero_times_cheaper():
    document = _document()
    document["models"][1]["metrics"]["price_out_micro"]["value"] = 0
    take = _board(document).takes[0]
    assert take.impact_micro_usd == 99_000_000
    assert "no output-token charge" in take.market
    assert "0.0x" not in take.market


def test_different_context_or_service_tiers_are_not_compared():
    document = _document()
    for measurement in document["models"][1]["metrics"].values():
        if measurement:
            measurement["basis"] = "Batch API, USD per million tokens"
    assert _board(document).takes[0].take_id == "calm-default"


def test_output_only_comparison_uses_both_sources_without_requiring_other_metrics():
    document = _document()
    for row in document["models"]:
        row["metrics"] = {"price_out_micro": row["metrics"]["price_out_micro"]}
    document["models"][1]["metrics"]["price_out_micro"].update(
        source_name="Provider B", source_url="https://provider-b.example.org/pricing",
    )
    rows = []
    for day in ("2026-09-03", "2026-09-04", "2026-09-05"):
        for model, cost in (("claude-haiku-4-5", 14_000_000), ("claude-fable-5", 7_000_000)):
            values = dict.fromkeys(ContributionRow._fields, 0)
            values.update(assistant_turns=14, cost_micro_usd=cost)
            rows.append((day, "claude_code", model,
                         [values[key] for key in ContributionRow._fields]))
    artifact = parse_advisor_artifact(document)
    board = build_advisor_board(build_receipts(rows, DAY), artifact, DAY)
    take = board.takes[0]
    assert take.take_id == "sticker-price-lie"
    assert take.attribution == "Market data: Provider A, Provider B"
    assert {item.metric for item in take.evidence} == {"price_out_micro"}


def test_stale_measurements_cannot_be_revived_by_new_edition_dates():
    document = _document()
    for row in document["models"]:
        for measurement in row["metrics"].values():
            if measurement:
                measurement["observed_on"] = "2026-08-01"
    assert parse_advisor_artifact(document) is not None  # historical facts remain readable
    assert _board(document).takes[0].take_id == "calm-default"
    assert _board(today=date(2026, 9, 20)).takes[0].take_id == "calm-default"
    assert _board(today=date(2026, 9, 4)).takes[0].take_id == "calm-default"


def test_curated_verdicts_require_present_current_citations_and_expire_with_them():
    document = _document()
    card = _verdict()
    card["families"] = ["claude_fable"]
    card["evidence"][0]["family"] = "claude_fable"
    document["verdicts"] = [card]
    assert any(t.take_id == "curated-verdict" for t in _board(document).takes)
    for today in (date(2026, 9, 20), date(2026, 10, 1)):
        assert all(t.take_id != "curated-verdict" for t in _board(document, today=today).takes)
    card["evidence"][0]["metric"] = "coding_index_tenths"
    assert parse_advisor_artifact(document) is None
    card["evidence"] = []
    assert parse_advisor_artifact(document) is None


def test_report_links_each_used_measurement_and_escapes_untrusted_copy():
    board = _board()
    rendered = _advisor_card(board)
    assert "partial market data" in rendered
    assert "Sources and measurements" in rendered
    assert "$1 / 1M tokens" in rendered
    assert "observed 2026-09-05" in rendered and BASIS in rendered
    assert 'href="https://provider.example.org/docs/pricing"' in rendered
    malicious = dataclasses.replace(board.takes[0].evidence[0],
        source_name='<img src=x onerror="alert(1)">', source_url="javascript:alert(1)")
    board = dataclasses.replace(board, takes=(dataclasses.replace(
        board.takes[0], evidence=(malicious,),
    ),))
    rendered = _advisor_card(board)
    assert "<img" not in rendered and 'href="javascript:' not in rendered
    assert "&lt;img" in rendered


def test_validator_preview_and_publisher_support_partial_evidence(tmp_path, capsys):
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(_document()), encoding="utf-8")
    validate_draft("advisor", draft, today=DAY)
    assert curate_advisor.main(["preview", str(draft)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert "coding_index_tenths" in preview["models"]["claude_haiku"]["unknown_metrics"]
    assert "source_name" in json.dumps(preview["document"])
    site = tmp_path / "site"
    site.mkdir()
    assert curate_advisor.main(["publish", str(draft), "--site", str(site)]) == 0
    assert parse_advisor_artifact(json.loads((site / "advisor.json").read_bytes())) is not None


def test_downloader_accepts_v2_and_preserves_it_when_later_evidence_is_invalid(
    tmp_path: Path, monkeypatch,
):
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    now = datetime(2026, 9, 6, tzinfo=UTC)
    document = _document()
    monkeypatch.setattr(catalog, "_get_json", lambda *_args, **_kwargs: document)
    assert catalog.pull_public_advisor(store, config, now) == "pulled"
    assert catalog.load_advisor(tmp_path) == parse_advisor_artifact(document)
    accepted = (tmp_path / "catalog/advisor.json").read_bytes()
    document["models"][0]["metrics"]["price_in_micro"]["source_url"] = "http://example.org"
    assert catalog.pull_public_advisor(store, config, now + timedelta(days=1)) == "invalid_artifact"
    assert (tmp_path / "catalog/advisor.json").read_bytes() == accepted
