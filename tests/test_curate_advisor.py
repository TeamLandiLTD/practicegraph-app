"""Curator tool for the Advisor artifact: validate | preview | publish
(docs/ADVISOR_PLAN.md Phase 2). Same discipline as curate_models: the closed
production parser is the authority; publish is explicit and atomic."""

from __future__ import annotations

import json
from pathlib import Path

import tools.curate_advisor as curator

from practicegraph.analysis.advisor_market import LEGACY_SCHEMA as SCHEMA
from practicegraph.analysis.advisor_market import parse_advisor_artifact


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
                "steelman": "The premium family keeps the best first-pass rate.",
                "action": "Set the budget family as the default for routine edits.",
                "experiment": "Route one week of rename work down and compare rework.",
                "as_of": "2026-07-19",
                "expires": "2026-08-16",
            }
        ],
    }


def _write_draft(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_validate_accepts_and_derives_missing_version(tmp_path: Path, capsys) -> None:
    document = _good()
    del document["artifact_version"]
    draft = _write_draft(tmp_path / "draft.json", document)
    assert curator.main(["validate", str(draft)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["artifact_version"].startswith("advisor-2026-07-19-")


def test_validate_reports_curator_hints_on_failure(tmp_path: Path, capsys) -> None:
    document = _good()
    document["verdicts"][0]["tier"] = "ship-it"  # type: ignore[index]
    draft = _write_draft(tmp_path / "draft.json", document)
    assert curator.main(["validate", str(draft)]) == 1
    err = capsys.readouterr().err
    assert "verdicts[0].tier" in err


def test_preview_summarizes_models_and_verdicts(tmp_path: Path, capsys) -> None:
    draft = _write_draft(tmp_path / "draft.json", _good())
    assert curator.main(["preview", str(draft)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert set(preview["models"]) == {"claude_fable", "claude_haiku"}
    assert preview["verdicts"]["budget-first-ladder"]["tier"] == "use"
    assert preview["stale_on_publish"] is True


def test_publish_writes_the_validated_artifact_atomically(
    tmp_path: Path, capsys
) -> None:
    draft = _write_draft(tmp_path / "draft.json", _good())
    site = tmp_path / "site"
    site.mkdir()
    assert curator.main(["publish", str(draft), "--site", str(site)]) == 0
    target = site / "advisor.json"
    assert str(target) in capsys.readouterr().out
    published = json.loads(target.read_text(encoding="utf-8"))
    assert parse_advisor_artifact(published) is not None
    assert not list(site.glob("*.tmp"))


def test_publish_requires_an_existing_site_directory(tmp_path: Path, capsys) -> None:
    draft = _write_draft(tmp_path / "draft.json", _good())
    missing = tmp_path / "nope"
    assert curator.main(["publish", str(draft), "--site", str(missing)]) == 1
    assert "publish:" in capsys.readouterr().err
