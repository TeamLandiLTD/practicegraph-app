"""Conditioning: three composite readings of the practice's fitness level,
computed indirectly from the behavioral record — never a re-display of the
practice dimension scores (those already live in "Your practice").

The design borrows the *shape* of how endurance sport reads an athlete's
condition, not its vocabulary or its verdicts:

  - capacity     : the deep-block length you can reliably repeat — the median
                   of the five longest daily uninterrupted blocks in the
                   window. A one-off marathon day is noise; a block you can
                   repeat is capacity.
  - load_balance : this week's active minutes per day against your own
                   four-week average (an acute-vs-chronic ratio). 100 means a
                   typical week; a sharp ramp or a long taper both show here.
  - composure    : of the moments that asked for judgment — follow-ups after
                   an answer, approvals after a long run — the share answered
                   deliberately rather than instantly. The resting pulse of
                   the practice.

Rules of the house apply in full: deterministic integer math over closed
counters (INV-6), closed render copy scanned by the lexicon suite (FR-FOC-8),
observational register (a reading, never a verdict), local-only forever
(NFR-PRV-6 — no emit imports this module), and suppression means an indicator
is DROPPED when its gate is not met — unavailable is never rendered as zero.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from practicegraph.analysis.focus import FocusMetrics, compute_metrics
from practicegraph.store import MarkRow

CONDITIONING_WINDOW_DAYS = 28  # the chronic window (named constant)
ACUTE_WINDOW_DAYS = 7  # the acute window inside it

# Capacity: median of the top-N daily longest blocks; needs at least N days
# with any block at all, else the reading is unavailable (dropped, not 0).
CAPACITY_TOP_DAYS = 5
CAPACITY_HOLD_BAND_MIN = 15  # |delta| under this reads as "holding"
CAPACITY_TARGET_MIN = 90  # the protected-block reference on the meter
CAPACITY_AXIS_MIN = 240  # the meter never shrinks below this ceiling
CAPACITY_AXIS_STEP = 60  # axis grows in whole hours past the ceiling

# Load balance: active minutes measured as distinct quarter-hour buckets, so
# event-density (agent loops, chatty tools) cannot inflate the reading.
ACTIVE_BUCKET_MIN = 15
LOAD_STEADY_MIN_PCT = 80  # under: an easier stretch
LOAD_STEADY_MAX_PCT = 130  # over: a sharp ramp
LOAD_AXIS_MAX_PCT = 200  # meter track edge; the true value can exceed it
LOAD_MIN_ACTIVE_DAYS = 7  # a sparser month makes the ratio noise — drop it

# Composure: deliberate share of review moments. Below the moments gate the
# sample is too thin to read — the indicator is dropped, never zeroed.
COMPOSURE_MIN_MOMENTS = 50
COMPOSURE_STRONG_MIN_PCT = 85
COMPOSURE_WATCH_MAX_PCT = 69

INDICATORS: tuple[str, ...] = ("capacity", "load_balance", "composure")

INDICATOR_LABEL: dict[str, str] = {
    "capacity": "Capacity",
    "load_balance": "Load balance",
    "composure": "Composure",
}

# What each reading measures — the stable hover copy, independent of today's
# numbers. Observational, lexicon-scanned like every closed catalog.
INDICATOR_MEASURES: dict[str, str] = {
    "capacity": (
        "The deep-block length you can reliably repeat: the median of your "
        "five longest uninterrupted daily blocks across four weeks — not the "
        "single best day."
    ),
    "load_balance": (
        "This week's active minutes per day against your own four-week "
        "average. 100 is a typical week; the steady band runs 80 to 130."
    ),
    "composure": (
        "Of the moments that asked for your judgment — follow-ups after an "
        "answer, approvals after a long run — the share answered "
        "deliberately rather than instantly."
    ),
}

INDICATOR_WHY: dict[str, str] = {
    "capacity": (
        "A one-off marathon is noise; a block you can repeat is capacity. It "
        "grows when long blocks are protected, and it is the base every hard "
        "problem draws on."
    ),
    "load_balance": (
        "A ratio only says this week is heavier or lighter than your own "
        "norm - whether either suits you is not something a timestamp can "
        "tell. Seeing it makes a ramp deliberate instead of accidental."
    ),
    "composure": (
        "The resting pulse of the practice: the calmer the reply, the more "
        "of the review actually happens. It rises as reading-first becomes "
        "the habit."
    ),
}

# Closed render copy — one line per state, integers only, scanned by the
# lexicon suite. The reading describes; it never grades the person.
CONDITIONING_COPY: dict[str, str] = {
    "capacity-rising": (
        "Your repeatable deep block is {value} minutes — up {delta} from the "
        "prior four weeks."
    ),
    "capacity-easing": (
        "Your repeatable deep block is {value} minutes — down {delta} from "
        "the prior four weeks. One protected block rebuilds it."
    ),
    "capacity-holding": (
        "Your repeatable deep block is {value} minutes, holding near the "
        "prior four weeks."
    ),
    "capacity-first": (
        "Your repeatable deep block is {value} minutes. First reading — the "
        "trend starts next window."
    ),
    "load-steady": (
        "This week is carrying {value}% of your four-week norm — inside the "
        "steady band, the pace a practice sustains."
    ),
    "load-hot": (
        "This week is running {value}% of your four-week norm. If the ramp "
        "is deliberate, plan the easier week that follows it."
    ),
    "load-light": (
        "This week sits at {value}% of your four-week norm — an easier "
        "stretch. Easing after a hard run is how gains consolidate."
    ),
    "composure-strong": (
        "{value}% of {moments} review moments got a deliberate response — "
        "read first, then answered. That patience is the practice."
    ),
    "composure-mid": (
        "{value}% of {moments} review moments got a deliberate response; "
        "the rest went back fast. A breath before the reply usually catches "
        "the one thing worth changing."
    ),
    "composure-watch": (
        "{value}% of {moments} review moments got a deliberate response. "
        "Fast replies and waved approvals are where the review step thins "
        "out."
    ),
}


@dataclass(frozen=True, slots=True)
class ConditioningIndicator:
    """One composite reading, render-ready. The meter geometry ships with the
    data (axis edge, steady band, prior-window marker) so no surface ever
    re-derives a band and drifts from the composer."""

    indicator_id: str
    label: str
    value: int
    unit: str  # "min" | "%"
    delta: int | None  # vs the prior window; None without a prior reading
    tone: str  # "watch" | "steady" | "base"
    reading: str
    measures: str
    why: str
    axis_max: int
    band_lo: int
    band_hi: int
    prior: int | None


def reliance_daily_metrics(
    marks_by_day: dict[str, list[MarkRow]],
) -> dict[str, FocusMetrics]:
    """Public alias of the per-day focus walk, so the reliance reading uses
    the exact same derivation as the conditioning readout rather than a second
    implementation that could drift from it."""
    return _daily_metrics(marks_by_day)


def _daily_metrics(
    marks_by_day: dict[str, list[MarkRow]],
) -> dict[str, FocusMetrics]:
    """One FocusMetrics per day — the same per-day walk the reflection band
    uses, so every composite here reads the exact counters the rest of the
    product already computes."""
    return {day: compute_metrics(marks) for day, marks in marks_by_day.items()}


def _capacity_minutes(daily: dict[str, FocusMetrics]) -> int | None:
    """Median of the top CAPACITY_TOP_DAYS daily longest blocks, or None when
    fewer than that many days had a block at all (too thin to call capacity)."""
    blocks = sorted(
        (m.longest_block_min for m in daily.values() if m.longest_block_min > 0),
        reverse=True,
    )
    if len(blocks) < CAPACITY_TOP_DAYS:
        return None
    return int(statistics.median(blocks[:CAPACITY_TOP_DAYS]))


def _active_minutes(marks: list[MarkRow]) -> int:
    """Distinct quarter-hour buckets touched, in minutes. Bucket counting is
    deliberately density-blind: a chatty agent loop and a quiet read of its
    output weigh the same fifteen minutes."""
    buckets = set()
    for mark in marks:
        ts = datetime.fromisoformat(mark[1])
        buckets.add((ts.date(), ts.hour, ts.minute // ACTIVE_BUCKET_MIN))
    return len(buckets) * ACTIVE_BUCKET_MIN


def _load_ratio_pct(
    marks_by_day: dict[str, list[MarkRow]],
    end_day: date,
    window_days: int,
) -> int | None:
    """Acute (last ACUTE_WINDOW_DAYS) vs chronic (whole window) daily-average
    active minutes, as an integer percent. None when the window is too sparse
    or empty for the ratio to mean anything."""
    if len(marks_by_day) < LOAD_MIN_ACTIVE_DAYS:
        return None
    minutes = {day: _active_minutes(marks) for day, marks in marks_by_day.items()}
    chronic_avg = sum(minutes.values()) // window_days
    if chronic_avg <= 0:
        return None
    acute_from = (end_day - timedelta(days=ACUTE_WINDOW_DAYS - 1)).isoformat()
    acute_avg = sum(v for d, v in minutes.items() if d >= acute_from) // (
        ACUTE_WINDOW_DAYS
    )
    return acute_avg * 100 // chronic_avg


def _composure_pct(daily: dict[str, FocusMetrics]) -> tuple[int, int] | None:
    """(deliberate_pct, review_moments) across the window, or None below the
    moments gate. Deliberate = neither a reflex follow-up nor a waved-through
    approval; both counters come from the same focus walk the tips read."""
    followups = sum(m.assistant_followups for m in daily.values())
    reflex = sum(m.reflex_replies for m in daily.values())
    approvals = sum(m.approval_moments for m in daily.values())
    waved = sum(m.waved_through for m in daily.values())
    moments = followups + approvals
    if moments < COMPOSURE_MIN_MOMENTS:
        return None
    hasty = min(reflex + waved, moments)
    return 100 - (hasty * 100 // moments), moments


def _capacity_indicator(value: int, prior: int | None) -> ConditioningIndicator:
    delta = value - prior if prior is not None else None
    if delta is None:
        copy_id = "capacity-first"
    elif delta >= CAPACITY_HOLD_BAND_MIN:
        copy_id = "capacity-rising"
    elif delta <= -CAPACITY_HOLD_BAND_MIN:
        copy_id = "capacity-easing"
    else:
        copy_id = "capacity-holding"
    tone = "steady" if copy_id == "capacity-rising" else "base"
    axis_top = max(CAPACITY_AXIS_MIN, value, prior or 0)
    axis_max = -(-axis_top // CAPACITY_AXIS_STEP) * CAPACITY_AXIS_STEP
    return ConditioningIndicator(
        indicator_id="capacity",
        label=INDICATOR_LABEL["capacity"],
        value=value,
        unit="min",
        delta=delta,
        tone=tone,
        reading=CONDITIONING_COPY[copy_id].format(
            value=value, delta=abs(delta) if delta is not None else 0
        ),
        measures=INDICATOR_MEASURES["capacity"],
        why=INDICATOR_WHY["capacity"],
        axis_max=axis_max,
        band_lo=CAPACITY_TARGET_MIN,
        band_hi=axis_max,
        prior=prior,
    )


def _load_indicator(value: int, prior: int | None) -> ConditioningIndicator:
    if value > LOAD_STEADY_MAX_PCT:
        copy_id, tone = "load-hot", "watch"
    elif value < LOAD_STEADY_MIN_PCT:
        copy_id, tone = "load-light", "base"
    else:
        copy_id, tone = "load-steady", "steady"
    return ConditioningIndicator(
        indicator_id="load_balance",
        label=INDICATOR_LABEL["load_balance"],
        value=value,
        unit="%",
        delta=value - prior if prior is not None else None,
        tone=tone,
        reading=CONDITIONING_COPY[copy_id].format(value=value),
        measures=INDICATOR_MEASURES["load_balance"],
        why=INDICATOR_WHY["load_balance"],
        axis_max=LOAD_AXIS_MAX_PCT,
        band_lo=LOAD_STEADY_MIN_PCT,
        band_hi=LOAD_STEADY_MAX_PCT,
        prior=prior,
    )


def _composure_indicator(
    value: int, moments: int, prior: int | None
) -> ConditioningIndicator:
    from practicegraph.report.format import count

    if value >= COMPOSURE_STRONG_MIN_PCT:
        copy_id, tone = "composure-strong", "steady"
    elif value > COMPOSURE_WATCH_MAX_PCT:
        copy_id, tone = "composure-mid", "base"
    else:
        copy_id, tone = "composure-watch", "watch"
    return ConditioningIndicator(
        indicator_id="composure",
        label=INDICATOR_LABEL["composure"],
        value=value,
        unit="%",
        delta=value - prior if prior is not None else None,
        tone=tone,
        reading=CONDITIONING_COPY[copy_id].format(
            value=value, moments=count(moments)
        ),
        measures=INDICATOR_MEASURES["composure"],
        why=INDICATOR_WHY["composure"],
        axis_max=100,
        band_lo=COMPOSURE_STRONG_MIN_PCT,
        band_hi=100,
        prior=prior,
    )


def compose_conditioning(
    current_by_day: dict[str, list[MarkRow]],
    prior_by_day: dict[str, list[MarkRow]],
    end_day: date,
    window_days: int = CONDITIONING_WINDOW_DAYS,
) -> list[ConditioningIndicator]:
    """The conditioning readout: up to three composite indicators from the
    current window, each with its prior-window marker when that window can be
    read. An indicator whose gate is not met is dropped — unavailable is never
    rendered as zero. Deterministic (INV-6): same marks, same readings, in the
    fixed INDICATORS order. Both mark dicts must already be interactive-gated
    (P3) and keyed by schedule-local ISO day, like every behavioral surface."""
    current = _daily_metrics(current_by_day)
    prior = _daily_metrics(prior_by_day)
    prior_end = end_day - timedelta(days=window_days)
    out: list[ConditioningIndicator] = []

    capacity_now = _capacity_minutes(current)
    if capacity_now is not None:
        out.append(_capacity_indicator(capacity_now, _capacity_minutes(prior)))

    load_now = _load_ratio_pct(current_by_day, end_day, window_days)
    if load_now is not None:
        out.append(
            _load_indicator(
                load_now, _load_ratio_pct(prior_by_day, prior_end, window_days)
            )
        )

    composure_now = _composure_pct(current)
    if composure_now is not None:
        value, moments = composure_now
        prior_read = _composure_pct(prior)
        out.append(
            _composure_indicator(
                value, moments, prior_read[0] if prior_read else None
            )
        )
    return out
