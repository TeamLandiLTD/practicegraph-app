"""Emit building from history, queueing-once, and flush classification
(FR-EMT-1/2/3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import practicegraph.emit as emit_module
from conftest import build_fixture_history
from practicegraph.config import Config
from practicegraph.emit import build_and_queue, flush_queue, queueable_days
from practicegraph.store import Store
from practicegraph.transport import EmitResult
from practicegraph.wire import validate_emit

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _config(tmp_path: Path, configured: bool = True) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        data_dir_source="env",
        api_base_url="not-used-in-tests" if configured else None,
        api_base_url_source="env" if configured else "default",
        org_id="acme-eng" if configured else None,
        org_id_source="env" if configured else "default",
        org_token_present=configured,
        org_token_source="env" if configured else "default",
        config_file_state="absent",
    )


def test_queueable_days_window_and_completion(tmp_path: Path) -> None:
    store, _health = build_fixture_history(tmp_path)
    assert queueable_days(store, NOW) == ["2026-07-02"]
    # The current (incomplete) UTC day is never queueable.
    midday_of_activity = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    assert queueable_days(store, midday_of_activity) == []
    # Activity older than the backfill window never leaves retroactively.
    much_later = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert queueable_days(store, much_later) == []


def test_build_and_queue_exactly_once_with_engagement(tmp_path: Path) -> None:
    store, health = build_fixture_history(tmp_path)
    store.engagement_add("2026-07-02", "report_generated", 2)
    result = build_and_queue(store, _config(tmp_path), NOW, health)
    assert result == {"queued": 1, "invalid": 0}
    raw = store.next_unsent_payload()
    assert raw is not None
    payload = json.loads(raw)
    assert validate_emit(payload) == []
    assert payload["day"] == "2026-07-02"
    assert payload["org_id"] == "acme-eng"
    assert payload["estimated_cost_micro_usd"] == 42005
    assert payload["sessions"] == 4  # incl. the sidechain session (accounting)
    assert payload["tools_observed"] == ["claude_code", "codex"]
    assert payload["engagement"]["report_generated"] == 2
    assert payload["schema_version"] == 2
    assert payload["work_type_sessions"] == {
        "build": 1, "investigate": 1, "converse": 2, "unknown": 0,
    }
    assert payload["maturity"]["cache_reuse"] == "leading"
    # Second build: the day is claimed, nothing new is queued.
    again = build_and_queue(store, _config(tmp_path), NOW, health)
    assert again == {"queued": 0, "invalid": 0}


def test_flush_requires_configuration(tmp_path: Path) -> None:
    store, _health = build_fixture_history(tmp_path)
    result = flush_queue(store, _config(tmp_path, configured=False), NOW, "")
    assert result == {"skipped_unconfigured": 1}


def _queue_one(tmp_path: Path) -> Store:
    store, health = build_fixture_history(tmp_path)
    build_and_queue(store, _config(tmp_path), NOW, health)
    return store


def test_flush_success_marks_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _queue_one(tmp_path)
    monkeypatch.setattr(
        emit_module, "post_emit", lambda *a, **k: EmitResult(ok=True)
    )
    assert flush_queue(store, _config(tmp_path), NOW, "tok-123") == {
        "sent": 1,
        "retry_wait": 0,
        "dead_letter": 0,
    }
    assert store.queue_counts()["sent"] == 1


def test_flush_transport_failure_schedules_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _queue_one(tmp_path)
    monkeypatch.setattr(
        emit_module,
        "post_emit",
        lambda *a, **k: EmitResult(ok=False, error_code="server_unavailable"),
    )
    assert flush_queue(store, _config(tmp_path), NOW, "tok-123")["retry_wait"] == 1
    assert store.queue_error_counts() == {"server_unavailable": 1}
    assert store.due_emits(NOW) == []  # not due again until the backoff elapses


def test_flush_version_rejection_dead_letters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-EMT-3: version rejection is terminal, never retried."""
    store = _queue_one(tmp_path)
    monkeypatch.setattr(
        emit_module,
        "post_emit",
        lambda *a, **k: EmitResult(ok=False, error_code="schema_version_not_supported"),
    )
    assert flush_queue(store, _config(tmp_path), NOW, "tok-123")["dead_letter"] == 1
    assert store.queue_counts()["dead_letter"] == 1
    assert store.queue_error_counts() == {"schema_version_not_supported": 1}
