from __future__ import annotations

import json
from pathlib import Path

import pytest
import tools.curate_news as source_registry
import tools.curate_news_rich as curator

from practicegraph.analysis.news import parse_news_artifact
from practicegraph_server.rss import FeedSpec


def valid_draft(*, item_count: int = 2, version: bool = True) -> dict[str, object]:
    items = []
    for index in range(item_count):
        items.append(
            {
                "id": f"news-item-{index}",
                "kind": "release" if index == 0 else "post",
                "title": f"Useful AI development item {index}",
                "hook": "A concrete update for people building with coding agents.",
                "summary": "The source describes a focused change and how it works.",
                "why": "It may change how a current coding workflow is designed.",
                "url": f"https://example.org/news/{index}",
                "source": "Example source",
            }
        )
    document: dict[str, object] = {"items": items}
    if version:
        document["news_version"] = "news-curated-test"
    return document


def write_draft(path: Path, **kwargs: object) -> Path:
    path.write_text(json.dumps(valid_draft(**kwargs)), encoding="utf-8")
    return path


def test_urgency_survives_draft_normalization_and_requires_expiry(tmp_path):
    raw = valid_draft(item_count=1)
    attention = {"urgency": "urgent", "reason": "The endpoint closes tomorrow.",
                 "starts_at": "2026-09-11T09:00:00Z", "expires_at": "2026-09-12T09:00:00Z"}
    raw["items"][0]["attention"] = attention
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(raw))
    artifact, errors = curator.load_validated_draft(path)
    assert not errors and artifact["items"][0]["attention"] == attention
    del attention["expires_at"]
    path.write_text(json.dumps(raw))
    assert curator.load_validated_draft(path)[0] is None


def atom(entries: list[tuple[str, str, str]]) -> bytes:
    rows = "".join(
        "<entry>"
        f"<title>{title}</title>"
        f'<link href="{url}" />'
        f"<published>{published}</published>"
        "<summary>A useful &lt;b&gt;feed&lt;/b&gt; excerpt.</summary>"
        "</entry>"
        for title, url, published in entries
    )
    return (
        f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{rows}</feed>'
    ).encode()


def rss(entries: list[tuple[str, str, str]]) -> bytes:
    rows = "".join(
        "<item>"
        f"<title>{title}</title>"
        f"<link>{url}</link>"
        f"<pubDate>{published}</pubDate>"
        "<description>A useful feed excerpt.</description>"
        "</item>"
        for title, url, published in entries
    )
    return f"<?xml version='1.0'?><rss><channel>{rows}</channel></rss>".encode()


def test_requested_youtube_channels_are_in_source_registry() -> None:
    urls = {feed.url for feed in source_registry.FEEDS}

    for channel_id in (
        "UCXZCJLdBC09xxGZ6gcdrc6A",
        "UCrDwWp7EBBv4NwvScIpBDOA",
        "UCbRP3c757lWg9M-U7TyEkXA",
        "UCawZsQWqfGSbCI5yjkdVkTA",
        "UCqcbQf6yw5KzRoDDcZ_wBSw",
    ):
        assert f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}" in urls


def test_gather_is_bounded_deduplicated_and_text_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feeds = tuple(
        FeedSpec(f"https://feeds.example/{index}", "post", f"Source {index}") for index in range(31)
    )

    def fake_fetch(url: str, _max_bytes: int, _timeout: float) -> bytes:
        index = url.rsplit("/", 1)[-1]
        return atom(
            [
                (
                    f"Item {index}",
                    f"https://example.org/item/{index}#fragment",
                    f"2026-07-{(int(index) % 28) + 1:02d}T10:00:00Z",
                ),
                ("Duplicate", "https://example.org/shared/", "2026-07-01T10:00:00Z"),
                ("Unsafe", "http://example.org/plaintext", "2026-07-01T10:00:00Z"),
            ]
        )

    monkeypatch.setattr(curator, "FEEDS", feeds)
    monkeypatch.setattr(curator, "_fetch_feed", fake_fetch)
    output = tmp_path / "candidates.json"

    assert curator.gather(output) == 0
    candidates = json.loads(output.read_text(encoding="utf-8"))

    assert len(candidates) == 30
    assert len({item["url"] for item in candidates}) == len(candidates)
    assert all(item["url"].startswith("https://") for item in candidates)
    assert all("#" not in item["url"] for item in candidates)
    assert all("thumb_src" not in item and "thumb" not in item for item in candidates)
    assert all(len(item["description"]) <= 1200 for item in candidates)


def test_gather_orders_rfc_feed_dates_by_time_not_weekday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        curator,
        "FEEDS",
        (FeedSpec("https://feeds.example/news", "post", "Example"),),
    )
    monkeypatch.setattr(
        curator,
        "_fetch_feed",
        lambda *_args: rss(
            [
                ("Older Wednesday", "https://example.org/older", "Wed, 15 Jul 2026 10:00:00 GMT"),
                ("Newer Friday", "https://example.org/newer", "Fri, 17 Jul 2026 10:00:00 GMT"),
            ]
        ),
    )
    output = tmp_path / "candidates.json"

    assert curator.gather(output) == 0
    candidates = json.loads(output.read_text(encoding="utf-8"))

    assert [item["title"] for item in candidates] == ["Newer Friday", "Older Wednesday"]
    assert candidates[0]["published"].startswith("2026-07-17")


def test_validate_reports_named_field_and_changes_nothing(
    tmp_path: Path,
) -> None:
    draft = valid_draft()
    draft["items"][0]["hook"] = ""  # type: ignore[index]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(draft), encoding="utf-8")

    artifact, errors = curator.load_validated_draft(path)

    assert artifact is None
    assert any("items[0].hook" in error for error in errors)
    assert sorted(item.name for item in tmp_path.iterdir()) == ["bad.json"]


def test_missing_version_is_content_derived_and_stable(tmp_path: Path) -> None:
    path = write_draft(tmp_path / "draft.json", version=False)

    first, first_errors = curator.load_validated_draft(path)
    second, second_errors = curator.load_validated_draft(path)

    assert first_errors == second_errors == []
    assert first is not None and second is not None
    assert first == second
    assert str(first["news_version"]).startswith("news-curated-")


def test_publish_rejects_more_than_three_items(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    prior = site / "news.json"
    prior.write_text("previous", encoding="utf-8")
    draft = write_draft(tmp_path / "draft.json", item_count=4)

    assert curator.publish(draft, site) == 1
    assert prior.read_text(encoding="utf-8") == "previous"


def test_publish_atomically_replaces_only_news_json(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    other = site / "models.json"
    other.write_text("keep", encoding="utf-8")
    draft = write_draft(tmp_path / "draft.json", version=False)

    assert curator.publish(draft, site) == 0

    artifact = json.loads((site / "news.json").read_text(encoding="utf-8"))
    assert parse_news_artifact(artifact) is not None
    assert other.read_text(encoding="utf-8") == "keep"
    assert sorted(item.name for item in site.iterdir()) == ["models.json", "news.json"]
    assert all(
        set(item)
        == {
            "id",
            "kind",
            "title",
            "hook",
            "summary",
            "why",
            "url",
            "source",
        }
        for item in artifact["items"]
    )


def test_failed_atomic_replace_preserves_previous_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    target = site / "news.json"
    target.write_text("previous", encoding="utf-8")
    draft = write_draft(tmp_path / "draft.json")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(curator.os, "replace", fail_replace)

    assert curator.publish(draft, site) == 1
    assert target.read_text(encoding="utf-8") == "previous"
    assert list(site.glob("*.tmp")) == []
