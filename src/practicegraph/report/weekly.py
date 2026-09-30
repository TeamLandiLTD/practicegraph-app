"""The weekly reading (A4): the Monday ritual.

Last week (Mon-Sun) set against the week before it — one theme, a few
week-over-week lines, the coach's acknowledgment if there is one, and a single
focus for the week ahead. Composed entirely from the per-day aggregates the rest
of the product already builds (spend series + the behavioral day metrics); it
walks no marks anew and stores nothing.

Two rules keep it honest (PRINCIPLES.md, FR-FOC-8):
- **Withheld, not zero.** No reading at all unless BOTH weeks have real activity
  (>= WEEKLY_MIN_ACTIVE_DAYS active days each). Thin data yields None, never a
  hollow reading. And a week-over-week line renders only when both weeks carry
  the metric (the sample-gate pattern from report/reflections.py) — no gate, no
  line.
- **Observational, never a verdict.** The lines are closed copy describing the
  change in integers; the person reads the meaning. No streak, no completion %,
  no "N weeks in a row" — that is the dark pattern PRINCIPLES.md forbids.

Determinism (INV-6): integer math, sorted iteration, closed templates, and
"today" passed in (the Monday/Tuesday show gate is computed here, never from a
clock the client reads). Local-only forever (NFR-PRV-6) — the reading rides the
view model, never an emit.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from practicegraph.analysis.performance import DayBehavior, behavior_by_day
from practicegraph.analysis.schedule import ScheduleProfile, compatibility_utc_schedule
from practicegraph.report.coach import (
    AckLine,
    compose_acknowledgment,
    current_pillars,
)
from practicegraph.report.format import count, usd
from practicegraph.store import ContributionRow, Store

# A week counts as readable only past this many active days — both weeks must
# clear it before any reading is composed (withheld, not zero).
WEEKLY_MIN_ACTIVE_DAYS = 3

# The reading shows in-app as the top card only early in the week (the Monday
# ritual, with Tuesday grace); otherwise it lives behind a quiet link. Computed
# server-side so the client never reads the clock.
WEEKLY_SHOW_WEEKDAYS = (0, 1)  # Monday, Tuesday

# Closed week-over-week copy (lexicon-scanned; NFR-QLT-3). Each line compares two
# integers, last week against the week before. Observational — it reports the
# move, it does not grade it.
WEEKLY_COPY: dict[str, str] = {
    "spend": (
        "Spend last week was about {this}, against {prior} the week before."
    ),
    # W1.4 capability-per-dollar: the unit-economics move, week over week.
    "per_turn": (
        "Each priced turn cost about {this} last week, against {prior} the "
        "week before."
    ),
    "late": (
        "Late-night stretches: {this} last week, {prior} the week before."
    ),
    "deepblock": (
        "Your longest uninterrupted block reached {this} minutes last week; "
        "the week before it was {prior}."
    ),
    "switches": (
        "Session switches came to {this} last week, next to {prior} the week "
        "before."
    ),
}

WEEKLY_MAX_LINES = 5
WEEKLY_MIN_LINES = 3  # documented target floor; a thin-but-eligible week may
#                       carry fewer if metrics are absent in one week — that is
#                       the sample gate doing its job, not a hollow reading.


@dataclasses.dataclass(frozen=True, slots=True)
class WeeklyReading:
    """The composed reading, render-ready. `lines` are the week-over-week
    sentences (closed copy, integers filled); `theme_pillar`/`next_focus` name
    and cue the week's focus; `ack` is A3's acknowledgment when present;
    `show_now` is True only Monday/Tuesday (computed server-side).
    `advice_note` (AM-4) is the Advisor's self-audit line when one exists —
    the product's receipts for its own advice, riding the Monday ritual."""

    theme_pillar: str
    theme_label: str
    lines: tuple[str, ...]
    next_focus: str
    ack: AckLine | None
    show_now: bool
    advice_note: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class _WeekMetrics:
    """One week's summed aggregates (all per-day sources, no fresh mark walk)."""

    active_days: int
    cost_micro_usd: int
    priced_turns: int
    quiet_hours_events: int | None
    longest_block_min: int
    switches: int


def _week_bounds(today: date) -> tuple[date, date, date, date]:
    """(last_mon, last_sun, prev_mon, prev_sun) — the two full Mon-Sun weeks
    ending with the most recent complete week before `today`'s week."""
    this_monday = today - timedelta(days=today.weekday())
    last_sun = this_monday - timedelta(days=1)
    last_mon = last_sun - timedelta(days=6)
    prev_sun = last_mon - timedelta(days=1)
    prev_mon = prev_sun - timedelta(days=6)
    return last_mon, last_sun, prev_mon, prev_sun


