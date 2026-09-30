"""Legacy local observations composed from a 28-day activity window.

Templates state recorded events and timing without inferring comprehension,
attention, or wellbeing. The current dashboard uses a separately qualified human
activity reading rather than replaying these reflections. Internal consumers
retain deterministic, closed templates. No emit field uses this module.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from statistics import median_low

from practicegraph.analysis.focus import (
    RhythmStats,
    compute_metrics,
)
from practicegraph.analysis.schedule import (
    ScheduleProfile,
    classify_time,
    compatibility_utc_schedule,
)
from practicegraph.report.format import count, duration_hm, percent
from practicegraph.store import MARK_FAILURES, MARK_TOOL_CALLS, MarkRow

# ---- gates (all calibrated against real history before pinning) ---------------

# Late-vs-daytime failure comparison: both cohorts need this many tool calls
# before a rate is worth speaking, and the late rate must be at least 30%
# worse before the effect clause renders (otherwise the pattern line runs).
EFFECT_MIN_TOOL_CALLS = 200
EFFECT_MATERIAL_NUM = 13  # late_rate * 10 >= day_rate * 13
LATE_DAY_MIN_MARKS = 6  # a day "worked late" only past a real stretch
LATE_PATTERN_MIN_PCT = 15  # matches the drift finding's floor
LATE_STEADY_MAX_PCT = 5
LATE_STEADY_MIN_EVENTS = 300

# Switch-cost comparison: median split over active-enough days.
SWITCH_DAY_MIN_EVENTS = 30
SWITCH_MIN_DAYS_PER_SIDE = 4
SWITCH_MIN_ELIGIBLE_DAYS = 10
SWITCH_MATERIAL_MIN = 10  # minutes of deep-block advantage, and >=30% more

# Review-behavior windows (28-day sums of the daily focus walk).
WAVED_Q_MIN_MOMENTS = 8
WAVED_Q_MIN_COUNT = 3
WAVED_Q_MIN_SHARE_PCT = 25
WAVED_STEADY_MIN_MOMENTS = 10
WAVED_STEADY_MAX_SHARE_PCT = 10
REFLEX_MIN_FOLLOWUPS = 60
REFLEX_Q_MIN_SHARE_PCT = 45
REFLEX_STEADY_MAX_SHARE_PCT = 15
REFIRE_MIN_TOTAL = 40

# Rhythm-sourced lines.
PACE_MIN_STREAK_DAYS = 8
WAITING_MIN_MINUTES = 180
DEEP_MIN_ACTIVE_DAYS = 10
DEEP_MIN_SHARE_PCT = 60
AGENT_MIN_MARKS = 1000
AGENT_MIN_PCT = 20

GROUP_SIZE = 3  # the app rotates reflections three at a time
MAX_REFLECTIONS = 3 * GROUP_SIZE

# ---- closed copy (scanned by the lexicon suite; NFR-QLT-3 reviewed diffs) -----

REFLECTION_COPY: dict[str, str] = {
    "late-effect": "Activity reached your confirmed quiet hours on {late_days} "
    "of the last {window} days. During those hours one command in {late_ratio} failed; "
    "outside them it was one in {day_ratio}.",
    "late-pattern": "Activity reached your confirmed quiet hours on {late_days} of the last "
    "{window} days — {late_pct}% of this month's activity.",
    "late-steady": "Almost all of this month's activity stayed outside your confirmed quiet hours. "
    "That timing pattern has been consistent.",
    "switch-effect": "On days with fewer recorded session changes, the longest log block was "
    "{low_min} minutes; on days with more it was {high_min}. This does not measure "
    "attention.",
    "waved-question": "{waved} of {moments} recorded long-run approvals arrived within seconds of "
    "the summary this month. Timing does not establish whether review happened.",
    "waved-steady": "Most of the {moments} recorded long-run approvals fell outside "
    "the quick-reply thresholds. Timing does not establish review quality.",
    "reflex-question": "{share}% of {followups} recorded follow-ups arrived within thirty seconds "
    "this month. Timing alone does not establish what was read.",
    "reflex-steady": "Recorded follow-ups usually arrived minutes after an answer. "
    "This timing does not establish reading or understanding.",
    "refire": "{refires} recorded replies followed a failure within five minutes this month. "
    "Inspecting the failure may help decide what to try next.",
    "pace": "On {streak_days} days, logged activity continued for two hours without a "
    "fifteen-minute gap. Gaps in logs are not a measure of breaks taken.",
    "waiting": "Prompt-to-response intervals totalled {waiting} this month. "
    "They can overlap and do not measure time spent waiting at the screen.",
    "deep-steady": "A log block of at least 45 minutes occurred on {deep_days} of {active_days} "
    "observed days. The logs do not establish uninterrupted attention.",
    "agent-share": "{agent_pct}% of recorded activity events were noninteractive and {human_pct}% "
    "were in interactive sessions. These are event shares, not shares of working "
    "time.",
}


@dataclasses.dataclass(frozen=True, slots=True)
class _DayStats:
    """One day's cohort inputs, from raw marks plus the daily focus walk."""

    events: int
    quiet_hours_events: int
    tool_calls: int
    failures: int
    late_tool_calls: int
    late_failures: int
    switches: int
    longest_block_min: int
    approval_moments: int
    waved_through: int
    followups: int
    reflex_replies: int
    refire_replies: int


