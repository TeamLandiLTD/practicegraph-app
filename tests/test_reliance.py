"""How you worked with it — the collaboration-shape reading.

The load-bearing tests here are the ones that pin what this reading may NOT
say: no score, no over/under-reliance label (that needs per-instance ground
truth logs cannot supply), and no sentence aimed at the person rather than the
work. The rest pins the sample gates, the two-sided framing, and determinism.
"""

from __future__ import annotations

import dataclasses
import json

from practicegraph.analysis.focus import FocusMetrics
from practicegraph.analysis.reliance import (
    RELIANCE_COPY,
    RELIANCE_MIN_FACETS,
    RELIANCE_MIN_HANDOFFS,
    RELIANCE_MIN_MOMENTS,
    RELIANCE_MIN_REWORK_OBSERVED,
    RELIANCE_MIN_TOOL_CALLS,
    compose_reliance,
)
from practicegraph.privacy import leak_findings, lexicon_violations

WINDOW = 28


def _metrics(
    *,
    events: int = 0,
    followups: int = 0,
    reflex: int = 0,
    approvals: int = 0,
    waved: int = 0,
) -> FocusMetrics:
    return FocusMetrics(
        longest_block_min=0,
        max_concurrent_sessions=0,
        switch_count=0,
        longest_streak_min=0,
        first_hour_utc=None,
        burst_windows=0,
        event_count=events,
        reflex_replies=reflex,
        assistant_followups=followups,
        refire_replies=0,
        approval_moments=approvals,
        waved_through=waved,
    )


def _daily(**kwargs: int) -> dict[str, FocusMetrics]:
    """One day carrying the whole window's counters — the composer sums, so
    the distribution across days is irrelevant to what is being pinned."""
    return {"2026-07-01": _metrics(**kwargs)}  # type: ignore[arg-type]


def _reading(**over: object):
    args: dict[str, object] = {
        "daily": _daily(events=800, followups=60, reflex=15, approvals=40, waved=5),
        "marks_total": 1000,
        "marks_interactive": 530,
        "waiting_minutes": 3798,
        "rework_edits": 400,
        "tool_calls": 2000,
        "window_days": WINDOW,
    }
    args.update(over)
    return compose_reliance(**args)  # type: ignore[arg-type]


def test_the_shape_reads_as_three_facets_with_exact_arithmetic() -> None:
    reading = _reading()
    assert reading.available is True
    assert reading.window_days == WINDOW
    by_id = {f.facet_id: f for f in reading.facets}
    assert set(by_id) == {"grain", "engagement", "shaping"}
    # Grain: 800 turns over 40 handoffs = 20 per handoff.
    assert "About 20 activity events per recorded handoff" in by_id["grain"].line
    assert "across 40 handoffs" in by_id["grain"].line
    # Engagement: 100 moments, 20 hasty -> 80% considered.
    assert "80% of 100 recorded response moments" in by_id["engagement"].line
    # Shaping: 400 of 2,000 = 20%.
    assert "Revision flags amounted to 20% of 2,000 recorded tool calls" in by_id["shaping"].line
    # Job-shape headline: 470 of 1,000 marks were unattended = 47%.
    assert "47% of recorded activity marks were noninteractive" in reading.headline
    assert "63h 18m" in reading.headline


def test_each_facet_is_withheld_below_its_own_gate() -> None:
    """Withheld, not zero — and never a guess from a thin sample."""
    thin_handoffs = _reading(
        daily=_daily(
            events=800,
            followups=60,
            reflex=15,
            approvals=RELIANCE_MIN_HANDOFFS - 1,
            waved=5,
        )
    )
    assert "grain" not in {f.facet_id for f in thin_handoffs.facets}

    thin_moments = _reading(
        daily=_daily(events=800, followups=1, reflex=0, approvals=20, waved=0)
    )
    assert RELIANCE_MIN_MOMENTS > 1 + 20
    assert "engagement" not in {f.facet_id for f in thin_moments.facets}

    thin_calls = _reading(tool_calls=RELIANCE_MIN_TOOL_CALLS - 1)
    assert "shaping" not in {f.facet_id for f in thin_calls.facets}


