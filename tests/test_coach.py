"""The practice check-in: working pattern, focus, discipline, craft — never a
health verdict. These pin the safety contract (all copy stays inside the
observational, non-clinical lexicon), that the check-in is composed
deterministically from local signals, and that the pillar logic surfaces the
right cue for the right state."""

from __future__ import annotations

import re

from practicegraph.analysis.focus import RhythmStats
from practicegraph.analysis.performance import DimensionReading
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.coach import (
    ACK_COPY,
    COACH_COPY,
    PILLAR_LABEL,
    PILLARS,
    CoachPillar,
    compose_coaching,
    training_load,
)


def _dr(dimension_id: str, score: int) -> DimensionReading:
    return DimensionReading(dimension_id, score, "level", ())


def _rhythm(**kw: int) -> RhythmStats:
    base = dict(
        window_days=28, events_total=1000, outside_preferred_hours_pct=8,
        quiet_hours_activity_pct=5, off_schedule_day_pct=5,
        long_streak_days=0, deep_block_days=20, active_days=25,
        waiting_minutes=100, distinct_projects=10, distinct_branches=10,
    )
    base.update(kw)
    return RhythmStats(**base)  # type: ignore[arg-type]


STRONG = (
    _dr("deep_work", 90), _dr("working_pattern", 85), _dr("single_threading", 85),
    _dr("context_hygiene", 85), _dr("model_economy", 85),
    _dr("execution_quality", 90),
)


def test_all_coaching_copy_is_non_clinical_and_clean() -> None:
    """Every line in the closed catalogs (cue, summary, measures) stays inside
    the observational-copy rules — no clinical/health vocabulary (FR-FOC-8), no
    leak markers. This is what keeps the view from ever reading as a diagnosis."""
    from practicegraph.report.coach import COACH_SUMMARY, PILLAR_MEASURES

    for text in (
        list(COACH_COPY.values())
        + list(COACH_SUMMARY.values())
        + list(PILLAR_MEASURES.values())
    ):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_no_surface_claims_to_see_rest_strain_or_sustainability() -> None:
    """The 2026-07-25 working-pattern rule, pinned so it cannot drift back.

    This pillar used to speak of recovery, sustainable load and "the pace that
    lasts". Felt load is precisely what timestamps cannot see — Mark et al.
    (CHI 2008) found interrupted work finished *faster* on the clock while
    stress, frustration and effort rose sharply. And Kluger & DeNisi's
    meta-analysis (607 effect sizes) makes the unfounded version worse than
    merely wrong: over a third of feedback interventions REDUCE performance,
    with the harm concentrated in feedback aimed at the self.

    So every line across the check-in, the brief and the dimension labels may
    describe when work landed and how continuously it ran, and nothing else."""
    from practicegraph.analysis.conditioning import (
        CONDITIONING_COPY,
        INDICATOR_LABEL,
        INDICATOR_MEASURES,
        INDICATOR_WHY,
    )
    from practicegraph.analysis.performance import DIMENSION_LABELS
    from practicegraph.report.brief import CELEBRATE_LEAD, FLAG_LEAD
    from practicegraph.report.coach import (
        ACK_COPY,
        COACH_SUMMARY,
        PILLAR_LABEL,
        PILLAR_MEASURES,
    )

    # Word-anchored: "the resting pulse of the practice" is a metaphor for
    # reply speed, not a claim about the person's state, and a substring scan
    # that trips on it would get relaxed rather than obeyed.
    unsupportable = (
        r"recovery", r"rest", r"rested", r"rests",
        r"sustainab", r"burnout", r"strain", r"wellbeing",
        r"well-being", r"exhaust", r"fatigue", r"grind",
        r"the pace that lasts", r"keeps the next block sharp",
    )
    catalogs = (
        list(COACH_COPY.values())
        + list(COACH_SUMMARY.values())
        + list(PILLAR_MEASURES.values())
        + list(PILLAR_LABEL.values())
        + list(ACK_COPY.values())
        + list(CELEBRATE_LEAD.values())
        + list(FLAG_LEAD.values())
        + list(DIMENSION_LABELS.values())
        # The conditioning strip is the same surface by another name — it is
        # where "strain accumulates" survived the first sweep, found only by
        # reading the live render.
        + list(INDICATOR_WHY.values())
        + list(INDICATOR_MEASURES.values())
        + list(INDICATOR_LABEL.values())
        + list(CONDITIONING_COPY.values())
    )
    for text in catalogs:
        low = text.lower()
        for claim in unsupportable:
            assert re.search(claim, low) is None, f"{claim!r} in {text!r}"


