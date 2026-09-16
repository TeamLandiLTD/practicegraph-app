"""Setup guidance: bounded inspection, project/context isolation, durable private outcomes."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from tools.content_release import inventory, prepare

from conftest import sealed
from practicegraph import catalog
from practicegraph.analysis import setup_improvements as si
from practicegraph.analysis.harness_playbooks import SCHEMA, compatible, parse_playbooks
from practicegraph.analysis.setup_inspection import MAX_FILE_BYTES, inspect_project
from practicegraph.config import resolve
from practicegraph.events import TokenCounts, Tool, TurnEvent, TurnKind
from practicegraph.history import (
    _contributions_from_events,
    _marks_from_events,
    _session_totals_from_events,
    _work_spans_from_events,
)
from practicegraph.store import Store

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
VERSIONS = {"codex": "1.2.3", "claude_code": "2.1.0"}


def edition():
    return {
        "schema": SCHEMA,
        "playbooks_version": "synthetic-2026-09-05",
        "published_on": "2026-09-05",
        "entries": [
            {
                "id": "synthetic-verification",
                "revision": 1,
                "recipe": "verification_setup",
                "detector": "verification_gap",
                "stacks": ["python", "javascript"],
                "harnesses": [
                    {"tool": tool, "min_version": None, "max_version": None}
                    for tool in ("codex", "claude_code")
                ],
                "title": "Establish a repeatable verification check",
                "explanation": "Synthetic guidance for tests, not an official editorial edition.",
                "guidance": "Find the project's intended check and document how to repeat it.",
                "limitations": "Observed attempts do not establish passing tests.",
                "sources": ["https://example.com/synthetic-reference"],
                "reviewed_on": "2026-09-05",
                "status": "active",
                "successor": None,
                "min_client_recipe_version": 1,
            }
        ],
    }


def publish(folder, value=None, now=NOW):
    catalog._accept_editorial(
        folder,
        "harness-playbooks",
        value or edition(),
        "https://example.com/harness-playbooks.json",
        now,
    )


@pytest.fixture()
def project(tmp_path):
    path = tmp_path / "selected-project"
    path.mkdir()
    (path / "package.json").write_text(json.dumps({"scripts": {"test": "secret-command"}}))
    return path


@pytest.fixture()
def store(tmp_path):
    result = Store(tmp_path / "state.db")
    result.migrate()
    publish(tmp_path)
    return result


def seed(
    store, project, hours=-2, tests=0, model="synthetic-model", branch="main", tool=Tool.CODEX
):
    stamp = NOW + timedelta(hours=hours)
    key = f"synthetic-{hours}-{model}-{project.name}-{tool.value}"
    events = tuple(
        TurnEvent(
            timestamp=stamp + timedelta(minutes=n),
            session_id=key,
            source_id=tool.value,
            tool=tool,
            kind=TurnKind.ASSISTANT_TURN,
            model=model,
            tokens=TokenCounts(input=10),
            cwd_hash=hashlib.sha256(str(project).encode()).hexdigest()[:16],
            branch_hash=hashlib.sha256(branch.encode()).hexdigest()[:16],
            files_edited=1 if n == 0 else 0,
            test_run_attempts=tests if n == 4 else 0,
        )
        for n in range(5)
    )
    store.replace_file_data(
        source_id=tool.value,
        path_hash=key,
        cursor=(1, 1, 1),
        contributions=_contributions_from_events(events),
        marks=_marks_from_events(events),
        health=None,
        turn_keys=(),
        now=stamp,
        work_spans=_work_spans_from_events(events),
        session_totals=_session_totals_from_events(events),
    )


def change(store, tmp_path, action, now=NOW, versions=None, **kwargs):
    return si.change(store, tmp_path, {"action": action, **kwargs}, now, versions or VERSIONS)


def start(store, tmp_path, project, key="a" * 32):
    change(
        store,
        tmp_path,
        "inspect",
        id=key,
        path=str(project),
        tool="codex",
        label="Selected project",
    )
    return key


def read(store, tmp_path, now=NOW, versions=None):
    return si.reading(store, tmp_path, now, versions or VERSIONS)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: e["entries"][0].update(command="rm -rf"),
        lambda e: e["entries"][0].update(recipe="execute_remote_code"),
        lambda e: e["entries"][0].update(detector="anything"),
        lambda e: e["entries"][0].update(sources=["javascript:alert(1)"]),
        lambda e: e["entries"][0].update(stacks=[{}]),
        lambda e: e["entries"].append(copy.deepcopy(e["entries"][0])),
        lambda e: e["entries"][0].update(reviewed_on="2026-09-06"),
        lambda e: e.update(published_on="not-a-date"),
    ],
)
def test_catalog_rejects_unknown_execution_fields_and_malformed_values(mutation):
    document = edition()
    assert parse_playbooks(document)
    mutation(document)
    assert parse_playbooks(document) is None


def test_version_compatibility_is_explicit_and_prerelease_ambiguity_is_unknown():
    entry = edition()["entries"][0]
    assert compatible(entry, ("codex",), {})
    entry["harnesses"][0].update(min_version="1.0.0", max_version="1.3.0")
    assert compatible(entry, ("codex",), VERSIONS)
    assert not compatible(entry, ("codex",), {"codex": "1.2.3-beta"})
    assert not compatible(entry, ("codex",), {})
    entry["min_client_recipe_version"] = 2
    assert not compatible(entry, ("codex",), VERSIONS)


def test_inspection_does_not_persist_contents_paths_or_run_commands(project):
    (project / ".env").write_text("SECRET-MARKER-private")
    (project / "README.md").write_text("SECRET-MARKER-private; npm test")
    result = inspect_project(str(project))
    assert result["declared_checks"] == ["test"]
    assert result["verification_mentioned"]
    encoded = json.dumps(result)
    assert "SECRET-MARKER" not in encoded and "secret-command" not in encoded
    assert str(project) not in encoded and ".env" not in encoded


def test_inspection_handles_invalid_large_and_unsupported_files(project):
    (project / "package.json").write_text("{")
    (project / "pyproject.toml").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    result = inspect_project(str(project))
    assert result["stacks"] == []
    assert [f["state"] for f in result["files"][:2]] == ["invalid", "too_large"]
    with pytest.raises(ValueError, match="invalid_project"):
        inspect_project("relative/project")
    with pytest.raises(ValueError, match="invalid_project"):
        inspect_project("\\\\server\\share")


def test_inspection_refuses_file_symlink_escape(project, tmp_path):
    target = tmp_path / "private.json"
    target.write_text('{"scripts":{"test":"secret"}}')
    (project / "package.json").unlink()
    try:
        (project / "package.json").symlink_to(target)
    except OSError:
        pytest.skip("This Windows account cannot create file symlinks")
    result = inspect_project(str(project))
    assert result["files"][0]["state"] == "outside_scope"
    assert result["declared_checks"] == []


def test_inspect_prepare_handoff_attempt_and_private_followup(store, tmp_path, project):
    for hour in (-6, -4, -2):
        seed(store, project, hour)
    key = start(store, tmp_path, project)
    inspected = read(store, tmp_path)["active"]
    assert len(inspected["baseline"]) == 3 and inspected["brief"] is None
    change(store, tmp_path, "prepare", id=key)
    brief = change(store, tmp_path, "handoff", id=key)["brief"]
    assert "0 of 3" in brief and "secret-command" not in brief
    assert read(store, tmp_path)["active"]["state"] == "prepared"
    change(store, tmp_path, "attempt", id=key)
    # Early self-report must not prevent the later observational check.
    change(store, tmp_path, "review", id=key, feedback="helpful")
    other = tmp_path / "other-project"
    for hour in (2, 4, 6):
        seed(store, project, hour, tests=1)
        seed(store, other, hour, tests=0)
    result = read(store, tmp_path, NOW + timedelta(hours=8))
    comparison = result["history"][0]["comparison"]
    assert result["active"] is None
    assert comparison == {
        "state": "observed",
        "before_count": 3,
        "after_count": 3,
        "before_verified": 0,
        "after_verified": 3,
    }
    restored_store = Store(tmp_path / "restored.db")
    restored_store.migrate()
    backup = si.export_history(store)
    for _ in range(2):
        change(restored_store, tmp_path, "restore", NOW + timedelta(hours=8), backup=backup)
    assert si.export_history(restored_store) == backup


def test_context_and_branch_changes_do_not_claim_improvement(store, tmp_path, project):
    seed(store, project)
    key = start(store, tmp_path, project)
    change(store, tmp_path, "prepare", id=key)
    change(store, tmp_path, "attempt", id=key)
    seed(store, project, 2, tests=1, model="different-model")
    seed(store, project, 4, tests=1, branch="different-branch")
    reading = read(store, tmp_path, NOW + timedelta(hours=6))
    assert reading["active"]["comparison"]["after_count"] == 0
    assert (
        read(store, tmp_path, versions={"codex": "2.0.0"})["active"]["comparison"]["state"]
        == "context_changed"
    )


def test_missing_project_history_is_unknown_not_a_zero_success_rate(store, tmp_path, project):
    start(store, tmp_path, project)
    assert read(store, tmp_path)["active"]["baseline"] == []
    change(store, tmp_path, "prepare", id="a" * 32)
    assert "No matching edited work episodes" in read(store, tmp_path)["active"]["brief"]


def test_concurrent_or_retried_actions_do_not_duplicate_records(store, tmp_path, project):
    key = start(store, tmp_path, project)
    start(store, tmp_path, project)
    with pytest.raises(ValueError, match="finish_improvement_first"):
        start(store, tmp_path, project, "b" * 32)
    for _ in range(2):
        change(store, tmp_path, "prepare", id=key)
        change(store, tmp_path, "handoff", id=key)
    assert len(si.export_history(store)["records"]) == 1
    with pytest.raises(ValueError, match="mark_attempt_first"):
        change(store, tmp_path, "review", id=key, feedback="helpful")


@pytest.mark.parametrize("change_kind", ["withdrawn", "changed", "stale", "missing"])
def test_catalog_changes_block_new_handoffs_but_preserve_frozen_history(
    store, tmp_path, project, change_kind
):
    key = start(store, tmp_path, project)
    change(store, tmp_path, "prepare", id=key)
    original = si.export_history(store)
    document = edition()
    now = NOW
    if change_kind == "withdrawn":
        document["entries"][0]["status"] = "withdrawn"
    elif change_kind == "changed":
        document["entries"][0]["revision"] = 2
    elif change_kind == "stale":
        now += timedelta(days=31)
    else:
        document["entries"] = []
    publish(tmp_path, document)
    with pytest.raises(ValueError):
        change(store, tmp_path, "handoff", now, id=key)
    assert si.export_history(store) == original


def test_restore_is_atomic_on_conflict_or_extra_private_fields(store, tmp_path, project):
    start(store, tmp_path, project)
    original = si.export_history(store)
    changed = copy.deepcopy(original)
    changed["records"][0]["raw_path"] = "SECRET-MARKER"
    with pytest.raises(ValueError, match="invalid_backup"):
        change(store, tmp_path, "restore", backup=changed)
    changed = copy.deepcopy(original)
    changed["records"][0]["inspection"]["pytest_config"] = True
    with pytest.raises(ValueError, match="record_conflict"):
        change(store, tmp_path, "restore", backup=changed)
    assert si.export_history(store) == original


def test_journal_is_private_and_clear_does_not_clear_other_history(store, tmp_path, project):
    from practicegraph.emit import build_payload_for_day

    args = dict(
        store=store,
        day="2026-09-05",
        org_id="synthetic",
        engagement={},
        emit_id="test",
        platform="windows",
        source_health=[],
    )
    before = build_payload_for_day(**args)
    start(store, tmp_path, project)
    change(store, tmp_path, "prepare", id="a" * 32)
    assert build_payload_for_day(**args) == before
    store.meta_set("capability_reviews:v1", "keep")
    with pytest.raises(ValueError):
        change(store, tmp_path, "clear", confirm=False)
    change(store, tmp_path, "clear", confirm=True)
    assert si.export_history(store)["records"] == []
    assert store.meta_get("capability_reviews:v1") == "keep"


def test_playbooks_follow_host_overrides_and_retain_valid_cache(tmp_path, monkeypatch):
    config = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_CONTENT_BASE_URL": "https://example.com/editions",
        }
    )
    assert config.playbooks_source_url == "https://example.com/editions/harness-playbooks.json"
    explicit = resolve(
        {
            "PRACTICEGRAPH_CONTENT_BASE_URL": "https://example.com/editions",
            "PRACTICEGRAPH_HARNESS_PLAYBOOKS_URL": "https://example.com/explicit.json",
        }
    )
    assert explicit.playbooks_source_url.endswith("/explicit.json")
    store = Store(tmp_path / "state.db")
    store.migrate()
    monkeypatch.setattr(
        catalog, "_get_json", lambda *_args, **_kwargs: sealed("harness-playbooks", edition())
    )
    assert catalog.pull_public_playbooks(store, config, NOW) == "pulled"
    original = catalog.load_playbooks(tmp_path)
    monkeypatch.setattr(catalog, "_get_json", lambda *_args, **_kwargs: {"broken": True})
    assert (
        catalog.pull_public_playbooks(store, config, NOW + timedelta(hours=1)) == "invalid_artifact"
    )
    assert catalog.load_playbooks(tmp_path) == original
    assert (
        catalog.feed_status(tmp_path, "harness-playbooks", now=NOW)["source_url"]
        == config.playbooks_source_url
    )


def test_playbooks_participate_in_the_existing_release_gate(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "CONTENT_LICENSE.txt").write_text("Synthetic terms")
    (site / "harness-playbooks.json").write_text(json.dumps(edition()))
    result = prepare(site, tmp_path / "private/releases")
    assert result == inventory(site)
    assert "harness-playbooks" in result["channels"]


def test_no_extra_brief_when_the_supported_verification_signals_are_present(
    store, tmp_path, project
):
    (project / "README.md").write_text("Use npm test to verify changes.")
    seed(store, project, tests=1)
    key = start(store, tmp_path, project)
    assert read(store, tmp_path)["active"]["availability"] == "no_verification_gap"
    with pytest.raises(ValueError, match="no_verification_gap"):
        change(store, tmp_path, "prepare", id=key)


def test_background_followup_survives_restart_and_retains_completed_samples(
    store, tmp_path, project
):
    seed(store, project)
    key = start(store, tmp_path, project)
    change(store, tmp_path, "prepare", id=key)
    change(store, tmp_path, "attempt", id=key)
    for hour in (2, 4, 6):
        seed(store, project, hour, tests=1)
    later = NOW + timedelta(hours=8)
    assert si.refresh_followups(store, later, VERSIONS) == 1
    assert si.refresh_followups(store, later, VERSIONS) == 0
    restarted = Store(store.path)
    assert len(si.export_history(restarted)["records"][0]["followup"]) == 3
    # Completed comparisons remain available after the raw observation horizon.
    assert (
        read(restarted, tmp_path, NOW + timedelta(days=200))["active"]["comparison"]["state"]
        == "observed"
    )


def test_editorial_validator_checks_revision_history_before_release(tmp_path):
    from tools.harness_playbooks import validate

    prior = tmp_path / "prior.json"
    draft = tmp_path / "draft.json"
    prior.write_text(json.dumps(edition()))
    updated = edition()
    updated["entries"][0]["guidance"] = "Updated synthetic guidance."
    draft.write_text(json.dumps(updated))
    with pytest.raises(ValueError, match="new playbooks_version"):
        validate(draft, prior, today=NOW.date())
    updated["playbooks_version"] += "-r2"
    draft.write_text(json.dumps(updated))
    with pytest.raises(ValueError, match="higher revision"):
        validate(draft, prior, today=NOW.date())
    updated["entries"][0]["revision"] = 2
    draft.write_text(json.dumps(updated))
    assert validate(draft, prior, today=NOW.date()) == 1
    updated["entries"] = []
    draft.write_text(json.dumps(updated))
    with pytest.raises(ValueError, match="withdrawn"):
        validate(draft, prior, today=NOW.date())


def test_inspector_rejects_a_directory_in_place_of_a_manifest(project):
    (project / "package.json").unlink()
    (project / "package.json").mkdir()
    assert inspect_project(str(project))["files"][0]["state"] == "outside_scope"


def test_restore_rejects_inconsistent_draft_or_practice_link(store, tmp_path, project):
    key = start(store, tmp_path, project)
    change(store, tmp_path, "prepare", id=key)
    original = si.export_history(store)
    for field, value in (("state", "inspected"), ("practice_id", "a" * 24)):
        invalid = copy.deepcopy(original)
        invalid["records"][0][field] = value
        with pytest.raises(ValueError, match="invalid_backup"):
            change(store, tmp_path, "restore", backup=invalid)
    assert si.export_history(store) == original
    with pytest.raises(ValueError, match="mark_attempt_first"):
        change(store, tmp_path, "link_practice", id=key, practice_id=None)
