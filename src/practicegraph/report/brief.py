"""The daily brief (FR-RPT-1 Today view, redesigned): a deterministic
verdict assembled from closed sentence templates over the performance
profile — what changed, what is prominent in the month, and the one
change worth making tomorrow.

No free text is ever generated: every sentence is a fixed template from the
catalogs below filled with integers, so the brief is byte-deterministic
(INV-6) and the whole surface passes the lexicon and leak scans like every
other copy catalog (FR-FOC-8, NFR-QLT-3). The positive sentence always
leads — FR-FOC-2's rule, promoted to the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from practicegraph.analysis.aggregate import DailySnapshot
from practicegraph.analysis.performance import (
    STRONG_MIN_SCORE,
    DimensionReading,
    PerformanceProfile,
    WindowInputs,
)
from practicegraph.history import DayPoint, WeekOverWeek
from practicegraph.report.format import usd

# ---- closed sentence catalogs (NFR-QLT-3: wording changes are reviewed) ------

CELEBRATE_LEAD: dict[str, str] = {
    "deep_work": "Deep work is carrying the month",
    "working_pattern": "The day held its shape",
    "single_threading": "Attention continuity held",
    "context_hygiene": "Context reuse stayed consistent",
    "model_economy": "Model routing stayed economical",
    "execution_quality": "Runs are landing clean",
}

FLAG_LEAD: dict[str, str] = {
    "deep_work": "Deep blocks were scarce",
    "working_pattern": "Long unbroken stretches are the pattern",
    "single_threading": "Cross-workstream movement is the pattern to watch",
    "context_hygiene": "Oversized context is the cheapest fix",
    "model_economy": "Premium routing is the costliest habit",
    "execution_quality": "Failing commands are the friction",
}

# One executable next step per flagged dimension. Timer verbs dispatch through
# the practicegraph: protocol (the shell runs them; this page stays static).
ACTIONS: dict[str, tuple[str, str]] = {
    "deep_work": ("Start a 90-minute one-task block", "practicegraph:timer-focus"),
    "working_pattern": (
        "Take a 10-minute break",
        "practicegraph:timer-break",
    ),
    "single_threading": (
        "Checkpoint one thread before moving",
        "practicegraph:timer-focus",
    ),
    "context_hygiene": ("Review the working set before the next session", "#insights"),
    "model_economy": ("Route routine work to a mid-tier model", "#insights"),
    "execution_quality": ("Read the failing step before the retry", "#insights"),
}

ALL_STRONG_ACTION: tuple[str, str] = (
    "Keep it going - protect tomorrow's first block",
    "practicegraph:timer-focus",
)

# Change-queue entry for the switching pattern (no matching FR-RPT-8
# suggestion exists — this is behavioral, so it carries no dismiss id).
QUEUE_SWITCHING_COPY: tuple[str, str] = (
    "Checkpoint one thread before moving",
    "A short handoff note makes it easier to return to unfinished work.",
)

# Cost typicality vs your own last 28 active days (quartile bands).
COST_LIGHT = "a light day by your own range"
COST_TYPICAL = "a typical day for you"
COST_HEAVY = "a heavy day by your own range"

SWITCH_MULTIPLE_MIN_TENTHS = 15  # phrase appears from 1.5x upward
SWITCH_MULTIPLE_CAP_TENTHS = 99  # beyond ~10x a number stops being credible
MIN_ACTIVE_DAYS_FOR_TYPICALITY = 4


@dataclass(frozen=True, slots=True)
class Brief:
    cost_line: str
    celebrate: str
    flag: str | None
    action_label: str
    action_href: str
    weekly_line: str | None  # Monday review (weekly cadence, FR-ANL-7)


def _sentence(lead: str, reading: DimensionReading, extra: str = "") -> str:
    return f"{lead} - {', '.join(reading.evidence)}{extra}."


def _switch_multiple_tenths(weekly: list[WindowInputs]) -> int | None:
    """This week's daily switch rate vs the median of prior active weeks,
    in tenths (31 -> 3.1x). None without a meaningful baseline."""
    if len(weekly) < 3:
        return None
    current = weekly[-1]
    if not current.attention_confident or current.attention_active_days == 0:
        return None
    prior_rates = sorted(
        window.attention_high_switch_days * 10 // window.attention_active_days
        for window in weekly[:-1]
        if window.attention_confident and window.attention_active_days > 0
    )
    if not prior_rates:
        return None
    baseline = prior_rates[len(prior_rates) // 2]
    if baseline <= 0:
        return None
    current_rate = (
        current.attention_high_switch_days * 10 // current.attention_active_days
    )
    return current_rate * 10 // baseline


def _cost_line(snapshot: DailySnapshot, costs: list[DayPoint]) -> str:
    label = usd(snapshot.total_cost_micro_usd) + " est. today"
    active = sorted(p.cost_micro_usd for p in costs if p.cost_micro_usd > 0)
    if len(active) < MIN_ACTIVE_DAYS_FOR_TYPICALITY:
        return label
    p25 = active[len(active) // 4]
    p75 = active[(3 * len(active)) // 4]
    today = snapshot.total_cost_micro_usd
    if today < p25:
        band = COST_LIGHT
    elif today > p75:
        band = COST_HEAVY
    else:
        band = COST_TYPICAL
    return f"{label} - {band}"


def _signed(pct: int) -> str:
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


def build_brief(
    snapshot: DailySnapshot,
    profile: PerformanceProfile | None,
    weekly: list[WindowInputs],
    costs: list[DayPoint],
    wow: WeekOverWeek | None,
    day: date,
) -> Brief | None:
    """Deterministic assembly; None until a profile exists (honest empty
    state stays with the renderer)."""
    if profile is None:
        return None
    scored = [reading for reading in profile.readings if not reading.neutral]
    if not scored:
        return None

    best = max(scored, key=lambda r: r.score)
    celebrate = _sentence(CELEBRATE_LEAD[best.dimension_id], best)

    worst = min(scored, key=lambda r: r.score)
    flag: str | None = None
    action_label, action_href = ALL_STRONG_ACTION
    if worst.score < STRONG_MIN_SCORE and worst.dimension_id != best.dimension_id:
        extra = ""
        if worst.dimension_id == "single_threading":
            tenths = _switch_multiple_tenths(weekly)
            if tenths is not None and tenths > SWITCH_MULTIPLE_CAP_TENTHS:
                extra = ", many times your usual weekly pace"
            elif tenths is not None and tenths >= SWITCH_MULTIPLE_MIN_TENTHS:
                extra = (
                    f", about {tenths // 10}.{tenths % 10}x your usual weekly pace"
                )
        flag = _sentence(FLAG_LEAD[worst.dimension_id], worst, extra)
        action_label, action_href = ACTIONS[worst.dimension_id]

    weekly_line: str | None = None
    if day.weekday() == 0 and wow is not None:
        weekly_line = (
            f"Weekly review: est. cost {_signed(wow.cost_delta_pct)} and tokens "
            f"{_signed(wow.tokens_delta_pct)} vs the prior week."
        )

    return Brief(
        cost_line=_cost_line(snapshot, costs),
        celebrate=celebrate,
        flag=flag,
        action_label=action_label,
        action_href=action_href,
        weekly_line=weekly_line,
    )
