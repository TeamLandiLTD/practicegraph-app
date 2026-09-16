"""Private capability outcome and practice ledger contracts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from practicegraph.analysis.capability_ledger import (
    CAPABILITY_OUTCOME_MAX_ENTRIES,
    CAPABILITY_OUTCOMES_KEY,
    CAPABILITY_PRACTICE_KEY,
    PracticeEntry,
    clear_practice,
    pending_outcome_unit,
    read_outcomes,
    read_practice,
    record_outcome,
    write_practice,
)
from practicegraph.analysis.capability_units import CapabilityUnitSummary
from practicegraph.store import Store

TODAY = date(2026, 8, 14)


@pytest.fixture
def store(tmp_path) -> Store:
    result = Store(tmp_path / "state.db")
    result.migrate()
    return result


def unit(unit_key: str) -> CapabilityUnitSummary:
    """Build a complete local-only summary with hand-checked field values."""
    return CapabilityUnitSummary(
        unit_key=unit_key,
        first_ts=datetime(2026, 8, 14, 9, tzinfo=UTC),
        last_ts=datetime(2026, 8, 14, 10, tzinfo=UTC),
        assistant_turns=6,
        prompt_tokens=1_200,
        cost_micro_usd=250,
        retries=1,
        command_failures=2,
        git_commit_attempts=1,
        test_run_attempts=3,
        files_created=2,
        doc_files_created=1,
        export_writes=1,
        files_edited=4,
        knowledge_evidence=True,
        software_evidence=False,
    )


def test_malformed_outcome_ledger_fails_closed(store: Store) -> None:
    """A corrupt persisted outcomes value must never yield partial history."""
    store.meta_set(CAPABILITY_OUTCOMES_KEY, "not-json")

    assert read_outcomes(store) == ()


def test_pending_outcome_is_latest_unanswered_and_once_per_day(store: Store) -> None:
    """A second prompt today would defeat the daily private-outcome limit."""
    older, latest = unit("older"), unit("latest")

    assert pending_outcome_unit((older, latest), (), TODAY) == latest

    record_outcome(store, latest, "yes", TODAY)

    assert pending_outcome_unit((older, latest), read_outcomes(store), TODAY) is None


def test_outcome_ledger_is_bounded(store: Store) -> None:
    """Unbounded private history would grow the local metadata record forever."""
    for index in range(CAPABILITY_OUTCOME_MAX_ENTRIES + 4):
        record_outcome(store, unit(f"unit-{index}"), "partly", TODAY + timedelta(days=index))

    entries = read_outcomes(store)

    assert len(entries) == CAPABILITY_OUTCOME_MAX_ENTRIES
    assert entries[-1].unit_key == f"unit-{CAPABILITY_OUTCOME_MAX_ENTRIES + 3}"


def test_outcome_reader_rejects_extra_fields_and_bool_counters(store: Store) -> None:
    """Permissive decoding could treat malformed evidence as a real outcome."""
    entry = {
        "unit_key": "unit-a",
        "day": TODAY.isoformat(),
        "outcome": "yes",
        "last_ts": "2026-08-14T10:00:00+00:00",
        "assistant_turns": 6,
        "prompt_tokens": 1200,
        "cost_micro_usd": 250,
        "retries": 1,
        "command_failures": 2,
        "git_commit_attempts": 1,
        "test_run_attempts": 3,
        "files_created": 2,
        "doc_files_created": 1,
        "export_writes": 1,
        "files_edited": 4,
        "knowledge_evidence": True,
        "software_evidence": False,
    }
    store.meta_set(CAPABILITY_OUTCOMES_KEY, json.dumps([{**entry, "extra": "no"}]))
    assert read_outcomes(store) == ()

    entry["assistant_turns"] = True
    store.meta_set(CAPABILITY_OUTCOMES_KEY, json.dumps([entry]))
    assert read_outcomes(store) == ()


def test_duplicate_persisted_outcome_unit_keys_fail_closed(store: Store) -> None:
    """Two stored answers for one unit make the private outcome ambiguous."""
    record_outcome(store, unit("unit-a"), "yes", TODAY)
    record_outcome(store, unit("unit-b"), "no", TODAY + timedelta(days=1))
    raw = store.meta_get(CAPABILITY_OUTCOMES_KEY)

    assert raw is not None
    duplicated = json.loads(raw)
    duplicated[1]["unit_key"] = "unit-a"
    store.meta_set(CAPABILITY_OUTCOMES_KEY, json.dumps(duplicated))

    assert read_outcomes(store) == ()


def test_record_outcome_rejects_unknown_or_duplicate_unit_keys(store: Store) -> None:
    """Unknown enums and duplicate units would make a private outcome ambiguous."""
    current = unit("unit-a")

    with pytest.raises(ValueError, match="invalid capability outcome"):
        record_outcome(store, current, "maybe", TODAY)
    assert read_outcomes(store) == ()

    record_outcome(store, current, "yes", TODAY)
    with pytest.raises(ValueError, match="duplicate capability outcome"):
        record_outcome(store, current, "no", TODAY)
    assert [entry.outcome for entry in read_outcomes(store)] == ["yes"]


def test_record_outcome_preserves_malformed_history(store: Store) -> None:
    """A write must not silently replace an unreadable outcome ledger."""
    store.meta_set(CAPABILITY_OUTCOMES_KEY, "not-json")

    with pytest.raises(ValueError, match="malformed capability outcomes"):
        record_outcome(store, unit("unit-a"), "yes", TODAY)

    assert store.meta_get(CAPABILITY_OUTCOMES_KEY) == "not-json"


def test_outcome_write_is_canonical_json(store: Store) -> None:
    """Canonical JSON keeps bounded local metadata stable across writes."""
    record_outcome(store, unit("unit-a"), "yes", TODAY)

    raw = store.meta_get(CAPABILITY_OUTCOMES_KEY)

    assert raw is not None
    assert raw == json.dumps(json.loads(raw), sort_keys=True)


def test_outcome_retention_drops_a_past_dated_late_append(store: Store) -> None:
    """Late ingestion of old work must not evict a newer retained outcome."""
    for index in range(CAPABILITY_OUTCOME_MAX_ENTRIES):
        record_outcome(store, unit(f"unit-{index:02}"), "partly", TODAY + timedelta(days=index))

    record_outcome(store, unit("unit-past"), "partly", TODAY - timedelta(days=1))

    assert [entry.unit_key for entry in read_outcomes(store)] == [
        f"unit-{index:02}" for index in range(CAPABILITY_OUTCOME_MAX_ENTRIES)
    ]


def test_outcome_retention_breaks_equal_day_ties_by_unit_key(store: Store) -> None:
    """Equal recency must retain the same 48 units regardless of write order."""
    for index in reversed(range(CAPABILITY_OUTCOME_MAX_ENTRIES + 1)):
        record_outcome(store, unit(f"unit-{index:02}"), "partly", TODAY)

    assert [entry.unit_key for entry in read_outcomes(store)] == [
        f"unit-{index:02}" for index in range(1, CAPABILITY_OUTCOME_MAX_ENTRIES + 1)
    ]


def test_practice_reader_fails_closed_for_invalid_path_or_types(store: Store) -> None:
    """A damaged active practice must not be interpreted as a valid one."""
    invalid = {
        "practice_id": "run_verification",
        "title": "Verify the result",
        "path": "executive",
        "accepted_day": TODAY.isoformat(),
        "baseline_unit_key": "unit-a",
        "baseline_last_ts": "2026-08-14T10:00:00+00:00",
        "baseline_value": 0,
    }
    store.meta_set(CAPABILITY_PRACTICE_KEY, json.dumps(invalid))
    assert read_practice(store) is None

    invalid["path"] = "software"
    invalid["baseline_value"] = False
    store.meta_set(CAPABILITY_PRACTICE_KEY, json.dumps(invalid))
    assert read_practice(store) is None


def test_practice_round_trip_replaces_and_clears_active_entry(store: Store) -> None:
    """The ledger must hold exactly one replaceable active practice."""
    practice = PracticeEntry(
        practice_id="run_verification",
        title="Verify the result",
        path="software",
        accepted_day=TODAY.isoformat(),
        baseline_unit_key="unit-a",
        baseline_last_ts="2026-08-14T10:00:00+00:00",
        baseline_value=0,
    )

    write_practice(store, practice)
    assert read_practice(store) == practice

    clear_practice(store)
    assert read_practice(store) is None
    assert store.meta_get(CAPABILITY_PRACTICE_KEY) == "null"


def test_finish_records_actual_use_and_releases_active_practice(store: Store) -> None:
    from practicegraph.analysis.capability_ledger import finish_practice, read_reviews

    practice = PracticeEntry("run_verification", "Verify", "software", "2026-08-14",
                             "private-unit", "2026-08-14T10:00:00+00:00", 0)
    write_practice(store, practice)
    result = finish_practice(store, "not_tried", TODAY)
    assert result.feedback == "not_tried"
    assert read_practice(store) is None
    assert read_reviews(store) == (result,)
    with pytest.raises(ValueError):
        finish_practice(store, "helpful", TODAY)


def test_corrupt_review_history_is_preserved_on_finish(store: Store) -> None:
    from practicegraph.analysis.capability_ledger import CAPABILITY_REVIEWS_KEY, finish_practice

    practice = PracticeEntry("run_verification", "Verify", "software", "2026-08-14",
                             "private-unit", "2026-08-14T10:00:00+00:00", 0)
    write_practice(store, practice)
    store.meta_set(CAPABILITY_REVIEWS_KEY, "broken")
    with pytest.raises(ValueError):
        finish_practice(store, "helpful", TODAY)
    assert read_practice(store) == practice
    assert store.meta_get(CAPABILITY_REVIEWS_KEY) == "broken"
