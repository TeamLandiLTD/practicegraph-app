"""One deterministic, confidence-qualified work-pattern observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from practicegraph.analysis.focus import FocusMetrics, RhythmStats
from practicegraph.analysis.schedule import ScheduleProfile

Confidence = Literal["insufficient", "early pattern", "repeated pattern"]
CAVEAT = "A work-pattern observation, not a health assessment."
QUIET_HOURS_MIN_EVENTS = 30
QUIET_HOURS_MIN_PCT = 20
REPEATED_NO_PAUSE_DAYS = 3
REPEATED_DEEP_BLOCK_DAYS = 3
REFIRE_MIN_COUNT = 15


@dataclass(frozen=True, slots=True)
class PracticeObservation:
    observation_id: str
    title: str
    body: str
    period: str
    confidence: Confidence
    caveat: str = CAVEAT


def _insufficient(body: str, period: str = "last 28 days") -> PracticeObservation:
    return PracticeObservation(
        observation_id="insufficient",
        title="No repeated pattern yet",
        body=body,
        period=period,
        confidence="insufficient",
    )


def compose_observation(
    focus: FocusMetrics | None,
    rhythm: RhythmStats | None,
    schedule: ScheduleProfile,
) -> PracticeObservation:
    """Choose one observation by a closed priority and fixed sample gates."""
    if not schedule.confirmed:
        return _insufficient(
            "Confirm your working and quiet hours before schedule-based "
            "observations are shown."
        )
    if rhythm is None or rhythm.events_total == 0:
        return _insufficient(
            "There is not enough local work history to describe a repeated pattern."
        )
    if rhythm.long_streak_days >= REPEATED_NO_PAUSE_DAYS:
        return PracticeObservation(
            observation_id="repeated-no-pause",
            title="Long stretches without a pause are repeating",
            body=(
                f"A two-hour stretch without a 15-minute pause appeared on "
                f"{rhythm.long_streak_days} of the last {rhythm.window_days} days."
            ),
            period=f"last {rhythm.window_days} days",
            confidence="repeated pattern",
        )
    if (
        rhythm.events_total >= QUIET_HOURS_MIN_EVENTS
        and rhythm.quiet_hours_activity_pct is not None
        and rhythm.quiet_hours_activity_pct >= QUIET_HOURS_MIN_PCT
    ):
        return PracticeObservation(
            observation_id="quiet-hours-activity",
            title="Activity is reaching your quiet hours",
            body=(
                f"{rhythm.quiet_hours_activity_pct}% of {rhythm.events_total} "
                "recent events landed during the quiet hours you confirmed."
            ),
            period=f"last {rhythm.window_days} days",
            confidence="repeated pattern",
        )
    if focus is not None and focus.refire_replies >= REFIRE_MIN_COUNT:
        return PracticeObservation(
            observation_id="refire-after-failure",
            title="Quick retries followed several failed runs",
            body=(
                f"A follow-up arrived within five minutes of a failed run "
                f"{focus.refire_replies} times on this local day."
            ),
            period="this local day",
            confidence="early pattern",
        )
    if rhythm.deep_block_days >= REPEATED_DEEP_BLOCK_DAYS:
        return PracticeObservation(
            observation_id="protected-blocks",
            title="Protected blocks are repeating",
            body=(
                f"A 45-minute uninterrupted block appeared on "
                f"{rhythm.deep_block_days} of the last {rhythm.window_days} days."
            ),
            period=f"last {rhythm.window_days} days",
            confidence="repeated pattern",
        )
    return _insufficient(
        "The available history has not cleared a repeated-pattern sample gate."
    )
