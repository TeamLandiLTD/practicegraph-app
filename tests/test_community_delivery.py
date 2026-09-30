"""Community publishing rails and privacy-safe matching, without network calls."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import practicegraph.catalog as catalog
from conftest import FIXTURE_GENERATED_AT, build_fixture_extras, build_fixture_snapshot, sealed
from practicegraph import __version__
from practicegraph.analysis.insights import Finding
from practicegraph.analysis.ratecard import RATE_CARD_VERSION
from practicegraph.config import resolve
from practicegraph.report.viewmodel import view_model
from practicegraph.store import Store

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def edition():
    return {
        "community_version": "community-2026-09-04",
        "items": [{
            "id": "small-retry", "kind": "practice", "title": "Try a smaller next step",
            "hook": "Practitioners compared ways to recover from repeated failures.",
            "finding": "A bounded reproduction made the next attempt easier to inspect.",
            "url": "https://example.com/discussion", "source": "Developer discussion",
            "observed": "2026-09-04", "answers": "retry_storm",
        }],
    }


def test_edition_reaches_local_reading_and_survives_invalid_refresh(tmp_path, monkeypatch):
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    monkeypatch.setattr(
        catalog, "_get_json", lambda *_args, **_kwargs: sealed("community", edition())
    )
    assert catalog.pull_public_community(store, config, NOW) == "pulled"
    assert catalog.load_community(tmp_path).items[0].item_id == "small-retry"
    assert catalog.pull_public_community(store, config, NOW) == "skipped_recently"
    monkeypatch.setattr(catalog, "_get_json", lambda *_args, **_kwargs: {"bad": "data"})
    assert catalog.pull_public_community(
        store, config, NOW + timedelta(minutes=16),
    ) == "invalid_artifact"
    assert catalog.load_community(tmp_path).items[0].item_id == "small-retry"
    status = catalog.feed_status(tmp_path, "community")
    assert status["version"] == "community-2026-09-04"
    assert status["edition_date"] == "2026-09-04"


def test_repeated_fetch_is_not_a_new_daily_edition(tmp_path):
    for offset in range(4):
        catalog._archive_artifact(
            tmp_path, "community", (NOW + timedelta(days=offset)).date().isoformat(), edition(),
        )
    shelf = tmp_path / "catalog" / "history" / "community"
    assert len(list(shelf.glob("*.json"))) == 1


def test_community_findings_stay_on_the_community_page(tmp_path):
    """Community items are delivered whole to the Community page, whether or
    not one of the person's own findings matches. The "From the community"
    lines that used to sit on Models, Work rhythm and Practice are gone
    (2026-09-29), so the view carries no per-page links."""
    path = tmp_path / "catalog" / "community.json"
    path.parent.mkdir()
    path.write_text(json.dumps(edition()), encoding="utf-8")
    extras = dataclasses.replace(
        build_fixture_extras(),
        community=list(catalog.load_community(tmp_path).items),
        findings=[Finding("retry_storm", "watch", 3)],
    )
    model = view_model(
        build_fixture_snapshot(), extras, FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION,
    )
    assert "community_links" not in model
    assert [item["id"] for item in model["community"]] == ["small-retry"]
    unmatched = view_model(
        build_fixture_snapshot(), dataclasses.replace(extras, findings=[]),
        FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION,
    )
    assert "community_links" not in unmatched
    assert len(unmatched["community"]) == 1
