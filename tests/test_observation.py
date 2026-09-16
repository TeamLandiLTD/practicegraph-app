from __future__ import annotations

from dataclasses import replace

from practicegraph.analysis.focus import FocusMetrics, RhythmStats
from practicegraph.analysis.schedule import compatibility_utc_schedule
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.observation import CAVEAT, compose_observation


def _focus(**changes: int) -> FocusMetrics:
    base = FocusMetrics(0, 0, 0, 0, None, 0, 0)
    return replace(base, **changes)


def _rhythm(**changes: int | None) -> RhythmStats:
    base = RhythmStats(
        window_days=28,
        events_total=100,
        outside_preferred_hours_pct=5,
        quiet_hours_activity_pct=2,
        off_schedule_day_pct=0,
        long_streak_days=0,
        deep_block_days=0,
        active_days=10,
    )
    return replace(base, **changes)


def test_unconfirmed_schedule_yields_insufficient_observation() -> None:
    schedule = replace(compatibility_utc_schedule(), version=0, confirmed=False)
    result = compose_observation(focus=None, rhythm=None, schedule=schedule)
    assert result.observation_id == "insufficient"
    assert result.confidence == "insufficient"
    assert result.caveat == CAVEAT


def test_repeated_no_pause_pattern_has_priority() -> None:
    result = compose_observation(
        focus=_focus(longest_block_min=95, refire_replies=20),
        rhythm=_rhythm(long_streak_days=4, active_days=12),
        schedule=compatibility_utc_schedule(),
    )
    assert result.observation_id == "repeated-no-pause"
    assert result.confidence == "repeated pattern"
    assert "4 of the last 28 days" in result.body


def test_quiet_hours_needs_sample_gate() -> None:
    result = compose_observation(
        focus=None,
        rhythm=_rhythm(
            events_total=12,
            quiet_hours_activity_pct=50,
            long_streak_days=0,
        ),
        schedule=compatibility_utc_schedule(),
    )
    assert result.observation_id == "insufficient"


def test_positive_deep_blocks_are_observed_without_judgment() -> None:
    result = compose_observation(
        focus=None,
        rhythm=_rhythm(
            active_days=10,
            deep_block_days=6,
            long_streak_days=0,
            quiet_hours_activity_pct=2,
        ),
        schedule=compatibility_utc_schedule(),
    )
    assert result.observation_id == "protected-blocks"
    assert result.confidence == "repeated pattern"


def test_observation_copy_is_closed_clean_and_deterministic() -> None:
    first = compose_observation(
        focus=None,
        rhythm=_rhythm(quiet_hours_activity_pct=28),
        schedule=compatibility_utc_schedule(),
    )
    second = compose_observation(
        focus=None,
        rhythm=_rhythm(quiet_hours_activity_pct=28),
        schedule=compatibility_utc_schedule(),
    )
    assert first == second
    surface = " ".join(
        (first.observation_id, first.title, first.body, first.period, first.caveat)
    )
    assert lexicon_violations(surface) == []
    assert leak_findings(surface) == []