def test_each_pillar_carries_summary_cue_and_measures() -> None:
    """Every pillar has a short summary (shown by default), a full cue (hover),
    and a stable measures line — and the summary is shorter than the cue."""
    for p in compose_coaching(STRONG, _rhythm()):
        assert p.summary and p.cue and p.measures
        assert len(p.summary) <= len(p.cue)  # the summary is the short line
        assert lexicon_violations(p.summary) == []
        assert lexicon_violations(p.measures) == []


def test_coaching_uses_behavioral_label_and_noncausal_copy() -> None:
    from practicegraph.report.coach import COACH_SUMMARY

    assert PILLAR_LABEL["discipline"] == "Review & judgment"
    surface = " ".join(
        list(PILLAR_LABEL.values())
        + list(COACH_COPY.values())
        + list(COACH_SUMMARY.values())
        + list(ACK_COPY.values())
    ).lower()
    assert "discipline" not in surface
    for fragment in (
        "is showing",
        "are doing their work",
        "is paying off",
        "caused",
        "because of the cue",
        "the cue worked",
    ):
        assert fragment not in surface


def test_one_pillar_each_and_deterministic() -> None:
    first = compose_coaching(STRONG, _rhythm())
    second = compose_coaching(STRONG, _rhythm())
    assert [(p.pillar, p.tone, p.cue) for p in first] == [
        (p.pillar, p.tone, p.cue) for p in second
    ]
    assert {p.pillar for p in first} == set(PILLARS)
    assert len(first) == len(PILLARS)
    for p in first:
        assert isinstance(p, CoachPillar)
        assert p.tone in {"watch", "steady", "base"}
        assert lexicon_violations(p.cue) == []


def test_watch_items_lead_then_steady() -> None:
    """A coach leads with what to work on: watch pillars come before steady/base.
    Here single_threading is weak (focus → watch) while the rest are strong."""
    readings = (
        _dr("deep_work", 90), _dr("working_pattern", 85), _dr("single_threading", 40),
        _dr("context_hygiene", 85), _dr("model_economy", 85),
        _dr("execution_quality", 90),
    )
    pillars = compose_coaching(readings, _rhythm())
    tones = [p.tone for p in pillars]
    # No steady/base appears before a watch.
    first_non_watch = next(
        (i for i, t in enumerate(tones) if t != "watch"), len(tones)
    )
    assert all(t == "watch" for t in tones[:first_non_watch])
    focus = next(p for p in pillars if p.pillar == "focus")
    assert focus.tone == "watch"


def test_long_unbroken_stretches_drive_the_pattern_watch() -> None:
    """Long no-pause stretches make the working pattern a watch pillar with the
    stretch cue, regardless of its score — this is a rhythm signal, not a score.

    The cue states the count and stops: after 2026-07-25 no line here may claim
    rest, sustainability, or "the pace that lasts", because felt load is exactly
    what timestamps cannot see."""
    pillars = compose_coaching(STRONG, _rhythm(long_streak_days=8))
    pattern = next(p for p in pillars if p.pillar == "pattern")
    assert pattern.tone == "watch"
    assert "no 15-minute gap" in pattern.cue
    # And a late-night-heavy window also flags the pattern pillar.
    late = compose_coaching(STRONG, _rhythm(quiet_hours_activity_pct=30))
    rec_late = next(p for p in late if p.pillar == "pattern")
    assert rec_late.tone == "watch"


def test_weak_dimensions_become_watch_cues() -> None:
    weak = (
        _dr("deep_work", 50), _dr("working_pattern", 85), _dr("single_threading", 50),
        _dr("context_hygiene", 45), _dr("model_economy", 45),
        _dr("execution_quality", 40),
    )
    pillars = {p.pillar: p for p in compose_coaching(weak, _rhythm())}
    assert pillars["focus"].tone == "watch"
    assert pillars["discipline"].tone == "watch"
    assert pillars["craft"].tone == "watch"


def test_strong_dimensions_read_steady() -> None:
    pillars = {p.pillar: p for p in compose_coaching(STRONG, _rhythm())}
    assert pillars["discipline"].tone == "steady"
    assert pillars["craft"].tone == "steady"
    assert pillars["focus"].tone == "steady"


def test_no_signal_is_base_not_a_verdict() -> None:
    """With no dimensions and no rhythm, every pillar gives a neutral base cue —
    the coach never invents a reading from nothing."""
    pillars = compose_coaching((), None)
    assert len(pillars) == len(PILLARS)
    for p in pillars:
        assert p.tone == "base"
        assert p.score == -1


def test_training_load_readings() -> None:
    assert training_load(None) == "unknown"
    assert training_load(_rhythm(active_days=0)) == "unknown"
    assert training_load(_rhythm(long_streak_days=8)) == "heavy"
    assert training_load(_rhythm(quiet_hours_activity_pct=25)) == "heavy"
    assert training_load(_rhythm(active_days=4)) == "light"
    assert training_load(_rhythm()) == "steady"