def test_an_unobserved_review_channel_is_withheld_not_reported_as_zero() -> None:
    """The bug this facet was built to avoid, pinned.

    Rework arrives from Claude Code's `toolUseResult.userModified` (IDE diff
    flow only) and Codex's rejected `patch_apply_end`. A CLI user emits
    neither: on 113 days of real history the channel produced 4 events in
    47,621 tool calls, with `userModified` false in all 5,518 observations.
    Rendering "0% of tool results were revised" there would state as a finding
    something the logs cannot see — and it is the harshest possible reading of
    a person. Below the floor the facet is withheld."""
    dead_channel = _reading(rework_edits=RELIANCE_MIN_REWORK_OBSERVED - 1)
    assert "shaping" not in {f.facet_id for f in dead_channel.facets}
    # The same volume with a live channel renders normally.
    live_channel = _reading(rework_edits=RELIANCE_MIN_REWORK_OBSERVED)
    assert "shaping" in {f.facet_id for f in live_channel.facets}


def test_an_all_attended_window_still_reads_as_a_shape() -> None:
    """No unattended agent runs is itself a shape, and early history is full
    of such windows. The headline frames the facets; it never gates them."""
    attended = _reading(marks_total=1000, marks_interactive=1000)
    assert attended.available is True
    assert len(attended.facets) == 3
    assert "Every recorded activity mark was interactive" in attended.headline
    assert "%" not in attended.headline


def test_a_single_number_is_not_a_shape() -> None:
    """One facet is a factoid; the reading only means something as a shape, so
    it is withheld entirely below the facet floor."""
    assert RELIANCE_MIN_FACETS == 2
    # Grain clears its gate; engagement (13 moments) and shaping do not.
    one_facet = _reading(
        daily=_daily(events=800, followups=1, reflex=0, approvals=12, waved=0),
        tool_calls=RELIANCE_MIN_TOOL_CALLS - 1,
    )
    assert one_facet.available is False
    assert one_facet.facets == ()
    assert one_facet.headline == ""


def test_the_reading_never_scores_or_labels_reliance() -> None:
    """The hard constraint. Labelling reliance over- or under- requires knowing
    whether the AI was right in each instance; content-blind logs cannot supply
    that (ACM Computing Surveys 2025). Reliance also fails in both directions,
    so neither tail may be framed as a fault."""
    reading = _reading()
    blob = json.dumps(dataclasses.asdict(reading)).lower()
    for banned in (
        "over-relian", "overrelian", "under-relian", "underrelian",
        "score", "grade", "rating", "too much", "too little",
        "should", "must ", "failing", "poor", "bad ", "worse",
    ):
        assert banned not in blob, banned
    # Both tails are named as legitimate, not one as correct.
    grain = next(f for f in reading.facets if f.facet_id == "grain")
    assert "Different tools and interaction styles" in grain.why


def test_copy_points_at_the_work_not_the_person() -> None:
    """Kluger & DeNisi: >1/3 of feedback interventions reduce performance, and
    the harm mechanism is feedback directed at the self. Every line here talks
    about sessions, handoffs and output."""
    for text in RELIANCE_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
        low = text.lower()
        for about_the_self in (
            "you tend", "you always", "you never", "you failed",
            "your habit", "your discipline", "your attention",
        ):
            assert about_the_self not in low, text


def test_double_compose_is_identical() -> None:
    assert _reading() == _reading()


def test_a_rounded_zero_revision_rate_reads_as_a_count_not_nothing() -> None:
    """40 hand edits across 20,000 results is under one percent - and "0%"
    on the face would say the person changed nothing, which is false. The
    count is the honest sentence there (COPY_RULES rule 1: meaning first)."""
    from practicegraph.analysis.reliance import _shaping

    facet = _shaping(rework_edits=40, tool_calls=20_000, prior_rework=0, prior_calls=0)
    assert facet is not None
    assert "40 revision flags across 20,000 tool calls" in facet.line
    assert "0%" not in facet.line
    assert "under 1%" in facet.line
    # A real rate still reads as a rate.
    rate = _shaping(rework_edits=400, tool_calls=2_000, prior_rework=0, prior_calls=0)
    assert rate is not None and "Revision flags amounted to 20%" in rate.line
