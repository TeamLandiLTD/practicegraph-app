"""Where the day ends — the session-tail reading.

The load-bearing tests pin the practice-day boundary (a past-midnight tail
belongs to the evening it grew from), the sample gates (below any floor the
reading or its parts are withheld, never zeroed), the two-sided cross line
(volume and hours in the same sentence), and what the copy may never say —
no sleep claim, no clinical register, nothing aimed at the person.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from practicegraph.analysis.schedule import compatibility_utc_schedule
from practicegraph.analysis.sessiontail import (
    SESSIONTAIL_COPY,
    TAIL_DAY_BOUNDARY_H,
    TAIL_MIN_DAY_EVENTS,
    TAIL_MIN_WEEKS,
    TAIL_WINDOW_WEEKS,
    compose_session_tail,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import MarkRow

END_DAY = date(2026, 6, 30)
SCHEDULE = compatibility_utc_schedule()  # confirmed UTC, quiet 22:00-06:00


def _mark(session: str, stamp: datetime, kind: str, tools: int = 1) -> MarkRow:
    return (
        session,
        stamp.isoformat(),
        kind,
        tools,
        0,  # retries
        0,  # interruptions
        0,  # command_failures
        0,  # compactions
        1,  # interactive
        "",  # cwd_hash
        "",  # branch_hash
        0,  # short_reply
        1,  # human_initiated
    )


def _work_day(
    day: date,
    *,
    end_hour: int,
    end_minute: int = 0,
    sessions: int = 1,
    tools_per_event: int = 1,
) -> list[MarkRow]:
    """One practice day: paired user/assistant turns from 10:00 local up to
    the requested last-prompt time, spread over `sessions` session keys so
    the 15-minute concurrency window sees them together."""
    start = datetime(day.year, day.month, day.day, 10, 0, tzinfo=SCHEDULE.zone)
    end = datetime(
        day.year, day.month, day.day, end_hour % 24, end_minute,
        tzinfo=SCHEDULE.zone,
    )
    if end_hour >= 24:
        end += timedelta(days=1)
    marks: list[MarkRow] = []
    steps = max(TAIL_MIN_DAY_EVENTS, 12)
    gap = (end - start) / steps
    for index in range(steps):
        stamp = start + gap * index
        kind = "user_turn" if index % 2 == 0 else "assistant_turn"
        # one event per live session inside the same minute, so the
        # 15-minute concurrency window sees them running together
        for lane in range(sessions):
            marks.append(
                _mark(
                    f"s{lane}",
                    stamp + timedelta(seconds=10 * lane),
                    kind,
                    tools_per_event,
                )
            )
    marks.append(_mark("s0", end, "user_turn", tools_per_event))
    return marks


def _weeks_of_days(
    *, early_end_hour: int, late_end_hour: int, sessions_late: int = 1
) -> list[MarkRow]:
    """Twelve weeks, four active days each: the first six weeks end at
    `early_end_hour`, the last six at `late_end_hour`."""
    marks: list[MarkRow] = []
    for week in range(TAIL_WINDOW_WEEKS):
        w_end = END_DAY - timedelta(days=7 * (TAIL_WINDOW_WEEKS - 1 - week))
        late = week >= TAIL_WINDOW_WEEKS // 2
        for offset in (1, 2, 3, 4):
            marks.extend(
                _work_day(
                    w_end - timedelta(days=offset),
                    end_hour=late_end_hour if late else early_end_hour,
                    sessions=sessions_late if late else 1,
                )
            )
    return marks


def test_background_user_carriers_cannot_shift_the_last_human_prompt() -> None:
    marks = _weeks_of_days(early_end_hour=18, late_end_hour=19)
    baseline = compose_session_tail(marks, SCHEDULE, END_DAY)
    background = [
        (*mark[:-1], 0)
        for mark in _weeks_of_days(early_end_hour=23, late_end_hour=25)
    ]
    assert compose_session_tail(marks + background, SCHEDULE, END_DAY) == baseline


def test_below_the_week_floor_the_reading_is_withheld() -> None:
    thin = _work_day(END_DAY - timedelta(days=1), end_hour=18)
    reading = compose_session_tail(thin, SCHEDULE, END_DAY)
    assert reading.available is False
    assert reading.weeks == ()
    assert reading.headline == ""


def test_past_midnight_tail_belongs_to_the_evening_it_grew_from() -> None:
    marks = _weeks_of_days(early_end_hour=18, late_end_hour=24)
    # the late weeks end at 00:00 the NEXT calendar day; the practice-day
    # boundary must attach that tail to the evening's own day.
    reading = compose_session_tail(marks, SCHEDULE, END_DAY)
    assert reading.available is True
    assert reading.weeks[-1].tail_clock == "00:00"
    # minutes are measured after the boundary: 00:00 is 19h past 05:00
    assert reading.weeks[-1].tail_minutes == (24 - TAIL_DAY_BOUNDARY_H) * 60


def test_trend_headline_names_both_clocks() -> None:
    marks = _weeks_of_days(early_end_hour=18, late_end_hour=23)
    reading = compose_session_tail(marks, SCHEDULE, END_DAY)
    assert len(reading.weeks) >= TAIL_MIN_WEEKS
    assert "18:00" in reading.headline and "23:00" in reading.headline
    assert reading.headline == SESSIONTAIL_COPY["trend-later"].format(
        weeks=len(reading.weeks), first="18:00", last="23:00"
    )


def test_holding_register_inside_the_band() -> None:
    marks = _weeks_of_days(early_end_hour=18, late_end_hour=18)
    reading = compose_session_tail(marks, SCHEDULE, END_DAY)
    assert "stayed near" in reading.headline


def test_concurrency_cross_carries_both_denominators() -> None:
    marks = _weeks_of_days(
        early_end_hour=17, late_end_hour=25, sessions_late=3
    )
    reading = compose_session_tail(marks, SCHEDULE, END_DAY)
    by_id = {bucket.bucket_id: bucket for bucket in reading.buckets}
    assert by_id["solo"].tail_clock == "17:00"
    assert by_id["multi"].tail_clock == "01:00"
    assert by_id["multi"].days == by_id["solo"].days
    assert reading.cross_line is not None
    # the sentence names volume AND hours — the honest-denominator rule
    assert "times the tool calls" in reading.cross_line
    assert "times the logged hours" in reading.cross_line
    assert "later" in reading.cross_line


def test_chart_geometry_ships_with_the_data() -> None:
    marks = _weeks_of_days(early_end_hour=18, late_end_hour=23)
    reading = compose_session_tail(marks, SCHEDULE, END_DAY)
    lows = min(w.tail_minutes for w in reading.weeks)
    highs = max(w.tail_minutes for w in reading.weeks)
    assert reading.axis_lo_minutes <= lows
    assert reading.axis_hi_minutes >= highs
    assert reading.axis_lo_minutes % 60 == 0
    assert reading.axis_hi_minutes % 60 == 0
    assert all(
        reading.axis_lo_minutes <= minutes <= reading.axis_hi_minutes
        for minutes, _label in reading.grid
    )
    # quiet 22:00 in the compatibility profile = 17h past the boundary
    assert reading.quiet_start_minutes == (22 - TAIL_DAY_BOUNDARY_H) * 60


def test_copy_is_clean_and_never_a_sleep_or_self_claim() -> None:
    for text in SESSIONTAIL_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
        low = text.lower()
        for banned in (
            "sleep", "bedtime", "rest ", "health", "unhealthy",
            "should stop", "too late", "too much",
            "you tend", "you always", "your habit", "your discipline",
        ):
            assert banned not in low, text


def test_double_compose_is_identical() -> None:
    marks = _weeks_of_days(early_end_hour=17, late_end_hour=25, sessions_late=3)
    assert compose_session_tail(marks, SCHEDULE, END_DAY) == compose_session_tail(
        marks, SCHEDULE, END_DAY
    )
