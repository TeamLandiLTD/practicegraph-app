"""A verified newer release is announced once, as a toast, outside quiet hours."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from practicegraph import update_notifications as un
from practicegraph.analysis.update import UpdateOffer
from practicegraph.store import Store

NOW = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
OFFER = UpdateOffer(
    version="0.2.11", published="2026-09-16",
    url=(
        "https://github.com/TeamLandiLTD/practicegraph-app/releases/download/"
        "v0.2.11/PracticeGraph-0.2.11.msi"
    ),
    sha256="0" * 64, size_bytes=15_000_000,
    notes_url=(
        "https://github.com/TeamLandiLTD/practicegraph-app/releases/tag/v0.2.11"
    ),
)


def _store(tmp_path):
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def test_nothing_newer_means_no_toast(tmp_path, monkeypatch):
    monkeypatch.setattr(un, "load_update_offer", lambda data_dir, current: None)
    calls: list[tuple[str, str, str | None]] = []
    def deliver(t, b, launch):
        calls.append((t, b, launch))
        return "delivered"

    result = un.evaluate_update(_store(tmp_path), tmp_path, NOW, "0.2.10", deliver=deliver)
    assert result == "skipped_current"
    assert calls == []


def test_a_verified_newer_release_is_announced_once(tmp_path, monkeypatch):
    monkeypatch.setattr(un, "load_update_offer", lambda data_dir, current: OFFER)
    monkeypatch.setattr(un, "quiet_now", lambda data_dir, now: False)
    store = _store(tmp_path)
    calls: list[tuple[str, str, str | None]] = []
    def deliver(t, b, launch):
        calls.append((t, b, launch))
        return "delivered"
    assert un.evaluate_update(store, tmp_path, NOW, "0.2.10", deliver=deliver) == "delivered"
    assert len(calls) == 1
    title, body, launch = calls[0]
    assert "0.2.11" in title and "0.2.10" in body and launch == "update"
    again = un.evaluate_update(store, tmp_path, NOW, "0.2.10", deliver=deliver)
    assert again == "skipped_already_notified"
    assert len(calls) == 1


def test_quiet_hours_hold_the_announcement(tmp_path, monkeypatch):
    monkeypatch.setattr(un, "load_update_offer", lambda data_dir, current: OFFER)
    monkeypatch.setattr(un, "quiet_now", lambda data_dir, now: True)
    calls: list[object] = []
    result = un.evaluate_update(_store(tmp_path), tmp_path, NOW, "0.2.10",
                                deliver=lambda *a: calls.append(a) or "delivered")
    assert result == "skipped_quiet"
    assert calls == []


def test_a_failed_delivery_is_retried_on_a_later_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(un, "load_update_offer", lambda data_dir, current: OFFER)
    monkeypatch.setattr(un, "quiet_now", lambda data_dir, now: False)
    store = _store(tmp_path)
    failed = un.evaluate_update(store, tmp_path, NOW, "0.2.10", deliver=lambda *a: "unavailable")
    assert failed == "unavailable"
    retried = un.evaluate_update(store, tmp_path, NOW, "0.2.10", deliver=lambda *a: "delivered")
    assert retried == "delivered"


def test_a_newer_version_after_an_announced_one_is_announced_again(tmp_path, monkeypatch):
    monkeypatch.setattr(un, "quiet_now", lambda data_dir, now: False)
    store = _store(tmp_path)
    monkeypatch.setattr(un, "load_update_offer", lambda data_dir, current: OFFER)
    first = un.evaluate_update(store, tmp_path, NOW, "0.2.10", deliver=lambda *a: "delivered")
    assert first == "delivered"
    later = replace(OFFER, version="0.2.12")
    monkeypatch.setattr(un, "load_update_offer", lambda data_dir, current: later)
    second = un.evaluate_update(store, tmp_path, NOW, "0.2.10", deliver=lambda *a: "delivered")
    assert second == "delivered"