def _day_stats(marks: list[MarkRow], schedule: ScheduleProfile) -> _DayStats:
    events = quiet_hours_events = tools = fails = late_tools = late_fails = 0
    for mark in marks:
        events += 1
        # Counters via the MARK_* constants (append-only mark contract);
        # short legacy/test rows default missing counters to zero.
        mark_tools = mark[MARK_TOOL_CALLS] if len(mark) > MARK_TOOL_CALLS else 0
        mark_fails = mark[MARK_FAILURES] if len(mark) > MARK_FAILURES else 0
        tools += mark_tools
        fails += mark_fails
        if classify_time(datetime.fromisoformat(mark[1]), schedule).inside_quiet_hours:
            quiet_hours_events += 1
            late_tools += mark_tools
            late_fails += mark_fails
    metrics = compute_metrics(marks)
    return _DayStats(
        events=events,
        quiet_hours_events=quiet_hours_events,
        tool_calls=tools,
        failures=fails,
        late_tool_calls=late_tools,
        late_failures=late_fails,
        switches=metrics.switch_count,
        longest_block_min=metrics.longest_block_min,
        approval_moments=metrics.approval_moments,
        waved_through=metrics.waved_through,
        followups=metrics.assistant_followups,
        reflex_replies=metrics.reflex_replies,
        refire_replies=metrics.refire_replies,
    )


def _line(reflection_id: str, tone: str, **values: object) -> dict[str, str]:
    return {
        "id": reflection_id,
        "tone": tone,
        "text": REFLECTION_COPY[reflection_id].format(**values),
    }


def _late_lines(days: list[_DayStats], rhythm: RhythmStats) -> dict[str, str] | None:
    late_days = sum(1 for day in days if day.quiet_hours_events >= LATE_DAY_MIN_MARKS)
    late_tools = sum(day.late_tool_calls for day in days)
    late_fails = sum(day.late_failures for day in days)
    day_tools = sum(day.tool_calls - day.late_tool_calls for day in days)
    day_fails = sum(day.failures - day.late_failures for day in days)
    if (
        late_tools >= EFFECT_MIN_TOOL_CALLS
        and day_tools >= EFFECT_MIN_TOOL_CALLS
        and late_fails > 0
        and day_fails > 0
        and late_days > 0
    ):
        late_rate = late_fails * 1000 // late_tools
        day_rate = day_fails * 1000 // day_tools
        late_ratio = late_tools // late_fails
        day_ratio = day_tools // day_fails
        if (
            day_rate > 0
            and late_rate * 10 >= day_rate * EFFECT_MATERIAL_NUM
            and late_ratio < day_ratio
        ):
            return _line(
                "late-effect",
                "watch",
                late_days=count(late_days),
                window=rhythm.window_days,
                late_ratio=count(late_ratio),
                day_ratio=count(day_ratio),
            )
    if (
        rhythm.quiet_hours_activity_pct is not None
        and rhythm.quiet_hours_activity_pct >= LATE_PATTERN_MIN_PCT
        and late_days > 0
    ):
        return _line(
            "late-pattern",
            "watch",
            late_days=count(late_days),
            window=rhythm.window_days,
            late_pct=rhythm.quiet_hours_activity_pct,
        )
    if (
        rhythm.quiet_hours_activity_pct is not None
        and rhythm.quiet_hours_activity_pct <= LATE_STEADY_MAX_PCT
        and rhythm.events_total >= LATE_STEADY_MIN_EVENTS
    ):
        return _line("late-steady", "steady")
    return None


