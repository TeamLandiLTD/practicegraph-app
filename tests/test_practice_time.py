"""Private time accounting: no duplicate time, downtime, or unconfirmed learning claims."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from practicegraph.analysis import practice_time as pt
from practicegraph.analysis.schedule import compatibility_utc_schedule
from practicegraph.store import Store

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
SCHEDULE = compatibility_utc_schedule()


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    value = Store(tmp_path / "state.db")
    value.migrate()
    return value


def change(store: Store, action: str, offset: int = 0, **values):
    pt.change(store, {"action": action, **values}, NOW + timedelta(seconds=offset), SCHEDULE)


def view(store: Store, offset: int = 0):
    return pt.reading(store, NOW + timedelta(seconds=offset), SCHEDULE)


def start(store: Store, offset: int = 0, skill: str = "ai-development") -> str:
    change(store, "enable", enabled=True)
    change(store, "start", offset, skill=skill)
    return view(store, offset)["active"]["id"]


def finish(store: Store, entry_id: str, offset: int = 60) -> None:
    change(store, "finish", offset, id=entry_id)
    change(
        store,
        "confirm",
        offset,
        id=entry_id,
        skill="ai-development",
        seconds=60,
        reflection="learned",
    )


def test_timer_persists_but_needs_review_before_counting(store: Store):
    entry = start(store)
    change(store, "heartbeat", 30, id=entry)
    assert view(Store(store.path), 40)["active"]["seconds"] == 30
    assert view(store, 40)["skills"][0]["confirmed_seconds"] == 0
    finish(store, entry)
    result = view(store, 60)
    assert result["active"] is None
    assert result["skills"][0]["confirmed_seconds"] == 60
    assert result["history"][0]["reflection"] == "learned"
    assert (
        pt.reading(Store(store.path), NOW + timedelta(days=400), SCHEDULE)["skills"][0][
            "confirmed_seconds"
        ]
        == 60
    )


def test_missed_heartbeat_never_backfills_downtime(store: Store):
    entry = start(store)
    change(store, "heartbeat", 30, id=entry)
    change(store, "heartbeat", 1000, id=entry)
    assert view(store, 1000)["active"]["seconds"] == 30
    assert view(store, 1000)["active"]["running"] is False
    change(store, "resume", 1100, id=entry)
    change(store, "finish", 1130, id=entry)
    assert view(store, 1130)["pending"][0]["seconds"] == 60


def test_overlapping_windows_and_duplicate_heartbeats_count_once(store: Store):
    entry = start(store)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _: change(store, "heartbeat", 30, id=entry), range(8)))
    assert view(store, 30)["active"]["seconds"] == 30
    with pytest.raises(ValueError, match="finish_active_first"):
        change(store, "start", 35, skill="verification")
    finish(store, entry)
    change(store, "confirm", 60, id=entry, skill="ai-development", seconds=60, reflection="learned")
    assert view(store, 60)["skills"][0]["confirmed_seconds"] == 60


def test_pause_and_existing_break_control_exclude_the_break(store: Store):
    entry = start(store)
    pt.record_break(store, NOW + timedelta(seconds=30), starting=True)
    change(store, "heartbeat", 50, id=entry)
    assert view(store, 50)["active"]["running"] is False
    pt.record_break(store, NOW + timedelta(seconds=100), starting=False)
    change(store, "resume", 100, id=entry)
    change(store, "finish", 130, id=entry)
    assert view(store, 130)["pending"][0]["seconds"] == 60


def test_disabling_preserves_history_and_pauses_current_session(store: Store):
    entry = start(store)
    change(store, "enable", 30, enabled=False)
    result = view(store, 60)
    assert result["enabled"] is False
    assert result["active"]["seconds"] == 30 and result["active"]["running"] is False
    with pytest.raises(ValueError, match="history_disabled"):
        change(store, "heartbeat", 60, id=entry)


def test_correcting_time_and_skill_updates_totals_without_creating_more_time(store: Store):
    entry = start(store)
    finish(store, entry)
    change(store, "confirm", 60, id=entry, skill="verification", seconds=45, reflection="unsure")
    result = view(store, 60)
    assert result["skills"][0]["confirmed_seconds"] == 0
    assert result["skills"][4]["confirmed_seconds"] == 45
    with pytest.raises(ValueError, match="invalid_duration"):
        change(store, "confirm", 60, id=entry, skill="verification", seconds=60, reflection="")


def seed_human(store: Store, offsets: list[int], human: int = 1, path: str = "human"):
    marks = []
    for offset in offsets:
        stamp = NOW + timedelta(seconds=offset)
        marks.append(
            (
                stamp.date().isoformat(),
                "synthetic-session",
                stamp.isoformat(),
                "user_turn",
                0,
                0,
                0,
                0,
                0,
                1,
                "",
                "",
                0,
                human,
            )
        )
    store.replace_file_data(
        source_id="codex_cli",
        path_hash=path,
        cursor=(1, 1, 1),
        contributions=[],
        marks=marks,
        health=None,
        turn_keys=(),
        now=NOW,
    )


def test_activity_estimates_require_human_evidence_and_never_count_automatically(store: Store):
    seed_human(store, [-600, -300, 0])
    seed_human(store, list(range(-590, 0)), human=0, path="agent")
    change(store, "enable", enabled=True)
    suggestion = view(store)["suggestions"][0]
    assert suggestion["seconds"] == 600
    assert view(store)["skills"][0]["confirmed_seconds"] == 0
    change(store, "confirm", id=suggestion["id"], skill="ai-research", seconds=300, reflection="")
    assert view(store)["suggestions"] == []
    assert view(store)["skills"][1]["confirmed_seconds"] == 300


def test_suggestions_merge_parallel_human_records_and_exclude_gaps_and_existing_time(store: Store):
    seed_human(store, [0, 60, 120, 3600, 3900])
    seed_human(store, [0, 60, 120, 3600, 3900], path="parallel")
    entry = start(store)
    finish(store, entry)
    result = view(store, 3900)
    assert result["suggestions"][0]["seconds"] == 360  # 420 less the confirmed minute.


def test_backup_restore_is_idempotent_and_does_not_import_active_clocks(
    store: Store, tmp_path: Path
):
    entry = start(store)
    finish(store, entry)
    change(store, "goal", 60, skill="ai-development", hours=10000)
    backup = pt.export_history(store, NOW + timedelta(seconds=60))
    other = Store(tmp_path / "restored.db")
    other.migrate()
    for _ in range(2):
        change(other, "restore", 60, backup=backup)
    assert view(other, 60)["skills"][0]["confirmed_seconds"] == 60
    assert view(other, 60)["skills"][0]["goal_hours"] == 10000
    assert view(other, 60)["active"] is None


def test_import_failure_is_atomic_and_overlapping_time_is_rejected(store: Store):
    entry = start(store)
    finish(store, entry)
    backup = pt.export_history(store, NOW + timedelta(seconds=60))
    overlapping = copy.deepcopy(backup)
    overlapping["entries"][0]["id"] = "a" * 24
    with pytest.raises(ValueError, match="overlapping_time"):
        change(store, "restore", 60, backup=overlapping)
    bad = copy.deepcopy(backup)
    bad["entries"].append({"raw_prompt": "must not enter the journal"})
    with pytest.raises(ValueError, match="invalid_backup"):
        change(store, "restore", 60, backup=bad)
    assert pt.export_history(store, NOW + timedelta(seconds=60)) == backup


@pytest.mark.parametrize("fragmented", [False, True])
def test_backup_accepts_long_local_days_and_many_timer_pauses(store: Store, fragmented):
    stamp = int(NOW.timestamp())
    intervals = (
        [[stamp - 3000 + n * 2, stamp - 2999 + n * 2] for n in range(1001)]
        if fragmented
        else [[stamp - 25 * 3600, stamp]]
    )
    entry = {
        "id": "a" * 24,
        "skill": "ai-development",
        "created_at": intervals[0][0],
        "source": "timer" if fragmented else "observed",
        "intervals": intervals,
        "state": "confirmed",
        "running": False,
        "last_tick": stamp,
        "reflection": "",
    }
    backup = {"schema": pt.SCHEMA, "entries": [entry], "goals": {}}
    change(store, "restore", backup=backup)
    assert pt.export_history(store, NOW) == backup
    assert view(store)["skills"][0]["confirmed_seconds"] == (1001 if fragmented else 90000)


@pytest.mark.parametrize("hours", [True, -1, 0, 1.5, "10000", 100001])
def test_goals_are_optional_bounded_whole_hours(store: Store, hours):
    change(store, "enable", enabled=True)
    with pytest.raises(ValueError, match="invalid_goal"):
        change(store, "goal", skill="ai-development", hours=hours)
    change(store, "goal", skill="ai-development", hours=None)
    assert view(store)["skills"][0]["goal_hours"] is None


def test_clear_only_removes_practice_time_and_requires_confirmation(store: Store):
    start(store)
    store.meta_set("capability_reviews:v1", "keep")
    with pytest.raises(ValueError, match="confirmation_required"):
        change(store, "clear", confirm=1)
    change(store, "clear", confirm=True)
    assert view(store)["history"] == [] and view(store)["enabled"] is False
    assert store.meta_get("capability_reviews:v1") == "keep"


def test_manual_time_can_recover_offline_practice_but_cannot_overlap(store: Store):
    change(store, "enable", enabled=True)
    start_time = int((NOW - timedelta(hours=1)).timestamp())
    change(
        store,
        "manual",
        skill="ai-writing",
        start=start_time,
        end=int(NOW.timestamp()),
        reflection="learned",
    )
    assert view(store)["skills"][2]["confirmed_seconds"] == 3600
    with pytest.raises(ValueError, match="overlapping_time"):
        change(
            store,
            "manual",
            skill="verification",
            start=start_time,
            end=int(NOW.timestamp()),
            reflection="",
        )
    with pytest.raises(ValueError, match="invalid_duration"):
        change(
            store,
            "manual",
            skill="ai-writing",
            start=int(NOW.timestamp()),
            end=int(NOW.timestamp()) + 60,
            reflection="",
        )


def test_week_totals_split_at_local_monday_and_full_lifetime_is_retained(store: Store):
    schedule = replace(SCHEDULE, timezone_name="Europe/Sofia")
    begin = int(datetime(2026, 8, 30, 20, 30, tzinfo=UTC).timestamp())
    change(store, "enable", enabled=True)
    change(store, "manual", skill="ai-development", start=begin, end=begin + 3600, reflection="")
    result = pt.reading(store, NOW, schedule)
    assert result["week_start"] == "2026-08-31"
    assert result["skills"][0]["confirmed_seconds"] == 3600
    assert result["skills"][0]["week_seconds"] == 1800
    # A lifetime history is not clipped to the coach ledger's 48-entry retention.
    for index in range(105):
        point = int(NOW.timestamp()) - 30000 + index * 120
        change(store, "manual", skill="ai-research", start=point, end=point + 60, reflection="")
    assert view(store)["skills"][1]["confirmed_seconds"] == 105 * 60
    assert len(view(store)["history"]) == 100
    assert len(pt.export_history(store, NOW)["entries"]) == 106


def test_clock_reversal_stops_an_active_clock_without_credit(store: Store):
    entry = start(store)
    change(store, "heartbeat", 30, id=entry)
    change(store, "heartbeat", 15, id=entry)
    assert view(store, 15)["active"]["running"] is False
    assert view(store, 15)["active"]["seconds"] == 30


def test_practice_journal_never_changes_an_emit(store: Store):
    from practicegraph.emit import build_payload_for_day

    args = dict(
        store=store,
        day="2026-09-05",
        org_id="synthetic-org",
        engagement={},
        emit_id="test-emit",
        platform="windows",
        source_health=[],
    )
    before = build_payload_for_day(**args)
    entry = start(store)
    finish(store, entry)
    change(store, "goal", 60, skill="ai-development", hours=10000)
    assert build_payload_for_day(**args) == before
