"""Release gates and offline reading use the same closed content contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import sealed
from practicegraph import catalog
from practicegraph.config import resolve
from practicegraph.content import profile_url
from practicegraph.store import Store
from synthetic_docs_fixture import SAMPLE_DOCS_DOCUMENT
from test_community_delivery import edition

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def test_base_origin_preserves_channel_overrides_and_explicit_intent(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "content_base_url": "https://mirror.example/editions/",
                "models_source_url": "https://models.example/models.json",
            }
        )
    )
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    assert config.news_source_url == "https://mirror.example/editions/news.json"
    assert config.news_source_url_source == "file"
    assert config.models_source_url == "https://models.example/models.json"
    config = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_CONTENT_BASE_URL": "https://env.example/catalogs",
            "PRACTICEGRAPH_NEWS_URL": "https://news.example/news.json",
        }
    )
    assert config.news_source_url == "https://news.example/news.json"
    assert config.docs_source_url == "https://env.example/catalogs/docs.json"
    assert config.docs_source_url_source == "env"


def test_profile_pull_is_separate_and_preserves_custom_query(tmp_path, monkeypatch):
    config = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_DOCS_URL": "https://catalog.example/v1/docs.json?edition=public",
        }
    )
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    seen = []

    def get(url, **_kwargs):
        seen.append(url)
        channel = "docs-productivity" if "docs-productivity" in url else "docs"
        return sealed(channel, SAMPLE_DOCS_DOCUMENT)

    monkeypatch.setattr(catalog, "_get_json", get)
    assert catalog.pull_public_docs(store, config, NOW) == "pulled"
    (tmp_path / "config.json").write_text('{"profile":"productivity"}')
    assert catalog.pull_public_docs(store, config, NOW) == "pulled"
    assert seen == [
        config.docs_source_url,
        "https://catalog.example/v1/docs-productivity.json?edition=public",
    ]
    assert catalog.load_docs(tmp_path, "productivity").sections
    assert catalog.pull_public_docs(store, config, NOW) == "skipped_recently"
    assert profile_url("https://example.com/docs.json", "coding").endswith("docs.json")


def test_failed_refresh_keeps_receipt_and_old_date_never_becomes_fresh(tmp_path, monkeypatch):
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    monkeypatch.setattr(
        catalog, "_get_json", lambda *_args, **_kwargs: sealed("community", edition())
    )
    assert catalog.pull_public_community(store, config, NOW) == "pulled"
    path = tmp_path / "catalog/community.json"
    original_mtime = path.stat().st_mtime_ns
    later = NOW + timedelta(days=20)
    assert catalog.pull_public_community(store, config, later) == "pulled"
    assert path.stat().st_mtime_ns == original_mtime
    status = catalog.feed_status(tmp_path, "community", now=later)
    assert status["state"] == "stale"
    assert status["checked_at"] == later.isoformat()
    assert status["source_label"] == "Curated by TeamLandi"
    monkeypatch.setattr(catalog, "_get_json", lambda *_args, **_kwargs: "timeout")
    assert catalog.pull_public_community(store, config, later + timedelta(hours=1)) == "timeout"
    assert catalog.feed_status(tmp_path, "community", now=later) == status


def test_tampered_or_legacy_cache_never_inherits_official_attribution(tmp_path):
    catalog._accept_editorial(
        tmp_path, "community", edition(), "https://custom.example/feed.json?token=private", NOW
    )
    status = catalog.feed_status(tmp_path, "community", now=NOW)
    assert status["source_label"] == "Custom catalog"
    assert status["source_url"] == "https://custom.example/feed.json"
    changed = edition()
    changed["community_version"] = "community-2026-09-05"
    path = tmp_path / "catalog/community.json"
    path.write_text(json.dumps(changed))
    status = catalog.feed_status(tmp_path, "community", now=NOW)
    assert status["source_url"] is None
    assert status["source_label"] == "Cached catalog · source not recorded"
    path.write_text('{"community_version":"community-2026-09-05","items":"broken"}')
    assert catalog.feed_status(tmp_path, "community", now=NOW)["state"] == "missing"