def _switch_line(days: list[_DayStats]) -> dict[str, str] | None:
    eligible = [day for day in days if day.events >= SWITCH_DAY_MIN_EVENTS]
    if len(eligible) < SWITCH_MIN_ELIGIBLE_DAYS:
        return None
    split = median_low(sorted(day.switches for day in eligible))
    low = [day for day in eligible if day.switches <= split]
    high = [day for day in eligible if day.switches > split]
    if len(low) < SWITCH_MIN_DAYS_PER_SIDE or len(high) < SWITCH_MIN_DAYS_PER_SIDE:
        return None
    low_avg = sum(day.longest_block_min for day in low) // len(low)
    high_avg = sum(day.longest_block_min for day in high) // len(high)
    if low_avg >= high_avg + SWITCH_MATERIAL_MIN and low_avg * 10 >= high_avg * EFFECT_MATERIAL_NUM:
        return _line(
            "switch-effect",
            "watch",
            low_min=count(low_avg),
            high_min=count(high_avg),
        )
    return None


def compose_reflections(
    marks_by_day: dict[str, list[MarkRow]],
    rhythm: RhythmStats | None,
    marks_total: int,
    marks_interactive: int,
    schedule: ScheduleProfile | None = None,
) -> list[dict[str, str]]:
    """The month's reading, as render-ready {id, tone, text} sentences.

    Tones: "watch" (a measured pattern worth seeing), "question" (review
    behavior handed back as a question), "steady" (a pattern worth keeping).
    Ordered so every rotation trio tends toward pattern -> question -> keep;
    the app chunks the list three at a time and owns the motion.
    """
    if rhythm is None or not marks_by_day:
        return []
    schedule = schedule or compatibility_utc_schedule()
    days = [_day_stats(marks_by_day[day], schedule) for day in sorted(marks_by_day)]

    watch: list[dict[str, str] | None] = []
    question: list[dict[str, str] | None] = []
    steady: list[dict[str, str] | None] = []

    late = _late_lines(days, rhythm)
    if late is not None and late["tone"] == "steady":
        steady.append(late)
    else:
        watch.append(late)
    watch.append(_switch_line(days))

    if rhythm.long_streak_days >= PACE_MIN_STREAK_DAYS:
        watch.append(_line("pace", "watch", streak_days=rhythm.long_streak_days))

    refires = sum(day.refire_replies for day in days)
    if refires >= REFIRE_MIN_TOTAL:
        watch.append(_line("refire", "watch", refires=count(refires)))

    if rhythm.waiting_minutes >= WAITING_MIN_MINUTES:
        watch.append(_line("waiting", "watch", waiting=duration_hm(rhythm.waiting_minutes)))

    moments = sum(day.approval_moments for day in days)
    waved = sum(day.waved_through for day in days)
    if (
        moments >= WAVED_Q_MIN_MOMENTS
        and waved >= WAVED_Q_MIN_COUNT
        and waved * 100 >= moments * WAVED_Q_MIN_SHARE_PCT
    ):
        question.append(
            _line(
                "waved-question",
                "question",
                waved=count(waved),
                moments=count(moments),
            )
        )
    elif (
        moments >= WAVED_STEADY_MIN_MOMENTS and waved * 100 <= moments * WAVED_STEADY_MAX_SHARE_PCT
    ):
        steady.append(_line("waved-steady", "steady", moments=count(moments)))

    followups = sum(day.followups for day in days)
    reflex = sum(day.reflex_replies for day in days)
    share = percent(reflex, followups)
    if followups >= REFLEX_MIN_FOLLOWUPS and share >= REFLEX_Q_MIN_SHARE_PCT:
        question.append(
            _line(
                "reflex-question",
                "question",
                share=share,
                followups=count(followups),
            )
        )
    elif followups >= REFLEX_MIN_FOLLOWUPS and share <= REFLEX_STEADY_MAX_SHARE_PCT:
        steady.append(_line("reflex-steady", "steady"))

    if (
        rhythm.active_days >= DEEP_MIN_ACTIVE_DAYS
        and rhythm.deep_block_days * 100 >= rhythm.active_days * DEEP_MIN_SHARE_PCT
    ):
        steady.append(
            _line(
                "deep-steady",
                "steady",
                deep_days=count(rhythm.deep_block_days),
                active_days=count(rhythm.active_days),
            )
        )

    agent_pct = percent(marks_total - marks_interactive, marks_total)
    if marks_total >= AGENT_MIN_MARKS and agent_pct >= AGENT_MIN_PCT:
        steady.append(
            _line(
                "agent-share",
                "steady",
                agent_pct=agent_pct,
                human_pct=100 - agent_pct,
            )
        )

    watches = [entry for entry in watch if entry is not None]
    questions = [entry for entry in question if entry is not None]
    steadies = [entry for entry in steady if entry is not None]
    ordered: list[dict[str, str]] = []
    for index in range(max(len(watches), len(questions), len(steadies))):
        for bucket in (watches, questions, steadies):
            if index < len(bucket):
                ordered.append(bucket[index])
    return ordered[:MAX_REFLECTIONS]
