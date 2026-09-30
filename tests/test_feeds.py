"""The editorial shelves: dated history behind news, and the Let's build feed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

import practicegraph.catalog as catalog_module
from conftest import sealed
from practicegraph.analysis.build_ideas import parse_build_ideas_artifact
from practicegraph.catalog import (
    HISTORY_KEEP_DAYS,
    list_artifact_days,
    load_build_ideas,
    load_build_ideas_for_day,
    load_news_for_day,
    pull_public_build_ideas,
    pull_public_news,
)
from practicegraph.config import Config
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store
from synthetic_build_ideas_fixture import (
    SAMPLE_BUILD_IDEAS,
    SAMPLE_REPO_PICKS,
)

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        data_dir_source="env",
        api_base_url=None,
        api_base_url_source="default",
        org_id="acme-eng",
        org_id_source="env",
        org_token_present=False,
        org_token_source="default",
        config_file_state="absent",
    )


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "data" / "state.db")
    store.migrate()
    return store


def _news_artifact(marker: str = "one") -> dict[str, object]:
    return {
        "news_version": f"shelf-{marker}",
        "items": [
            {
                "id": f"shelf-{marker}",
                "kind": "release",
                "title": f"Edition {marker}",
                "hook": "A compact control surface keeps common actions close.",
                "summary": "The project maps a small keyboard to frequent controls.",
                "why": "A concrete example of shaping tools around repeated work.",
                "url": "https://example.org/item",
                "source": "Example source",
            }
        ],
    }


def _ideas_artifact() -> dict[str, object]:
    idea = SAMPLE_BUILD_IDEAS[0]
    return {
        "ideas_version": "served-1",
        "items": [
            {
                "id": "served-idea",
                "api": idea.api,
                "feature": idea.feature,
                "title": idea.title,
                "hook": idea.hook,
                "summary": idea.summary,
                "steps": list(idea.steps),
                "why": idea.why,
                "url": idea.url,
                "source": idea.source,
            }
        ],
    }


def test_news_pull_archives_a_dated_copy_and_prunes_the_shelf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(
        catalog_module,
        "_get_json",
        lambda url, timeout_s=10.0, **_kwargs: sealed("news", _news_artifact(f"day-{offset}")),
    )
    # One pull per day for a month: the shelf holds only the newest
    # HISTORY_KEEP_DAYS, oldest pruned, newest first.
    for offset in range(HISTORY_KEEP_DAYS + 6):
        assert pull_public_news(store, config, NOW + timedelta(days=offset)) == "pulled"
    days = list_artifact_days(config.data_dir, "news")
    assert len(days) == HISTORY_KEEP_DAYS
    assert days == tuple(sorted(days, reverse=True))
    assert days[0] == (NOW + timedelta(days=HISTORY_KEEP_DAYS + 5)).date().isoformat()
    # An archived day reads back through the same strict parse.
    items = load_news_for_day(config.data_dir, days[0])
    assert items and items[0].news_id == f"shelf-day-{HISTORY_KEEP_DAYS + 5}"


def test_a_tampered_archived_day_reads_as_empty_not_as_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(
        catalog_module,
        "_get_json",
        lambda url, timeout_s=10.0, **_kwargs: sealed("news", _news_artifact()),
    )
    assert pull_public_news(store, config, NOW) == "pulled"
    day = NOW.date().isoformat()
    shelf = config.data_dir / "catalog" / "history" / "news"
    (shelf / f"{day}.json").write_text('{"broken": true}', encoding="utf-8")
    assert load_news_for_day(config.data_dir, day) == ()
    # A traversal-shaped "day" is refused by shape, before any path math.
    assert load_news_for_day(config.data_dir, "../../../etc/passwd") == ()
    # A stray non-day file on the shelf is not a day and is never deleted.
    (shelf / "notes.json").write_text("{}", encoding="utf-8")
    assert "notes" not in list_artifact_days(config.data_dir, "news")
    assert pull_public_news(store, config, NOW + timedelta(days=1)) == "pulled"
    assert (shelf / "notes.json").exists()


def test_build_ideas_pull_validates_archives_and_starts_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path)
    # Before any pull: no shipped editorial recommendations.
    assert load_build_ideas(config.data_dir).ideas == ()
    assert load_build_ideas(config.data_dir).repos == ()

    monkeypatch.setattr(
        catalog_module,
        "_get_json",
        lambda url, timeout_s=10.0, **_kwargs: sealed("build-ideas", _ideas_artifact()),
    )
    assert pull_public_build_ideas(store, config, NOW) == "pulled"
    assert load_build_ideas(config.data_dir).ideas[0].idea_id == "served-idea"
    day = NOW.date().isoformat()
    assert list_artifact_days(config.data_dir, "build-ideas") == (day,)
    archived = load_build_ideas_for_day(config.data_dir, day)
    assert archived.ideas[0].idea_id == "served-idea"
    # Fifteen-minute claim, independent of the news channel's claim.
    assert pull_public_build_ideas(store, config, NOW + timedelta(minutes=5)) == "skipped_recently"

    # A malformed serve never reaches disk.
    monkeypatch.setattr(
        catalog_module,
        "_get_json",
        lambda url, timeout_s=10.0, **_kwargs: {"ideas_version": "x", "items": [{"id": "y"}]},
    )
    assert pull_public_build_ideas(store, config, NOW + timedelta(minutes=20)) == "invalid_artifact"
    assert load_build_ideas(config.data_dir).ideas[0].idea_id == "served-idea"


def test_bundled_ideas_pass_their_own_parser_and_the_safety_scans() -> None:
    document = {
        "ideas_version": "bundled-echo",
        "items": [
            {
                "id": idea.idea_id,
                "api": idea.api,
                "feature": idea.feature,
                "title": idea.title,
                "hook": idea.hook,
                "summary": idea.summary,
                "steps": list(idea.steps),
                "why": idea.why,
                "url": idea.url,
                "source": idea.source,
            }
            for idea in SAMPLE_BUILD_IDEAS
        ],
    }
    assert parse_build_ideas_artifact(document) is not None
    for idea in SAMPLE_BUILD_IDEAS:
        for text in (
            idea.feature,
            idea.title,
            idea.hook,
            idea.summary,
            idea.why,
            idea.source,
            *idea.steps,
        ):
            assert lexicon_violations(text) == []
            assert leak_findings(text) == []
        assert idea.url.startswith("https://")
        # Synthetic fixtures use a reserved example origin.
        assert idea.url.split("/")[2] in ("example.com",)


def test_ideas_parser_is_closed_over_keys_apis_and_step_counts() -> None:
    base = _ideas_artifact()
    assert parse_build_ideas_artifact(base) is not None

    extra = json.loads(json.dumps(base))
    extra["items"][0]["surprise"] = "key"
    assert parse_build_ideas_artifact(extra) is None

    wrong_api = json.loads(json.dumps(base))
    wrong_api["items"][0]["api"] = "acme"
    assert parse_build_ideas_artifact(wrong_api) is None

    one_step = json.loads(json.dumps(base))
    one_step["items"][0]["steps"] = ["only one"]
    assert parse_build_ideas_artifact(one_step) is None

    http_url = json.loads(json.dumps(base))
    http_url["items"][0]["url"] = "http://docs.claude.com/x"
    assert parse_build_ideas_artifact(http_url) is None


def test_the_repo_shelf_is_closed_github_only_and_hand_checked() -> None:
    """The GitHub shelf (added 2026-08-24): curated picks with an honest
    caveat, github.com project URLs only, never a scraped ranking. The
    bundled picks pass their own parser and every safety scan."""
    base = _ideas_artifact()
    base["repos"] = [
        {
            "id": pick.repo_id,
            "name": pick.name,
            "url": pick.url,
            "what": pick.what,
            "why": pick.why,
            **({"caveat": pick.caveat} if pick.caveat else {}),
        }
        for pick in SAMPLE_REPO_PICKS
    ]
    edition = parse_build_ideas_artifact(base)
    assert edition is not None and len(edition.repos) == len(SAMPLE_REPO_PICKS)

    for pick in SAMPLE_REPO_PICKS:
        for text in (pick.name, pick.what, pick.why, pick.caveat or "x"):
            assert lexicon_violations(text) == []
            assert leak_findings(text) == []
        assert pick.url.startswith("https://github.com/")

    # Only github.com project URLs - a deep path, a query string, or any
    # other host is refused with the whole edition.
    for bad_url in (
        "https://github.com/owner/name/issues/1",
        "https://github.com/owner/name?ref=trending",
        "https://gitlab.com/owner/name",
        "http://github.com/owner/name",
    ):
        broken = json.loads(json.dumps(base))
        broken["repos"][0]["url"] = bad_url
        assert parse_build_ideas_artifact(broken) is None, bad_url

    # An unknown key on a pick closes the edition.
    extra = json.loads(json.dumps(base))
    extra["repos"][0]["stars"] = 99999
    assert parse_build_ideas_artifact(extra) is None


def test_an_idea_may_carry_a_repo_receipt_but_only_a_github_one() -> None:
    base = _ideas_artifact()
    base["items"][0]["repo"] = "https://github.com/owner/example"
    edition = parse_build_ideas_artifact(base)
    assert edition is not None
    assert edition.ideas[0].repo == "https://github.com/owner/example"

    bad = json.loads(json.dumps(base))
    bad["items"][0]["repo"] = "https://example.org/not-github"
    assert parse_build_ideas_artifact(bad) is None


def test_todays_own_key_never_appears_as_a_previous_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shelf is keyed by the UTC day the pull happened on. The page used
    to drop "today" by comparing against the reader's LOCAL day, so for part
    of every day outside UTC today's edition sat on the shelf as a duplicate
    (2026-08-24 review). The engine owns the key, so the engine drops it."""
    store = _store(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(
        catalog_module,
        "_get_json",
        lambda url, timeout_s=10.0, **_kwargs: sealed("news", _news_artifact()),
    )
    assert pull_public_news(store, config, NOW) == "pulled"
    assert pull_public_news(store, config, NOW + timedelta(days=1)) == "pulled"
    today = NOW + timedelta(days=1)
    days = list_artifact_days(config.data_dir, "news", today)
    assert days == (NOW.date().isoformat(),)

    # 01:30 in Europe/Sofia on the 5th is still the 4th in UTC: the archive
    # key is the 4th, and asking as of that instant must still hide it.
    sofia = timezone(timedelta(hours=3))
    just_after_local_midnight = datetime(2026, 7, 5, 1, 30, tzinfo=sofia)
    assert just_after_local_midnight.date().isoformat() == "2026-07-05"
    assert pull_public_news(store, config, just_after_local_midnight) == "pulled"
    days = list_artifact_days(config.data_dir, "news", just_after_local_midnight)
    assert "2026-07-04" not in days, days


def test_legacy_duplicate_archive_days_collapse_without_deleting_files(tmp_path: Path) -> None:
    shelf = tmp_path / "catalog" / "history" / "news"
    shelf.mkdir(parents=True)
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        (shelf / f"{day}.json").write_text(json.dumps(_news_artifact()), encoding="utf-8")
    assert list_artifact_days(tmp_path, "news", NOW) == ()
    assert list_artifact_days(tmp_path, "news", NOW + timedelta(days=1)) == ("2026-07-03",)
    assert len(list(shelf.glob("*.json"))) == 3
