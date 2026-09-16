"""Where the day ends — the session-tail reading.

The behavioral half of a construct the self-report literature can only ask
about. The Syntax "Vibe Health Check" survey (2026, n=1,252 — self-report,
graded below peer review in docs/RELIANCE_PLAN.md) put its strongest
association on *continuing past your own intended stopping point* (rho 0.43
with reported sleep change, rho 0.38 with concurrent-agent count). A survey
must ask; these logs can read the behavior itself: the local clock time of
the last human prompt of each practice day, and how that tail moves against
the person's own history.

Two readings, one card:

  - the tail trend : weekly medians of the last-prompt clock across twelve
                     weeks, plus a recent-vs-baseline drift line.
  - the cross      : days bucketed by peak concurrent sessions, each bucket
                     carrying its median tail AND its work volume — per day
                     and per active hour, so "more work" and "just more
                     hours" stay distinguishable (the honest denominator).

Rules of the house, in full. This is *late-evening activity*, never "sleep"
— no clinical claim, no harm claim (the corroborating survey is itself
association-only, and says so). Two-sided by construction: the same bucket
that ends latest also carries the most work, and both facts render together
(the psych-atlas two-sided rule). Deterministic integer math over marks
passed in (INV-6). Local-only forever (NFR-PRV-6) — the vocabulary below is
wire-banned, and no emit imports this module. Suppression drops a reading;
unavailable is never rendered as zero.

A practice day runs 05:00→05:00 local (the confirmed ScheduleProfile zone):
a prompt at 00:24 belongs to the evening it grew from, not to tomorrow.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from practicegraph.analysis.focus import ACTIVITY_WINDOW_MIN
from practicegraph.analysis.schedule import ScheduleProfile
from practicegraph.report.format import duration_hm
from practicegraph.store import MARK_HUMAN_INITIATED, MARK_TOOL_CALLS, MarkRow

# Local-only vocabulary (test_wire unions every module's list into the scan).
# Bare "tail" would collide with legitimate words ("detail"); the banned terms
# are the specific compounds this module mints.
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "session_tail",
    "overrun",
    "last_prompt",
)

# A practice day starts at 05:00 local: the boundary that keeps a past-
# midnight tail attached to the evening it belongs to.
TAIL_DAY_BOUNDARY_H = 5
# The trend window: twelve 7-day buckets, newest anchored at the local today.
TAIL_WINDOW_WEEKS = 12
# A day below this many interactive events is a trace, not a workday — it
# has no tail worth reading and is dropped from every derivation here.
TAIL_MIN_DAY_EVENTS = 10
# A weekly point needs this many qualifying days or the point is dropped.
TAIL_MIN_WEEK_DAYS = 3
# The whole reading needs this many weekly points, else it is unavailable.
TAIL_MIN_WEEKS = 6
# Drift compares the last N active days against the prior-M active-day
# baseline; each side has its own floor below which the line is withheld.
TAIL_DRIFT_CURRENT_DAYS = 7
TAIL_DRIFT_BASELINE_DAYS = 28
TAIL_DRIFT_MIN_CURRENT = 5
TAIL_DRIFT_MIN_BASELINE = 14
# The trend register holds ("held near") inside this band, in minutes.
TAIL_TREND_HOLD_MIN = 45
# A concurrency bucket below this many days is too thin to read.
TAIL_MIN_BUCKET_DAYS = 5
# Active minutes use the same density-blind quarter-hour buckets as the
# conditioning readout (a chatty loop and a quiet read weigh the same).
TAIL_ACTIVE_BUCKET_MIN = 15
# Chart geometry: the axis snaps to whole hours with this much headroom.
TAIL_AXIS_PAD_MIN = 60
TAIL_GRID_STEP_MIN = 120

BUCKET_ORDER: tuple[str, ...] = ("solo", "pair", "multi")
BUCKET_LABEL: dict[str, str] = {
    "solo": "1 session",
    "pair": "2 sessions",
    "multi": "3+ sessions",
}

SESSIONTAIL_COPY: dict[str, str] = {
    "eyebrow": "Last recorded prompt",
    "title": "The last prompt of the day",
    "intro": "Weekly medians of the last positively identified human prompt, in your local "
    "timezone. Work outside supported tools is not observed.",
    "trend-later": "Across {weeks} weekly readings, the median last recorded prompt moved from "
    "{first} to {last}.",
    "trend-earlier": "Across {weeks} weekly readings, the median last recorded prompt moved from "
    "{first} to {last}.",
    "trend-holding": "Across {weeks} weekly readings, the median last recorded prompt stayed near "
    "{last}.",
    "drift-line": "Last {current_days} observed days: median last prompt {current}. The preceding "
    "{baseline_days} observed days: {baseline}.",
    "cross-title": "Days with several sessions end later",
    "cross-line": "Your {multi_label} days recorded {tools_ratio} times the tool calls "
    "and {hours_ratio} times the logged hours of your {solo_label} days, "
    "with the last recorded prompt {gap} later.",
    "bucket-tail-label": "median last prompt",
    "bucket-tools-label": "tool calls / day",
    "bucket-hours-label": "active hours / day",
    "bucket-density-label": "calls / active hour",
    "note": "Uses a {boundary}:00 local day boundary to keep after-midnight prompts with the "
    "previous evening. This is not the end of all work.",
}


@dataclass(frozen=True, slots=True)
class TailWeek:
    """One weekly point: the median last-prompt time of its qualifying days."""

    start_day: str  # ISO first calendar day of the 7-day bucket
    tail_minutes: int  # minutes after the 05:00 boundary
    tail_clock: str  # "HH:MM" local
    days: int


@dataclass(frozen=True, slots=True)
class TailBucket:
    """One concurrency cohort with its tail and its honest denominators."""

    bucket_id: str
    label: str
    days: int
    tail_minutes: int
    tail_clock: str
    tools_per_day: int
    active_hours_tenths: int
    tools_per_active_hour: int


@dataclass(frozen=True, slots=True)
class SessionTailReading:
    """Render-ready session-tail card. Chart geometry (axis bounds, grid,
    quiet-band edge) ships with the data so no surface re-derives it."""

    available: bool
    window_weeks: int = 0
    active_days: int = 0
    weeks: tuple[TailWeek, ...] = ()
    headline: str = ""
    drift_line: str | None = None
    buckets: tuple[TailBucket, ...] = ()
    cross_line: str | None = None
    note: str = ""
    boundary_hour: int = TAIL_DAY_BOUNDARY_H
    quiet_start_minutes: int | None = None  # band edge, minutes after boundary
    axis_lo_minutes: int = 0
    axis_hi_minutes: int = 0
    grid: tuple[tuple[int, str], ...] = ()  # (minutes-after-boundary, "HH:MM")


def _clock_of(minutes_after_boundary: int) -> str:
    total = (minutes_after_boundary + TAIL_DAY_BOUNDARY_H * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _ratio_tenths(numerator: int, denominator: int) -> int:
    return numerator * 10 // denominator if denominator > 0 else 0


def _fmt_tenths(tenths: int) -> str:
    return f"{tenths // 10}.{tenths % 10}"


@dataclass(frozen=True, slots=True)
class _Day:
    day: str  # practice-day ISO
    tail_minutes: int
    tool_calls: int
    active_minutes: int
    concurrency: int


def _practice_days(marks: list[MarkRow], schedule: ScheduleProfile, end_day: date) -> list[_Day]:
    """Group interactive marks into practice days and derive each day's tail,
    volume, active minutes, and peak concurrency. Marks outside the window
    and trace days are dropped."""
    window_start = end_day - timedelta(days=TAIL_WINDOW_WEEKS * 7 - 1)
    by_day: dict[str, list[tuple[datetime, str, int, str, int]]] = {}
    for mark in marks:
        # Interactive session membership alone includes background messages.
        # A last-human-prompt reading requires positive origin classification.
        if len(mark) <= MARK_HUMAN_INITIATED or not mark[MARK_HUMAN_INITIATED]:
            continue
        stamp = datetime.fromisoformat(mark[1])
        local = stamp.astimezone(schedule.zone)
        p_day = (local - timedelta(hours=TAIL_DAY_BOUNDARY_H)).date()
        if not window_start <= p_day <= end_day:
            continue
        clock = ((local.hour - TAIL_DAY_BOUNDARY_H) % 24) * 60 + local.minute
        by_day.setdefault(p_day.isoformat(), []).append(
            (stamp, mark[0], clock, mark[2], int(mark[MARK_TOOL_CALLS]))
        )
    window = timedelta(minutes=ACTIVITY_WINDOW_MIN)
    out: list[_Day] = []
    for day_key in sorted(by_day):
        events = sorted(by_day[day_key], key=lambda item: item[0])
        if len(events) < TAIL_MIN_DAY_EVENTS:
            continue
        user_clocks = [clock for _t, _s, clock, kind, _tc in events if kind == "user_turn"]
        if not user_clocks:
            continue
        best, lo = 1, 0
        for hi, (stamp, _s, _c, _k, _tc) in enumerate(events):
            while stamp - events[lo][0] > window:
                lo += 1
            distinct = len({s for _t, s, _c2, _k2, _tc2 in events[lo : hi + 1]})
            if distinct > best:
                best = distinct
        active_buckets = {
            (stamp.date(), stamp.hour, stamp.minute // TAIL_ACTIVE_BUCKET_MIN)
            for stamp, _s, _c, _k, _tc in events
        }
        out.append(
            _Day(
                day=day_key,
                tail_minutes=max(user_clocks),
                tool_calls=sum(tc for _t, _s, _c, _k, tc in events),
                active_minutes=len(active_buckets) * TAIL_ACTIVE_BUCKET_MIN,
                concurrency=best,
            )
        )
    return out


def _weekly_points(days: list[_Day], end_day: date) -> list[TailWeek]:
    points: list[TailWeek] = []
    for index in range(TAIL_WINDOW_WEEKS):
        w_end = end_day - timedelta(days=7 * (TAIL_WINDOW_WEEKS - 1 - index))
        w_start = w_end - timedelta(days=6)
        chunk = [d for d in days if w_start.isoformat() <= d.day <= w_end.isoformat()]
        if len(chunk) < TAIL_MIN_WEEK_DAYS:
            continue
        median = int(statistics.median(d.tail_minutes for d in chunk))
        points.append(
            TailWeek(
                start_day=w_start.isoformat(),
                tail_minutes=median,
                tail_clock=_clock_of(median),
                days=len(chunk),
            )
        )
    return points


def _headline(weeks: list[TailWeek]) -> str:
    first, last = weeks[0], weeks[-1]
    delta = last.tail_minutes - first.tail_minutes
    if delta >= TAIL_TREND_HOLD_MIN:
        template = SESSIONTAIL_COPY["trend-later"]
    elif delta <= -TAIL_TREND_HOLD_MIN:
        template = SESSIONTAIL_COPY["trend-earlier"]
    else:
        template = SESSIONTAIL_COPY["trend-holding"]
    return template.format(weeks=len(weeks), first=first.tail_clock, last=last.tail_clock)


def _drift_line(days: list[_Day]) -> str | None:
    current = days[-TAIL_DRIFT_CURRENT_DAYS:]
    baseline = days[-TAIL_DRIFT_CURRENT_DAYS - TAIL_DRIFT_BASELINE_DAYS : -TAIL_DRIFT_CURRENT_DAYS]
    if len(current) < TAIL_DRIFT_MIN_CURRENT or len(baseline) < TAIL_DRIFT_MIN_BASELINE:
        return None
    current_med = int(statistics.median(d.tail_minutes for d in current))
    baseline_med = int(statistics.median(d.tail_minutes for d in baseline))
    return SESSIONTAIL_COPY["drift-line"].format(
        current_days=len(current),
        current=_clock_of(current_med),
        baseline_days=len(baseline),
        baseline=_clock_of(baseline_med),
    )


def _buckets(days: list[_Day]) -> list[TailBucket]:
    grouped: dict[str, list[_Day]] = {key: [] for key in BUCKET_ORDER}
    for d in days:
        key = "solo" if d.concurrency == 1 else "pair" if d.concurrency == 2 else "multi"
        grouped[key].append(d)
    out: list[TailBucket] = []
    for key in BUCKET_ORDER:
        cohort = grouped[key]
        if len(cohort) < TAIL_MIN_BUCKET_DAYS:
            continue
        tail = int(statistics.median(d.tail_minutes for d in cohort))
        active_med = int(statistics.median(d.active_minutes for d in cohort))
        densities = [d.tool_calls * 60 // d.active_minutes for d in cohort if d.active_minutes > 0]
        out.append(
            TailBucket(
                bucket_id=key,
                label=BUCKET_LABEL[key],
                days=len(cohort),
                tail_minutes=tail,
                tail_clock=_clock_of(tail),
                tools_per_day=int(statistics.median(d.tool_calls for d in cohort)),
                active_hours_tenths=active_med * 10 // 60,
                tools_per_active_hour=(int(statistics.median(densities)) if densities else 0),
            )
        )
    return out


def _cross_line(buckets: list[TailBucket]) -> str | None:
    by_id = {bucket.bucket_id: bucket for bucket in buckets}
    solo, multi = by_id.get("solo"), by_id.get("multi")
    if solo is None or multi is None:
        return None
    if solo.tools_per_day <= 0 or solo.active_hours_tenths <= 0:
        return None
    gap = multi.tail_minutes - solo.tail_minutes
    if gap <= 0:
        return None  # the sentence only exists for the later-ending shape
    return SESSIONTAIL_COPY["cross-line"].format(
        multi_label=multi.label,
        solo_label=solo.label,
        tools_ratio=_fmt_tenths(_ratio_tenths(multi.tools_per_day, solo.tools_per_day)),
        hours_ratio=_fmt_tenths(_ratio_tenths(multi.active_hours_tenths, solo.active_hours_tenths)),
        gap=duration_hm(gap),
    )


def _quiet_start_minutes(schedule: ScheduleProfile) -> int | None:
    if not schedule.confirmed:
        return None
    hour, minute = int(schedule.quiet_start[:2]), int(schedule.quiet_start[3:])
    return ((hour - TAIL_DAY_BOUNDARY_H) % 24) * 60 + minute


def compose_session_tail(
    marks: list[MarkRow],
    schedule: ScheduleProfile,
    end_day: date,
) -> SessionTailReading:
    """The session-tail reading over the trailing twelve weeks.

    `marks` must already be interactive-gated (P3), flat, spanning at least
    the window plus a day of boundary slack on each side; grouping into
    05:00-boundary practice days happens here. Deterministic (INV-6): same
    marks and schedule, same reading."""
    days = _practice_days(marks, schedule, end_day)
    weeks = _weekly_points(days, end_day)
    if len(weeks) < TAIL_MIN_WEEKS:
        return SessionTailReading(available=False)
    buckets = _buckets(days)
    lo = min(w.tail_minutes for w in weeks)
    hi = max(w.tail_minutes for w in weeks)
    axis_lo = max(0, (lo - TAIL_AXIS_PAD_MIN) // 60 * 60)
    axis_hi = -((-(hi + TAIL_AXIS_PAD_MIN)) // 60) * 60
    grid = tuple(
        (mark_min, _clock_of(mark_min))
        for mark_min in range(axis_lo, axis_hi + 1, TAIL_GRID_STEP_MIN)
    )
    return SessionTailReading(
        available=True,
        window_weeks=TAIL_WINDOW_WEEKS,
        active_days=len(days),
        weeks=tuple(weeks),
        headline=_headline(weeks),
        drift_line=_drift_line(days),
        buckets=tuple(buckets),
        cross_line=_cross_line(buckets),
        note=SESSIONTAIL_COPY["note"].format(boundary=f"{TAIL_DAY_BOUNDARY_H:02d}"),
        boundary_hour=TAIL_DAY_BOUNDARY_H,
        quiet_start_minutes=_quiet_start_minutes(schedule),
        axis_lo_minutes=axis_lo,
        axis_hi_minutes=axis_hi,
        grid=grid,
    )
