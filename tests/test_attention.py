"""Human-attention continuity uses verified human actions, never agent volume."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date
from zoneinfo import ZoneInfo

from practicegraph.analysis.attention import (
    attention_by_day,
    quiet_hours_reading,
    summarize_attention,
)
from practicegraph.analysis.schedule import compatibility_utc_schedule
from practicegraph.store import DayMarkRow


def _mark(
    ts: str,
    session: str = "s1",
    cwd: str = "project-a",
    branch: str = "main",
    *,
    human: int = 1,
) -> DayMarkRow:
    day = ts[:10]
    return (
        day,
        session,
        ts,
        "user_turn" if human else "assistant_turn",
        0, 0, 0, 0, 0, 1,
        cwd,
        branch,
        0,
        human,
    )


def test_quiet_hours_counts_local_days_and_ignores_background_traffic() -> None:
    schedule = replace(compatibility_utc_schedule(), timezone_name="Europe/Sofia")
    marks = [
        _mark("2026-09-05T09:00:00+00:00"),
        _mark("2026-09-04T21:15:00+00:00"),  # Sep 5, inside quiet hours locally.
        _mark("2026-09-04T22:00:00+00:00"),  # Same local day, never a second day.
        _mark("2026-09-04T09:00:00+00:00"),
        _mark("2026-08-30T09:00:00+00:00"),  # Current window boundary.
        _mark("2026-08-23T20:00:00+00:00"),  # Prior window boundary, quiet.
        _mark("2026-08-22T09:00:00+00:00"),  # Outside both windows.
        _mark("2026-09-05T22:00:00+00:00"),  # Tomorrow locally, excluded.
    ]
    reading = quiet_hours_reading(marks, schedule, date(2026, 9, 5))
    assert reading == {
        "from_day": "2026-08-30", "to_day": "2026-09-05",
        "observed_days": 3, "quiet_days": 1,
        "prior_observed_days": 1, "prior_quiet_days": 1,
        "observed_at": "2026-09-05T09:00:00+00:00",
    }
    background = [_mark("2026-09-03T23:00:00+00:00", human=0)] * 1000
    assert quiet_hours_reading(marks + background, schedule, date(2026, 9, 5)) == reading


def test_quiet_hours_needs_confirmed_schedule_and_current_human_evidence() -> None:
    schedule = compatibility_utc_schedule()
    today = date(2026, 9, 5)
    marks = [_mark("2026-09-05T09:00:00+00:00")]
    assert quiet_hours_reading(marks, replace(schedule, confirmed=False), today) is None
    assert quiet_hours_reading([], schedule, today) is None
    background = [_mark("2026-09-05T23:00:00+00:00", human=0)]
    assert quiet_hours_reading(background, schedule, today) is None
    assert quiet_hours_reading([_mark("2026-08-25T23:00:00+00:00")], schedule, today) is None


def test_same_workstream_actions_within_five_minutes_collapse() -> None:
    days = attention_by_day([
        _mark("2026-07-18T09:00:00+00:00"),
        _mark("2026-07-18T09:04:59+00:00"),
        _mark("2026-07-18T09:10:00+00:00"),
    ])
    assert days["2026-07-18"].human_events == 3
    assert days["2026-07-18"].episodes == 2


def test_known_cross_workstream_move_counts_only_inside_thirty_minutes() -> None:
    days = attention_by_day([
        _mark("2026-07-18T09:00:00+00:00", cwd="a"),
        _mark("2026-07-18T09:20:00+00:00", cwd="b"),
        _mark("2026-07-18T10:00:01+00:00", cwd="a"),
    ])
    assert days["2026-07-18"].cross_workstream_switches == 1


def test_same_project_agent_hopping_is_coordination_not_switching() -> None:
    days = attention_by_day([
        _mark("2026-07-18T09:00:00+00:00", session="s1"),
        _mark("2026-07-18T09:05:00+00:00", session="s2"),
        _mark("2026-07-18T09:10:00+00:00", session="s3"),
    ])
    day = days["2026-07-18"]
    assert day.cross_workstream_switches == 0
    assert day.coordination_windows == 1


def test_different_known_branches_are_workstreams_but_missing_branch_is_conservative() -> None:
    days = attention_by_day([
        _mark("2026-07-18T09:00:00+00:00", branch="one"),
        _mark("2026-07-18T09:10:00+00:00", branch=""),
        _mark("2026-07-18T09:20:00+00:00", branch="two"),
        _mark("2026-07-18T09:30:00+00:00", branch="one"),
    ])
    assert days["2026-07-18"].cross_workstream_switches == 1


def test_unknown_workstream_never_creates_a_switch() -> None:
    days = attention_by_day([
        _mark("2026-07-18T09:00:00+00:00", cwd=""),
        _mark("2026-07-18T09:10:00+00:00", cwd="known"),
    ])
    window = summarize_attention(days.values())
    assert window.cross_workstream_switches == 0
    assert window.known_events == 1
    assert window.known_coverage_pct == 50


def test_four_same_thread_prompts_make_one_non_overlapping_burst() -> None:
    marks = [
        _mark(f"2026-07-18T09:0{minute}:00+00:00")
        for minute in range(8)
    ]
    day = attention_by_day(marks)["2026-07-18"]
    assert day.prompt_burst_windows == 2


def test_background_volume_cannot_change_attention_metrics() -> None:
    human = [
        _mark("2026-07-18T09:00:00+00:00", cwd="a"),
        _mark("2026-07-18T09:20:00+00:00", cwd="b"),
    ]
    background = [
        _mark(
            f"2026-07-18T09:{minute % 60:02d}:{second % 60:02d}+00:00",
            session=f"agent-{minute}",
            human=0,
        )
        for minute in range(50)
        for second in range(20)
    ]
    assert attention_by_day(human) == attention_by_day(human + background)


def test_switch_is_attributed_to_destination_local_day() -> None:
    days = attention_by_day(
        [
            _mark("2026-07-18T20:55:00+00:00", cwd="a"),
            _mark("2026-07-18T21:05:00+00:00", cwd="b"),
        ],
        zone=ZoneInfo("Europe/Sofia"),
    )
    assert days["2026-07-19"].cross_workstream_switches == 1
    assert days["2026-07-18"].cross_workstream_switches == 0


def test_window_confidence_requires_days_events_and_identity_coverage() -> None:
    marks = []
    for day in range(1, 5):
        for event in range(3):
            marks.append(_mark(f"2026-07-{day:02d}T09:0{event}:00+00:00"))
    window = summarize_attention(attention_by_day(marks, zone=UTC).values())
    assert window.active_days == 4
    assert window.human_events == 12
    assert window.known_coverage_pct == 100
    assert window.confident is True
