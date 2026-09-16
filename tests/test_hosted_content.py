"""Release gates and offline reading use the same closed content contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from tools.content_release import inventory, prepare, validate_draft

from conftest import sealed
from practicegraph import catalog
from practicegraph.config import resolve
from practicegraph.content import profile_url
from practicegraph.store import Store
from synthetic_docs_fixture import SAMPLE_DOCS_DOCUMENT
from test_community_delivery import edition

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def test_draft_validation_is_read_only_and_preserves_version_history(tmp_path):
    path = tmp_path / "docs.json"
    prior = tmp_path / "previous.json"
    document = dict(SAMPLE_DOCS_DOCUMENT)
    document["docs_version"] = "docs-2026-09-05"
    original = json.dumps(document).encode()
    path.write_bytes(original)
    prior.write_bytes(original)
    result = validate_draft("docs", path, prior, today=NOW.date())
    assert result["edition_date"] == "2026-09-05" and result["review_due"] is False
    assert path.read_bytes() == original
    document["sections"] = document["sections"][:1]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="new version"):
        validate_draft("docs", path, prior, today=NOW.date())
    document["docs_version"] = "docs-2026-09-06"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="future"):
        validate_draft("docs", path, prior, today=NOW.date())


@pytest.mark.parametrize("missing", ["docs.json", "CONTENT_LICENSE.txt", "catalog-manifest.json"])
def test_release_gate_rejects_catalog_or_metadata_excluded_from_deployment(tmp_path, missing):
    (tmp_path / "CONTENT_LICENSE.txt").write_text("Synthetic terms")
    (tmp_path / "docs.json").write_text(json.dumps(SAMPLE_DOCS_DOCUMENT))
    required = ["docs.json", "CONTENT_LICENSE.txt", "catalog-manifest.json"]
    (tmp_path / ".vercelignore").write_text(
        "/*\n" + "\n".join("!" + name for name in required if name != missing)
    )
    with pytest.raises(ValueError, match="deployment allowlist"):
        inventory(tmp_path)
    (tmp_path / ".vercelignore").write_text("/*\n" + "\n".join("!" + name for name in required))
    assert "docs" in inventory(tmp_path)["channels"]


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


def _site(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "CONTENT_LICENSE.txt").write_text("Test content terms.")
    (site / "community.json").write_text(json.dumps(edition()))
    return site


def test_release_manifest_archive_and_rollback(tmp_path):
    site = _site(tmp_path)
    archive = tmp_path / "private/releases"
    first_bytes = (site / "community.json").read_bytes()
    first = prepare(site, archive)
    assert first == inventory(site)
    changed = edition()
    changed["community_version"] = "community-2026-09-05"
    (site / "community.json").write_text(json.dumps(changed))
    prepare(site, archive)
    (site / "community.json").write_bytes(first_bytes)
    assert prepare(site, archive) == first
    assert len(list((archive / "community").glob("*.json"))) == 2


@pytest.mark.parametrize("problem", ["draft", "invalid", "reuse", "inside"])
def test_release_refuses_unsafe_or_inconsistent_site_before_mutation(tmp_path, problem):
    site = _site(tmp_path)
    archive = tmp_path / "private/releases"
    prepare(site, archive)
    before = (site / "catalog-manifest.json").read_bytes()
    if problem == "draft":
        (site / "build-ideas-next.json").write_text("{}")
    elif problem == "invalid":
        (site / "models.json").write_text("{}")
    elif problem == "reuse":
        changed = edition()
        changed["items"][0]["hook"] = "Different editorial copy."
        (site / "community.json").write_text(json.dumps(changed))
    else:
        archive = site / "private"
    with pytest.raises(ValueError):
        prepare(site, archive)
    assert (site / "catalog-manifest.json").read_bytes() == before


def test_archive_refuses_rewriting_an_old_version_after_rollback(tmp_path):
    site = _site(tmp_path)
    archive = tmp_path / "private/releases"
    prepare(site, archive)
    (site / "catalog-manifest.json").unlink()
    changed = edition()
    changed["items"][0]["hook"] = "Reusing an old version must fail."
    (site / "community.json").write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="already archived"):
        prepare(site, archive)


def test_windows_authoring_produces_byte_exact_deployment_and_archive(tmp_path):
    import hashlib

    site = _site(tmp_path)
    target = site / "community.json"
    target.write_bytes(json.dumps(edition(), indent=2).replace("\n", "\r\n").encode())
    manifest = prepare(site, tmp_path / "archive")
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert manifest["channels"]["community"]["sha256"] == hashlib.sha256(raw).hexdigest()
    archived = tmp_path / "archive/community/community-2026-09-04.json"
    assert archived.read_bytes() == raw
    assert inventory(site) == manifest