def _week_metrics(
    start: date,
    end: date,
    behavior: dict[str, DayBehavior],
    spend_by_day: dict[str, int],
    priced_turns_by_day: dict[str, int],
) -> _WeekMetrics:
    """Sum one Mon-Sun week from already-computed per-day aggregates."""
    active = 0
    cost = 0
    priced = 0
    late = 0
    quiet_hours_known = True
    longest = 0
    switches = 0
    day = start
    while day <= end:
        key = day.isoformat()
        day_cost = spend_by_day.get(key, 0)
        beh = behavior.get(key)
        if day_cost > 0 or beh is not None:
            active += 1
        cost += day_cost
        priced += priced_turns_by_day.get(key, 0)
        if beh is not None:
            if beh.quiet_hours_events is None:
                quiet_hours_known = False
            else:
                late += beh.quiet_hours_events
            longest = max(longest, beh.longest_block_min)
            switches += beh.switches
        day += timedelta(days=1)
    return _WeekMetrics(
        active_days=active,
        cost_micro_usd=cost,
        priced_turns=priced,
        quiet_hours_events=late if quiet_hours_known else None,
        longest_block_min=longest,
        switches=switches,
    )


def _lines(this_week: _WeekMetrics, prev_week: _WeekMetrics) -> list[str]:
    """The week-over-week lines, each rendered ONLY when both weeks carry the
    metric (the sample gate). Ordered spend, late, deep block, switches; capped."""
    lines: list[str] = []
    # Spend: both weeks priced something.
    if this_week.cost_micro_usd > 0 and prev_week.cost_micro_usd > 0:
        lines.append(WEEKLY_COPY["spend"].format(
            this=usd(this_week.cost_micro_usd), prior=usd(prev_week.cost_micro_usd)))
    # Capability per dollar (W1.4): both weeks priced turns AND spend.
    if (
        this_week.cost_micro_usd > 0
        and prev_week.cost_micro_usd > 0
        and this_week.priced_turns > 0
        and prev_week.priced_turns > 0
    ):
        lines.append(WEEKLY_COPY["per_turn"].format(
            this=usd(this_week.cost_micro_usd // this_week.priced_turns, 4),
            prior=usd(prev_week.cost_micro_usd // prev_week.priced_turns, 4)))
    # Late-night: both weeks had at least one late stretch (else nothing to
    # compare, and we never manufacture a "0 vs 0" line).
    if (
        this_week.quiet_hours_events is not None
        and prev_week.quiet_hours_events is not None
        and this_week.quiet_hours_events > 0
        and prev_week.quiet_hours_events > 0
    ):
        lines.append(WEEKLY_COPY["late"].format(
            this=count(this_week.quiet_hours_events), prior=count(prev_week.quiet_hours_events)))
    # Longest block: both weeks recorded a block.
    if this_week.longest_block_min > 0 and prev_week.longest_block_min > 0:
        lines.append(WEEKLY_COPY["deepblock"].format(
            this=count(this_week.longest_block_min),
            prior=count(prev_week.longest_block_min)))
    # Switches: both weeks switched sessions at least once.
    if this_week.switches > 0 and prev_week.switches > 0:
        lines.append(WEEKLY_COPY["switches"].format(
            this=count(this_week.switches), prior=count(prev_week.switches)))
    return lines[:WEEKLY_MAX_LINES]


def compose_weekly(
    store: Store,
    today: date,
    schedule: ScheduleProfile | None = None,
    advice_audit: str = "",
) -> WeeklyReading | None:
    """The weekly reading for the two full weeks before `today`, or None when
    either week is too thin (withheld, not zero). Deterministic and local-only;
    composed on read, persists nothing. `advice_audit` is the Advisor's
    already-composed self-audit line (AM-4) — passed in so one page never
    derives the same number twice; "" when there is nothing to audit.
    """
    schedule = schedule or compatibility_utc_schedule()
    last_mon, last_sun, prev_mon, prev_sun = _week_bounds(today)
    # One behavior pass over the whole two-week span, plus the spend series —
    # the existing per-day aggregates, summed here (no fresh mark walk).
    behavior = behavior_by_day(store, prev_mon, last_sun, schedule)
    spend_by_day = {
        row[0]: int(row[1])
        for row in store.spend_series(prev_mon.isoformat(), last_sun.isoformat())
    }
    priced_turns_by_day: dict[str, int] = {}
    for day_key, _tool, _model, values in store.usage_rows_by_day(
        prev_mon.isoformat(), last_sun.isoformat()
    ):
        row = ContributionRow.from_values(values)
        priced_turns_by_day[day_key] = (
            priced_turns_by_day.get(day_key, 0)
            + row.assistant_turns
            - row.unpriced_turns
        )
    this_week = _week_metrics(
        last_mon, last_sun, behavior, spend_by_day, priced_turns_by_day
    )
    prev_week = _week_metrics(
        prev_mon, prev_sun, behavior, spend_by_day, priced_turns_by_day
    )
    if (this_week.active_days < WEEKLY_MIN_ACTIVE_DAYS
            or prev_week.active_days < WEEKLY_MIN_ACTIVE_DAYS):
        return None
    lines = _lines(this_week, prev_week)
    # Theme + next focus: the watch-first pillar and its cue, reused from the
    # coach's own gather (not re-derived). None-safe: no activity to score yet
    # means no theme, so no reading.
    pillars = current_pillars(store, today, schedule)
    if not pillars:
        return None
    lead = pillars[0]
    return WeeklyReading(
        theme_pillar=lead.pillar,
        theme_label=lead.label,
        lines=tuple(lines),
        next_focus=lead.summary,
        ack=compose_acknowledgment(store, today),
        show_now=today.weekday() in WEEKLY_SHOW_WEEKDAYS,
        advice_note=advice_audit,
    )
