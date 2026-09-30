"""Learning choices use declared preferences and evidence, not inferred skill scores."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from practicegraph import catalog
from practicegraph.analysis import training
from practicegraph.analysis.training_catalog import SCHEMA, parse_training
from practicegraph.config import resolve
from practicegraph.store import Store

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


def edition():
    return {
        "schema": SCHEMA,
        "training_version": "synthetic-2026-09-06",
        "published_on": "2026-09-06",
        "entries": [
            {
                "id": "synthetic-verification",
                "revision": 1,
                "title": "Synthetic verification lab",
                "provider": "Synthetic provider",
                "url": "https://example.org/verification",
                "goal": "verify_results",
                "tools": ["any"],
                "kind": "lab",
                "credential": "none",
                "summary": "A synthetic learning fixture.",
                "why": "You chose to learn how to verify AI-generated work.",
                "prerequisites": "A synthetic project with a test command.",
                "duration_minutes": 45,
                "duration_basis": "editorial_estimate",
                "cost": "free",
                "cost_note": "Synthetic free exercise; no exam or subscription.",
                "steps": [
                    {
                        "title": "Practice a check",
                        "url": "https://example.org/verification",
                        "outcome": "Explain a check and its limitations.",
                    }
                ],
                "sources": ["https://example.org/verification"],
                "reviewed_on": "2026-09-06",
                "status": "active",
                "successor": None,
                "limitations": "Not a real course recommendation.",
            }
        ],
    }


def publish(folder, document=None):
    catalog._accept_editorial(
        folder, "training", document or edition(), "https://example.org/training.json", NOW
    )


@pytest.fixture()
def store(tmp_path):
    result = Store.in_data_dir(tmp_path)
    result.migrate()
    publish(tmp_path)
    return result


def preferences(store, folder, **kwargs):
    training.change(
        store,
        folder,
        {"action": "preferences", **training.DEFAULT_PREFS, "goal": "verify_results", **kwargs},
        NOW,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("url", "javascript:alert(1)"),
        ("sources", ["https://user:pass@example.org/x"]),
        ("duration_minutes", True),
        ("duration_minutes", -1),
        ("duration_minutes", None),
        ("goal", "low_intelligence"),
        ("credential", "professional_certification"),
        ("reviewed_on", "2026-09-07"),
        ("tools", ["any", "codex"]),
        ("extra", "hidden"),
    ],
)
def test_contract_refuses_unsafe_and_misleading_records(field, value):
    raw = edition()
    raw["entries"][0][field] = value
    assert parse_training(raw) is None


def test_choices_require_a_goal_and_never_read_activity(store, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Training must not infer ability from private work logs")

    monkeypatch.setattr(store, "setup_session_context", forbidden)
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    preferences(store, tmp_path)
    result = training.reading(store, tmp_path, NOW)
    assert result["suggestion"]["id"] == "synthetic-verification"
    assert "score" not in result


def test_cost_time_tool_and_certification_filters_are_explicit(store, tmp_path):
    raw = edition()
    entry = raw["entries"][0]
    entry.update(
        cost="unknown", duration_minutes=None, duration_basis="unknown", tools=["claude_code"]
    )
    publish(tmp_path, raw)
    preferences(store, tmp_path)
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    preferences(store, tmp_path, free_only=False, max_minutes=60)
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    preferences(store, tmp_path, free_only=False, tool="codex")
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    preferences(store, tmp_path, free_only=False, tool="claude_code")
    assert training.reading(store, tmp_path, NOW)["suggestion"] is not None
    entry.update(
        kind="certification", credential="professional_certification", goal="certification"
    )
    publish(tmp_path, raw)
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    preferences(store, tmp_path, free_only=False, goal="certification")
    assert training.reading(store, tmp_path, NOW)["suggestion"] is not None


def test_old_entry_cannot_be_refreshed_by_new_edition_date(store, tmp_path):
    raw = edition()
    raw["entries"][0]["reviewed_on"] = "2026-07-01"
    publish(tmp_path, raw)
    preferences(store, tmp_path)
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    publish(tmp_path)
    assert training.reading(store, tmp_path, NOW + timedelta(days=31))["suggestion"] is None


def test_empty_states_distinguish_missing_coverage_filters_and_recorded_choices(store, tmp_path):
    assert training.reading(store, tmp_path, NOW)["empty_reason"] == "choose_goal"
    preferences(store, tmp_path, goal="certification")
    assert training.reading(store, tmp_path, NOW)["empty_reason"] == "no_coverage"
    raw = edition()
    raw["entries"][0].update(cost="unknown", tools=["claude_code"])
    publish(tmp_path, raw)
    preferences(store, tmp_path)
    assert training.reading(store, tmp_path, NOW)["empty_reason"] == "filters"
    preferences(store, tmp_path, free_only=False, tool="codex")
    assert training.reading(store, tmp_path, NOW)["empty_reason"] == "filters"
    preferences(store, tmp_path, free_only=False)
    assert training.reading(store, tmp_path, NOW)["empty_reason"] is None
    training.change(
        store,
        tmp_path,
        {"action": "save", "id": "synthetic-verification", "edition": raw["training_version"]},
        NOW,
    )
    assert training.reading(store, tmp_path, NOW)["empty_reason"] is None
    training.change(
        store,
        tmp_path,
        {"action": "progress", "id": "synthetic-verification", "state": "dismissed"},
        NOW,
    )
    assert training.reading(store, tmp_path, NOW)["empty_reason"] == "already_recorded"
    assert (
        training.reading(store, tmp_path, NOW + timedelta(days=31))["empty_reason"]
        == "catalog_unavailable"
    )
    raw["entries"][0]["status"] = "retired"
    publish(tmp_path, raw)
    assert training.reading(store, tmp_path, NOW)["empty_reason"] == "no_coverage"


def test_returning_to_set_aside_choice_preserves_snapshot_even_when_retired(store, tmp_path):
    preferences(store, tmp_path)
    entry_id = "synthetic-verification"
    training.change(
        store,
        tmp_path,
        {"action": "save", "id": entry_id, "edition": edition()["training_version"]},
        NOW,
    )
    training.change(
        store, tmp_path, {"action": "progress", "id": entry_id, "state": "dismissed"}, NOW
    )
    before = training.export_history(store)["records"][0]
    raw = edition()
    raw["entries"][0].update(status="retired", revision=2, title="Updated synthetic lab")
    publish(tmp_path, raw)
    training.change(store, tmp_path, {"action": "resume", "id": entry_id}, NOW + timedelta(days=1))
    result = training.reading(Store.in_data_dir(tmp_path), tmp_path, NOW + timedelta(days=1))[
        "records"
    ][0]
    assert result["state"] == "saved"
    assert result["entry"] == before["entry"]
    assert result["edition"] == before["edition"]
    assert result["updated_on"] == "2026-09-07"
    assert result["availability"] == "unavailable"


def test_resume_and_save_compete_for_one_active_step(store, tmp_path):
    raw = edition()
    second = copy.deepcopy(raw["entries"][0])
    second["id"] = "synthetic-second"
    raw["entries"].append(second)
    publish(tmp_path, raw)
    preferences(store, tmp_path)
    training.change(
        store,
        tmp_path,
        {"action": "save", "id": "synthetic-verification", "edition": raw["training_version"]},
        NOW,
    )
    training.change(
        store,
        tmp_path,
        {"action": "progress", "id": "synthetic-verification", "state": "dismissed"},
        NOW,
    )
    actions = [
        {"action": "resume", "id": "synthetic-verification"},
        {"action": "save", "id": second["id"], "edition": raw["training_version"]},
    ]

    def choose(body):
        try:
            training.change(store, tmp_path, body, NOW)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(choose, actions)) == 1
    records = training.export_history(store)["records"]
    assert sum(r["state"] == "saved" for r in records) == 1


def test_resume_refuses_unknown_and_completed_records(store, tmp_path):
    entry_id = "synthetic-verification"
    with pytest.raises(ValueError, match="invalid_training_progress"):
        training.change(store, tmp_path, {"action": "resume", "id": entry_id}, NOW)
    preferences(store, tmp_path)
    training.change(
        store,
        tmp_path,
        {"action": "save", "id": entry_id, "edition": edition()["training_version"]},
        NOW,
    )
    for state in ("in_progress", "completed"):
        training.change(
            store, tmp_path, {"action": "progress", "id": entry_id, "state": state}, NOW
        )
    with pytest.raises(ValueError, match="invalid_training_progress"):
        training.change(store, tmp_path, {"action": "resume", "id": entry_id}, NOW)


def test_private_journey_persists_and_does_not_credit_hours(store, tmp_path):
    preferences(store, tmp_path)
    record_id = "synthetic-verification"
    training.change(
        store,
        tmp_path,
        {"action": "save", "id": record_id, "edition": edition()["training_version"]},
        NOW,
    )
    assert training.reading(store, tmp_path, NOW)["suggestion"] is None
    with pytest.raises(ValueError):
        training.change(
            store, tmp_path, {"action": "progress", "id": record_id, "state": "completed"}, NOW
        )
    for state in ("in_progress", "completed"):
        training.change(
            store, tmp_path, {"action": "progress", "id": record_id, "state": state}, NOW
        )
    fresh = Store.in_data_dir(tmp_path)
    assert training.reading(fresh, tmp_path, NOW)["records"][0]["state"] == "completed"
    assert training.export_history(fresh)["records"][0]["entry"]["id"] == record_id
    with fresh.local_transaction() as conn:
        assert conn.execute("SELECT count(*) FROM practice_time_entries").fetchone()[0] == 0
    with pytest.raises(ValueError):
        training.change(fresh, tmp_path, {"action": "clear"}, NOW)
    training.change(fresh, tmp_path, {"action": "clear", "confirm": True}, NOW)
    assert training.reading(fresh, tmp_path, NOW)["records"] == []


def test_only_one_item_can_be_selected_across_windows(store, tmp_path):
    preferences(store, tmp_path)
    body = {
        "action": "save",
        "id": "synthetic-verification",
        "edition": edition()["training_version"],
    }

    def choose(_):
        try:
            training.change(store, tmp_path, body, NOW)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(choose, range(4))) == 1
    assert len(training.export_history(store)["records"]) == 1


def test_saved_snapshot_survives_retirement_and_outage(store, tmp_path):
    preferences(store, tmp_path)
    training.change(
        store,
        tmp_path,
        {
            "action": "save",
            "id": "synthetic-verification",
            "edition": edition()["training_version"],
        },
        NOW,
    )
    raw = edition()
    raw["entries"][0].update(status="retired", revision=2)
    publish(tmp_path, raw)
    result = training.reading(store, tmp_path, NOW)
    assert result["records"][0]["entry"]["status"] == "active"
    assert result["records"][0]["availability"] == "unavailable"
    (tmp_path / "catalog" / "training.json").unlink()
    assert training.reading(store, tmp_path, NOW)["records"]


def test_training_pull_preserves_cache_and_enterprise_boundary(store, tmp_path, monkeypatch):
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    monkeypatch.setattr(catalog, "_get_json", lambda *a, **kw: "timeout")
    assert catalog.pull_public_training(store, config, NOW) == "timeout"
    assert catalog.load_training(tmp_path) is not None
    private = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_API_BASE_URL": "https://enterprise.example.org",
        }
    )
    assert catalog.pull_public_training(store, private, NOW) == "skipped_enterprise_configured"
    custom = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_CONTENT_BASE_URL": "https://custom.example.org/feeds",
        }
    )
    assert custom.training_source_url == "https://custom.example.org/feeds/training.json"
