from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from practicegraph import news_notifications as news
from practicegraph.analysis.news import news_item_to_entry, parse_news_artifact
from practicegraph.store import Store

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def edition(urgency: str = "urgent", count: int = 1) -> dict:
    return {
        "news_version": "news-2026-09-11-1",
        "items": [
            {
                "id": f"news-{i}",
                "kind": "update",
                "title": "A tool update needs review",
                "hook": "Review the affected version before your next deployment.",
                "summary": "The maintainer describes a change to the supported configuration.",
                "why": "Teams using this version should review the migration steps.",
                "source": "Maintainer",
                "url": "https://example.org/update",
                "attention": {
                    "urgency": urgency,
                    "reason": "The old endpoint closes tomorrow.",
                    "starts_at": "2026-09-11T09:00:00Z",
                    "expires_at": "2026-09-13T09:00:00Z",
                },
            }
            for i in range(count)
        ],
    }


def write_news(path: Path, doc: dict) -> None:
    (path / "catalog").mkdir(exist_ok=True)
    (path / "catalog/news.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture()
def state(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.db")
    store.migrate()
    monkeypatch.setattr(news, "quiet_now", lambda *_: False)
    write_news(tmp_path, edition())
    return store, tmp_path


def test_attention_roundtrip_and_legacy_defaults():
    parsed = parse_news_artifact(edition())
    assert parsed and news_item_to_entry(parsed[0]) == edition()["items"][0]
    legacy = edition()
    del legacy["items"][0]["attention"]
    parsed = parse_news_artifact(legacy)
    assert parsed and parsed[0].attention is None and not news.active(parsed[0], NOW)


@pytest.mark.parametrize(
    "field,value",
    [
        ("urgency", "breaking"),
        ("urgency", True),
        ("reason", ""),
        ("starts_at", "2026-09-11T09:00:00"),
        ("expires_at", "2026-09-11T09:00:00Z"),
        ("expires_at", "2026-10-13T09:00:00Z"),
        ("starts_at", "not-a-date"),
        ("reason", "x" * 281),
        ("execute", "open something"),
    ],
)
def test_attention_rejects_invalid_or_unbounded_metadata(field, value):
    doc = edition()
    doc["items"][0]["attention"][field] = value
    assert parse_news_artifact(doc) is None


def test_dedupe_survives_restart_and_copy_edits_but_allows_escalation(state):
    store, path = state
    doc = edition("important")
    write_news(path, doc)
    calls = []

    def deliver(item, popup):
        return calls.append((item.news_id, popup)) or "delivered"

    assert news.evaluate_news(store, path, NOW, deliver) == "ok"
    assert calls == [("news-0", False)]
    doc["news_version"] = "news-2026-09-11-2"
    doc["items"][0]["title"] = "A clearer title for the same update"
    write_news(path, doc)
    restarted = Store(store.path)
    assert news.evaluate_news(restarted, path, NOW + timedelta(hours=2), deliver) == "skipped_quiet"
    doc["items"][0]["attention"]["urgency"] = "urgent"
    write_news(path, doc)
    assert news.evaluate_news(restarted, path, NOW + timedelta(hours=2), deliver) == "ok"
    assert calls[-1] == ("news-0", True)


def test_quiet_snooze_and_read_do_not_consume_delivery(state, monkeypatch):
    store, path = state
    calls = []

    def deliver(*args):
        return calls.append(args) or "delivered"

    monkeypatch.setattr(news, "quiet_now", lambda *_: True)
    assert news.evaluate_news(store, path, NOW, deliver) == "skipped_quiet"
    assert store.meta_get(news.STATE_KEY) is None
    monkeypatch.setattr(news, "quiet_now", lambda *_: False)
    news.change(store, path, {"action": "snooze", "minutes": 60}, NOW)
    assert news.evaluate_news(store, path, NOW, deliver) == "skipped_quiet"
    news.change(store, path, {"action": "snooze", "minutes": 0}, NOW)
    news.change(store, path, {"action": "read", "id": "news-0"}, NOW)
    assert news.reading(store, path, NOW)["unread_count"] == 0
    assert news.evaluate_news(store, path, NOW, deliver) == "skipped_quiet"
    assert calls == []


def test_expired_and_future_items_stay_readable_without_alerts(state):
    store, path = state
    for when in (NOW - timedelta(days=1), NOW + timedelta(days=3)):
        assert (
            news.evaluate_news(store, path, when, lambda *_: pytest.fail("unexpected alert"))
            == "skipped_quiet"
        )
        assert len(news.reading(store, path, when)["items"]) == 1
        assert news.reading(store, path, when)["items"][0]["attention_active"] is False


def test_rolling_cap_spacing_and_failed_delivery_retry(state):
    store, path = state
    write_news(path, edition(count=4))
    calls = []

    def failed(*_):
        return calls.append("failed") or "unavailable"

    assert news.evaluate_news(store, path, NOW, failed) == "error"
    assert news.evaluate_news(store, path, NOW + timedelta(minutes=1), failed) == "skipped_quiet"

    def delivered(*_):
        return calls.append("ok") or "delivered"

    assert news.evaluate_news(store, path, NOW + timedelta(minutes=15), delivered) == "ok"
    assert (
        news.evaluate_news(store, path, NOW + timedelta(minutes=20), delivered) == "skipped_quiet"
    )
    assert news.evaluate_news(store, path, NOW + timedelta(hours=2), delivered) == "ok"
    assert news.evaluate_news(store, path, NOW + timedelta(hours=3), delivered) == "ok"
    assert news.evaluate_news(store, path, NOW + timedelta(hours=4), delivered) == "skipped_quiet"
    assert calls == ["failed", "ok", "ok", "ok"]


def test_preferences_and_actions_are_closed(state):
    store, path = state
    for body in (
        {"action": "settings", "mode": "all", "urgent_popup": True},
        {"action": "settings", "mode": "off", "urgent_popup": 1},
        {"action": "snooze", "minutes": True},
        {"action": "read", "id": "missing"},
        {"action": "read", "id": "news-0", "command": "bad"},
    ):
        with pytest.raises(ValueError):
            news.change(store, path, body, NOW)
    news.change(store, path, {"action": "settings", "mode": "off", "urgent_popup": False}, NOW)
    assert (
        news.evaluate_news(store, path, NOW, lambda *_: pytest.fail("disabled")) == "skipped_quiet"
    )
    news.change(store, path, {"action": "settings", "mode": "urgent", "urgent_popup": False}, NOW)
    calls = []
    assert (
        news.evaluate_news(store, path, NOW, lambda _, popup: calls.append(popup) or "delivered")
        == "ok"
    )
    assert calls == [False]


def test_confirmed_quiet_hours_use_the_selected_timezone(tmp_path, monkeypatch):
    monkeypatch.setattr(
        news,
        "read_schedule_profile",
        lambda _: SimpleNamespace(
            confirmed=True, zone=ZoneInfo("Europe/Sofia"), quiet_start="22:00", quiet_end="07:00"
        ),
    )
    assert news.quiet_now(tmp_path, datetime(2026, 9, 11, 20, tzinfo=UTC))
    assert not news.quiet_now(tmp_path, NOW)


def test_routine_news_gets_unread_count_without_notification(state):
    store, path = state
    doc = copy.deepcopy(edition())
    del doc["items"][0]["attention"]
    write_news(path, doc)
    assert news.reading(store, path, NOW)["unread_count"] == 1
    assert (
        news.evaluate_news(store, path, NOW, lambda *_: pytest.fail("routine")) == "skipped_quiet"
    )
