"""Agent scheduler tick: category isolation and consent gating (FR-ALR-1,
NFR-REL-1/2, FR-CNS-1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import practicegraph.agent as agent_module
from conftest import FIXTURE_ENV
from practicegraph.agent import (
    _TICK_CATEGORIES,
    OUTCOME_SKIPPED_NOT_INITIALIZED,
    initialize,
    run_tick,
)
from practicegraph.store import Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path / "data")
    return env


def _consent_on(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "consent.json").write_text(
        json.dumps({"emission_enabled": True, "decided_at": "2026-07-01T08:00:00+00:00"}),
        encoding="utf-8",
    )


def test_initialize_is_idempotent(tmp_path: Path) -> None:
    env = _env(tmp_path)
    data_dir = initialize(env)
    first_stamp = Store.in_data_dir(data_dir).meta_get("initialized_at")
    assert data_dir.is_dir()
    assert (data_dir / "reports").is_dir()
    initialize(env)
    assert Store.in_data_dir(data_dir).meta_get("initialized_at") == first_stamp


def test_tick_without_init_skips_everything(tmp_path: Path) -> None:
    """A USER-context tick never creates state from nothing — that stays
    `init`'s job (the service bootstraps itself; see the test below)."""
    outcomes = run_tick(_env(tmp_path), now=NOW)
    assert set(outcomes.values()) == {"skipped_not_initialized"}


def test_tick_consent_off_stays_local(tmp_path: Path) -> None:
    env = _env(tmp_path)
    initialize(env)
    outcomes = run_tick(env, now=NOW)
    assert outcomes["collect"] == "ok"
    assert outcomes["report_artifact"] == "ok"
    assert outcomes["emit_build"] == "skipped_consent_off"
    assert outcomes["emit_flush"] == "skipped_consent_off"
    assert outcomes["catalog_pull"] == "skipped_unconfigured"  # no endpoint set
    assert "models_pull" in outcomes
    artifact = tmp_path / "data" / "reports" / "daily-2026-07-03.html"
    assert artifact.is_file()
    store = Store.in_data_dir(tmp_path / "data")
    assert store.engagement_for_day("2026-07-03") == {"report_generated": 1}
    assert store.queue_counts()["pending"] == 0  # nothing queued while off


def test_tick_consent_on_queues_but_flush_unconfigured(tmp_path: Path) -> None:
    env = _env(tmp_path)
    initialize(env)
    _consent_on(tmp_path)
    outcomes = run_tick(env, now=NOW)
    assert outcomes["emit_build"] == "ok"
    assert outcomes["emit_flush"] == "skipped_unconfigured"
    store = Store.in_data_dir(tmp_path / "data")
    assert store.queue_counts()["pending"] == 1  # 2026-07-02 queued
    assert store.meta_get("last_tick_at") is not None


def test_tick_writes_closed_status_file(tmp_path: Path) -> None:
    """The tray reads status.json read-only (C-4): closed codes, counts, and a
    relative filename only — no paths, no content."""
    from practicegraph.privacy import leak_findings

    env = _env(tmp_path)
    initialize(env)
    run_tick(env, now=NOW)
    status_path = tmp_path / "data" / "status.json"
    text = status_path.read_text(encoding="utf-8")
    document = json.loads(text)
    assert document["schema"] == "practicegraph.status/1"
    assert document["latest_report"] == "daily-2026-07-03.html"
    assert document["consent_enabled"] is False
    assert document["outcomes"]["report_artifact"] == "ok"
    assert document["queue_counts"]["pending"] == 0
    assert leak_findings(text) == []


def test_tick_categories_are_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failing category never breaks the cycle (FR-ALR-1)."""
    env = _env(tmp_path)
    initialize(env)
    _consent_on(tmp_path)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(agent_module, "_write_report_artifact", boom)
    outcomes = run_tick(env, now=NOW)
    assert outcomes["report_artifact"] == "error"
    assert outcomes["collect"] == "ok"
    assert outcomes["emit_build"] == "ok"  # the cycle survived


def test_tick_self_initializes_a_missing_store(tmp_path: Path) -> None:
    """The MSI is declarative (C-3), so nothing in the install path can run
    `init` — the service must create its own state on first tick. Field
    report 2026-07-20: a clean install sat inert with state_db
    not_initialized and no endpoint, because every tick short-circuited."""
    env = {
        **FIXTURE_ENV,
        "PRACTICEGRAPH_DATA_DIR": str(tmp_path / "fresh"),
        "PRACTICEGRAPH_SCAN_PROFILES": "1",  # the service context
    }
    store = Store.in_data_dir(Path(env["PRACTICEGRAPH_DATA_DIR"]))
    assert not store.exists()

    outcomes = run_tick(env)

    assert store.exists(), "first tick must create the state store"
    assert outcomes["collect"] != OUTCOME_SKIPPED_NOT_INITIALIZED
    assert set(outcomes) == set(_TICK_CATEGORIES)
    # Idempotent: a second tick reuses the store it just made.
    assert run_tick(env)["collect"] != OUTCOME_SKIPPED_NOT_INITIALIZED
