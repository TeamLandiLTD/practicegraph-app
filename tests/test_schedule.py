from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from practicegraph.analysis.schedule import (
    ScheduleUpdate,
    classify_time,
    compatibility_utc_schedule,
    local_day,
    local_day_bounds_utc,
    marks_for_local_day,
    read_schedule_profile,
    save_schedule_profile,
)
from practicegraph.store import DayMarkRow, Store


def _sofia() -> ScheduleUpdate:
    return ScheduleUpdate(
        timezone_name="Europe/Sofia",
        working_days=(0, 1, 2, 3, 4),
        work_start="09:00",
        work_end="18:00",
        quiet_start="22:00",
        quiet_end="07:00",
        weekend_mode="exceptional",
    )


def test_unconfigured_profile_is_only_a_suggestion(tmp_path: Path) -> None:
    profile = read_schedule_profile(tmp_path)
    assert profile.version == 0
    assert profile.confirmed is False
    assert profile.timezone_name
    assert profile.tzdata_version == "2026.2"


def test_save_confirms_versions_and_retains_profiles(tmp_path: Path) -> None:
    first = save_schedule_profile(tmp_path, _sofia())
    second = save_schedule_profile(tmp_path, replace(_sofia(), work_start="10:00"))
    assert first.confirmed is True and first.version == 1
    assert second.version == 2
    assert read_schedule_profile(tmp_path) == second
    assert read_schedule_profile(tmp_path, version=1) == first


def test_sofia_local_day_and_work_context(tmp_path: Path) -> None:
    profile = save_schedule_profile(tmp_path, _sofia())
    instant = datetime(2026, 7, 11, 21, 30, tzinfo=UTC)
    assert local_day(instant, profile) == date(2026, 7, 12)
    context = classify_time(instant, profile)
    assert context.local_day == date(2026, 7, 12)
    assert context.inside_preferred_hours is False
    assert context.inside_quiet_hours is True
    assert context.on_preferred_day is False


@pytest.mark.parametrize(
    ("timezone_name", "instant", "expected_day"),
    [
        ("Europe/Sofia", "2026-07-11T21:30:00+00:00", date(2026, 7, 12)),
        ("America/New_York", "2026-07-12T02:30:00+00:00", date(2026, 7, 11)),
        ("UTC", "2026-07-12T00:30:00+00:00", date(2026, 7, 12)),
    ],
)
def test_local_day_fixtures(
    timezone_name: str, instant: str, expected_day: date, tmp_path: Path
) -> None:
    profile = save_schedule_profile(
        tmp_path, replace(_sofia(), timezone_name=timezone_name)
    )
    assert local_day(datetime.fromisoformat(instant), profile) == expected_day


@pytest.mark.parametrize(
    ("day", "expected_hours"),
    [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)],
)
def test_dst_day_bounds_are_exact(
    day: date, expected_hours: int, tmp_path: Path
) -> None:
    profile = save_schedule_profile(tmp_path, _sofia())
    start, end = local_day_bounds_utc(day, profile)
    assert int((end - start).total_seconds() // 3600) == expected_hours


def test_pinned_derivation_vector_is_byte_stable(tmp_path: Path) -> None:
    vectors: dict[str, dict[str, list[str]]] = {}
    cases = {
        "Europe/Sofia": (date(2026, 3, 29), date(2026, 10, 25)),
        "America/New_York": (date(2026, 3, 8), date(2026, 11, 1)),
        "UTC": (date(2026, 3, 29), date(2026, 10, 25)),
    }
    for timezone_name, days in cases.items():
        profile = save_schedule_profile(
            tmp_path / timezone_name.replace("/", "_"),
            replace(_sofia(), timezone_name=timezone_name),
        )
        vectors[timezone_name] = {}
        for day in days:
            start, end = local_day_bounds_utc(day, profile)
            vectors[timezone_name][day.isoformat()] = [
                start.isoformat(),
                end.isoformat(),
            ]

    encoded = json.dumps(vectors, sort_keys=True, separators=(",", ":"))
    assert encoded == (
        '{"America/New_York":{"2026-03-08":'
        '["2026-03-08T05:00:00+00:00","2026-03-09T04:00:00+00:00"],'
        '"2026-11-01":["2026-11-01T04:00:00+00:00",'
        '"2026-11-02T05:00:00+00:00"]},"Europe/Sofia":'
        '{"2026-03-29":["2026-03-28T22:00:00+00:00",'
        '"2026-03-29T21:00:00+00:00"],"2026-10-25":'
        '["2026-10-24T21:00:00+00:00","2026-10-25T22:00:00+00:00"]},'
        '"UTC":{"2026-03-29":["2026-03-29T00:00:00+00:00",'
        '"2026-03-30T00:00:00+00:00"],"2026-10-25":'
        '["2026-10-25T00:00:00+00:00","2026-10-26T00:00:00+00:00"]}}'
    )


def test_invalid_profile_values_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown_timezone"):
        save_schedule_profile(tmp_path, replace(_sofia(), timezone_name="Mars/Base"))
    with pytest.raises(ValueError, match="invalid_clock"):
        save_schedule_profile(tmp_path, replace(_sofia(), work_start="25:00"))
    with pytest.raises(ValueError, match="invalid_working_days"):
        save_schedule_profile(tmp_path, replace(_sofia(), working_days=(0, 7)))


def test_malformed_schedule_config_never_silently_confirms(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.json").write_text(
        '{"schedule":{"current_version":1,"profiles":"broken"}}',
        encoding="utf-8",
    )
    profile = read_schedule_profile(tmp_path)
    assert profile.version == 0
    assert profile.confirmed is False


def test_unknown_retained_version_is_rejected(tmp_path: Path) -> None:
    save_schedule_profile(tmp_path, _sofia())
    with pytest.raises(ValueError, match="unknown_schedule_version"):
        read_schedule_profile(tmp_path, version=9)


class _MarkStore:
    def __init__(self, rows: list[DayMarkRow]) -> None:
        self.rows = rows

    def marks_between(self, from_day: str, to_day: str) -> list[DayMarkRow]:
        return self.rows


def _day_mark(day: str, timestamp: str) -> DayMarkRow:
    return (
        day,
        "s1",
        timestamp,
        "assistant_turn",
        0,
        0,
        0,
        0,
        0,
        1,
        "",
        "",
        0,
    )


def test_marks_for_local_day_spans_adjacent_utc_days() -> None:
    store = cast(
        Store,
        _MarkStore(
            [
                _day_mark("2026-07-11", "2026-07-11T21:30:00+00:00"),
                _day_mark("2026-07-12", "2026-07-12T20:30:00+00:00"),
                _day_mark("2026-07-12", "2026-07-12T21:30:00+00:00"),
            ]
        ),
    )
    profile = replace(compatibility_utc_schedule(), timezone_name="Europe/Sofia")
    marks = marks_for_local_day(store, date(2026, 7, 12), profile)
    assert [row[1] for row in marks] == [
        "2026-07-11T21:30:00+00:00",
        "2026-07-12T20:30:00+00:00",
    ]
