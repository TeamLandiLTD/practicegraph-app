from __future__ import annotations

import json
from pathlib import Path

import pytest
import tools.curate_models as curator

from practicegraph.analysis.model_intelligence import SCHEMA, parse_model_artifact


def draft_document(*, version: bool = True) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": SCHEMA,
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
    if version:
        document["artifact_version"] = "models-aa-coding-v1.1-2026-07-16"
    return document


def write_draft(path: Path, *, version: bool = True) -> Path:
    path.write_text(json.dumps(draft_document(version=version)), encoding="utf-8")
    return path


def test_validate_reports_field_specific_failure(tmp_path: Path, capsys) -> None:
    draft = draft_document()
    draft["variants"][0]["effort"] = "extreme"  # type: ignore[index]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(draft), encoding="utf-8")

    assert curator.main(["validate", str(path)]) == 1
    assert "variants[0].effort" in capsys.readouterr().err


def test_preview_matches_production_balanced_selection(tmp_path: Path, capsys) -> None:
    path = write_draft(tmp_path / "draft.json")

    assert curator.main(["preview", str(path)]) == 0
    preview = json.loads(capsys.readouterr().out)

    assert preview["recommendations"]["codex"]["balanced"] == "codex-balanced"
    assert preview["recommendations"]["codex"]["save_tokens"] == "codex-efficient"
    assert preview["recommendations"]["codex"]["maximum_capability"] == "codex-capable"
    assert preview["frontiers"]["codex"] == [
        "codex-efficient", "codex-balanced", "codex-capable",
    ]


def test_missing_version_is_derived_deterministically(tmp_path: Path, capsys) -> None:
    path = write_draft(tmp_path / "draft.json", version=False)

    assert curator.main(["validate", str(path)]) == 0
    first = capsys.readouterr().out
    assert curator.main(["validate", str(path)]) == 0
    second = capsys.readouterr().out

    assert first == second
    assert "models-aa-coding-1.1-2026-07-16-" in first


def test_publish_atomically_writes_only_models_json(tmp_path: Path) -> None:
    draft = write_draft(tmp_path / "draft.json", version=False)
    site = tmp_path / "site"
    site.mkdir()
    news = site / "news.json"
    news.write_text("keep", encoding="utf-8")

    assert curator.main(["publish", str(draft), "--site", str(site)]) == 0

    published = json.loads((site / "models.json").read_text(encoding="utf-8"))
    assert parse_model_artifact(published) is not None
    assert news.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in site.iterdir()) == ["models.json", "news.json"]


def test_failed_replace_preserves_previous_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    draft = write_draft(tmp_path / "draft.json")
    site = tmp_path / "site"
    site.mkdir()
    target = site / "models.json"
    target.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(curator.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))

    assert curator.main(["publish", str(draft), "--site", str(site)]) == 1
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(site.glob("*.tmp")) == []

