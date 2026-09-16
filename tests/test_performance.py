"""Human-performance profile: closed dimension vocabulary, deterministic
integer scoring, copy discipline (FR-FOC-8), and the never-on-wire stance
shared with every focus surface (NFR-PRV-6)."""

from __future__ import annotations

from dataclasses import replace

from conftest import FIXTURE_DAY, build_fixture_history
from practicegraph.analysis.performance import (
    DIMENSION_IDS,
    DIMENSION_LABELS,
    NEUTRAL_SCORE,
    PERFORMANCE_WINDOW_DAYS,
    TRAJECTORY_WEEKS,
    WindowInputs,
    compute_performance,
    dimension_scores,
    gather_performance,
    level_for,
    weekly_windows,
    window_inputs_from_store,
)
from practicegraph.analysis.schedule import compatibility_utc_schedule
from practicegraph.privacy import leak_findings, lexicon_violations


def _window(**overrides: int) -> WindowInputs:
    base: dict[str, int] = {
        "window_days": PERFORMANCE_WINDOW_DAYS,
        "active_days": 20,
        "deep_block_days": 12,
        "longest_block_min": 148,
        "quiet_hours_activity_pct": 8,
        "no_pause_days": 3,
        "switch_count": 40,
        "parallel_days": 2,
        "burst_windows": 10,
        "breaks_completed": 4,
        "cached_tokens": 8_000_000,
        "fresh_input_tokens": 1_500_000,
        "assistant_turns": 900,
        "premium_cost_micro_usd": 4_000_000,
        "priced_cost_micro_usd": 9_000_000,
        "unpriced_turns": 0,
        "tool_calls": 1200,
        "rework_edits": 60,
        "commands_run": 300,
        "commands_failed": 30,
        "commands_slow": 15,
        "attention_active_days": 20,
        "attention_human_events": 100,
        "attention_known_events": 100,
        "attention_known_coverage_pct": 100,
        "attention_switch_count": 16,
        "attention_high_switch_days": 4,
        "attention_coordination_windows": 3,
        "attention_coordination_days": 2,
        "attention_prompt_burst_windows": 3,
        "attention_prompt_burst_days": 3,
        "attention_confident": True,
    }
    base.update(overrides)
    return WindowInputs(**base)


def test_dimension_catalog_is_pinned() -> None:
    assert DIMENSION_IDS == (
        "deep_work",
        "working_pattern",
        "single_threading",
        "context_hygiene",
        "model_economy",
        "execution_quality",
    )
    assert set(DIMENSION_LABELS) == set(DIMENSION_IDS)
    assert DIMENSION_LABELS["single_threading"] == "Attention continuity"


def test_scoring_is_deterministic_and_pinned() -> None:
    """INV-6: the same counters must always produce the same profile —
    these exact scores are the contract."""
    profile = compute_performance(_window(), None)
    assert profile == compute_performance(_window(), None)
    by_id = {reading.dimension_id: reading for reading in profile.readings}
    assert by_id["deep_work"].score == 72
    assert by_id["working_pattern"].score == 94
    assert by_id["single_threading"].score == 88
    assert by_id["context_hygiene"].score == 84
    assert by_id["model_economy"].score == 100
    # 10% fail (-10), 5% slow (-5), 5% rework sits in the neutral band (-0).
    assert by_id["execution_quality"].score == 85
    assert profile.overall == 87  # (72+94+88+84+100+85) // 6
    assert level_for(profile.overall) == "strong"
    assert profile.delta_vs_prior is None
    assert [r.dimension_id for r in profile.readings] == list(DIMENSION_IDS)


def test_scores_stay_in_bounds_at_the_extremes() -> None:
    quiet = _window(
        active_days=1, deep_block_days=0, longest_block_min=0, quiet_hours_activity_pct=100,
        no_pause_days=1, switch_count=500, parallel_days=1, burst_windows=90,
        breaks_completed=0, cached_tokens=0, fresh_input_tokens=0,
        assistant_turns=0, premium_cost_micro_usd=0, priced_cost_micro_usd=0,
        unpriced_turns=10_000,
    )
    heavy = _window(
        deep_block_days=20, longest_block_min=600, quiet_hours_activity_pct=0,
        no_pause_days=0, switch_count=0, parallel_days=0, burst_windows=0,
        breaks_completed=50, cached_tokens=90_000_000,
    )
    for window in (quiet, heavy):
        for reading in compute_performance(window, None).readings:
            assert 0 <= reading.score <= 100
            assert reading.level in ("strong", "steady", "building")


def test_dimension_behaviors() -> None:
    baseline = compute_performance(_window(), None)
    by_id = {r.dimension_id: r for r in baseline.readings}

    # Recovery: late nights and no-pause days subtract; timer breaks credit.
    strained = compute_performance(
        _window(quiet_hours_activity_pct=40, no_pause_days=14, breaks_completed=0), None
    )
    assert {r.dimension_id: r for r in strained.readings}["working_pattern"].score < (
        by_id["working_pattern"].score
    )

    # Attention continuity: affected human-attention days are the cost.
    switchy = compute_performance(_window(attention_high_switch_days=20), None)
    assert {r.dimension_id: r for r in switchy.readings}[
        "single_threading"
    ].score < by_id["single_threading"].score

    # Structural transcript volume and background parallelism are neutral.
    noisy = compute_performance(
        _window(switch_count=50_000, parallel_days=20, burst_windows=7_000), None
    )
    assert {r.dimension_id: r for r in noisy.readings}[
        "single_threading"
    ].score == by_id["single_threading"].score

    # Context hygiene: oversized prompts subtract; low volume scores neutral.
    bloated = compute_performance(_window(assistant_turns=100), None)
    assert {r.dimension_id: r for r in bloated.readings}[
        "context_hygiene"
    ].score == by_id["context_hygiene"].score - 20
    tiny = compute_performance(
        _window(cached_tokens=1_000, fresh_input_tokens=1_000), None
    )
    tiny_reading = {r.dimension_id: r for r in tiny.readings}["context_hygiene"]
    assert tiny_reading.score == NEUTRAL_SCORE
    assert "not enough prompt volume" in tiny_reading.evidence[0]

    # Model economy: only the share above the premium-heavy line subtracts.
    premium_only = compute_performance(
        _window(premium_cost_micro_usd=9_000_000), None
    )
    assert {r.dimension_id: r for r in premium_only.readings}[
        "model_economy"
    ].score == 50


def test_execution_quality_two_sided_rework_and_caps() -> None:
    """P1 boundary math: neutral gate, capped penalties, and the two-sided
    rework treatment (neutral band, churn penalty, no-review deduction)."""

    def reading(**overrides: int):
        profile = compute_performance(_window(**overrides), None)
        return {r.dimension_id: r for r in profile.readings}["execution_quality"]

    # Too little of both volumes -> neutral, never celebrated or flagged.
    quiet = reading(commands_run=19, commands_failed=0, commands_slow=0,
                    tool_calls=19, rework_edits=0)
    assert quiet.neutral is True
    assert quiet.score == NEUTRAL_SCORE
    # Either volume alone crosses the gate.
    assert reading(commands_run=20, commands_failed=0, commands_slow=0,
                   tool_calls=0, rework_edits=0).neutral is False

    # Fail and slow penalties cap at 35 and 15.
    floor = reading(commands_run=100, commands_failed=80, commands_slow=100,
                    tool_calls=0, rework_edits=0)
    assert floor.score == 100 - 35 - 15

    # Rework inside the 3..30 band carries no penalty at all.
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=100, rework_edits=30).score == 100
    # Above the band the penalty grows with the rate, capped at 20.
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=100, rework_edits=40).score == 90
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=100, rework_edits=90).score == 80
    # Below the band AT volume, on a channel that has shown it can fire: the
    # gentle no-review deduction (two-sided rework by decision — low-touch
    # acceptance never reads as perfect).
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=1000, rework_edits=5).score == 92
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=500, rework_edits=10).score == 92
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=100, rework_edits=3).score == 100
    # ...but low edit volume takes no deduction (nothing to review).
    assert reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=10, rework_edits=0).score == 100

    # Evidence stays observational: the revision line names both numbers.
    revised = reading(commands_run=100, commands_failed=7, commands_slow=2,
                      tool_calls=200, rework_edits=24)
    assert "7 of 100 commands exited nonzero" in revised.evidence[0]
    assert "you revised 24 of 200 tool results" in revised.evidence[1]


def test_an_unobserved_rework_channel_never_deducts_or_accuses() -> None:
    """Regression, found by running the real record through the reading on
    2026-07-25.

    Rework is only emitted by Claude Code's IDE-extension diff flow
    (`toolUseResult.userModified`) and Codex's rejected `patch_apply_end`. A
    CLI/desktop user emits neither: 4 events in 47,621 tool calls over 113
    days, with `userModified` false in all 5,518 observations across 1,337
    session files. Before this fix every such window lost 8 points for
    "unreviewed acceptance" and rendered the line "you revised 4 of 22375 tool
    results" — a second-person accusation resting on a signal the logs cannot
    carry. Unobserved is not zero."""

    def reading(**overrides: int):
        profile = compute_performance(_window(**overrides), None)
        return {r.dimension_id: r for r in profile.readings}["execution_quality"]

    dead = reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=22375, rework_edits=4)
    assert dead.score == 100
    assert not any("revised" in line for line in dead.evidence)
    # A live channel at the same low rate still takes the deduction and still
    # shows its numbers — the fix narrows the claim, it does not remove it.
    live = reading(commands_run=100, commands_failed=0, commands_slow=0,
                   tool_calls=22375, rework_edits=5)
    assert live.score == 92
    assert any("you revised 5 of 22375 tool results" in line
               for line in live.evidence)


def test_trend_delta_against_prior_window() -> None:
    current, prior = _window(), _window(deep_block_days=2, quiet_hours_activity_pct=35)
    prior_alone = compute_performance(prior, None)
    profile = compute_performance(current, prior)
    assert profile.delta_vs_prior == profile.overall - prior_alone.overall
    assert profile.delta_vs_prior is not None and profile.delta_vs_prior > 0
    # Per-dimension deltas feed the trajectory arrows.
    prior_scores = dimension_scores(prior)
    for reading in profile.readings:
        assert reading.delta == reading.score - prior_scores[reading.dimension_id]
    empty_prior = _window(active_days=0)
    without = compute_performance(current, empty_prior)
    assert without.delta_vs_prior is None
    assert all(reading.delta is None for reading in without.readings)


def test_neutral_readings_are_marked() -> None:
    tiny = compute_performance(
        _window(cached_tokens=1_000, fresh_input_tokens=1_000,
                premium_cost_micro_usd=0, priced_cost_micro_usd=0,
                unpriced_turns=0),
        None,
    )
    by_id = {r.dimension_id: r for r in tiny.readings}
    assert by_id["context_hygiene"].neutral is True
    assert by_id["model_economy"].neutral is True
    assert by_id["deep_work"].neutral is False


def test_attention_continuity_confidence_and_exact_day_based_score() -> None:
    confident = compute_performance(
        _window(attention_high_switch_days=5, attention_prompt_burst_days=4), None
    )
    reading = {r.dimension_id: r for r in confident.readings}["single_threading"]
    assert reading.score == 100 - (45 * 5 // 20) - (20 * 4 // 20)
    assert reading.neutral is False
    assert "5 of 20 active days" in reading.evidence[0]
    assert "Background agent activity was excluded" in reading.evidence

    for override in (
        {"attention_active_days": 3},
        {"attention_human_events": 9},
        {"attention_known_coverage_pct": 69},
        {"attention_confident": False},
    ):
        profile = compute_performance(
            _window(
                attention_coordination_days=0,
                attention_prompt_burst_days=0,
                **override,
            ),
            None,
        )
        reading = {r.dimension_id: r for r in profile.readings}["single_threading"]
        assert reading.neutral is True
        assert reading.score == NEUTRAL_SCORE
        assert profile.attention_recommendation is None


def test_attention_recommendations_are_gated_and_ranked() -> None:
    cross = compute_performance(
        _window(
            attention_high_switch_days=4,
            attention_coordination_days=0,
            attention_prompt_burst_days=0,
        ),
        _window(attention_high_switch_days=1),
    ).attention_recommendation
    assert cross is not None
    assert cross.title == "Checkpoint one thread before moving"
    assert "4 of 20 active days" in cross.evidence
    assert "Background agent activity was excluded" in cross.evidence

    coordination = compute_performance(
        _window(
            attention_high_switch_days=0,
            attention_coordination_days=3,
            attention_prompt_burst_days=0,
        ), None,
    ).attention_recommendation
    assert coordination is not None
    assert coordination.title == "Review agent results in batches"

    batching = compute_performance(
        _window(
            attention_high_switch_days=0,
            attention_coordination_days=0,
            attention_prompt_burst_days=3,
        ), None,
    ).attention_recommendation
    assert batching is not None
    assert batching.title == "Batch nearby asks into one turn"

    tie = compute_performance(
        _window(
            attention_high_switch_days=4,
            attention_coordination_days=4,
            attention_prompt_burst_days=4,
        ), None,
    ).attention_recommendation
    assert tie is not None
    assert tie.title == "Checkpoint one thread before moving"


def test_weekly_windows_series() -> None:
    store, _health = build_fixture_history()
    weeks = weekly_windows(store, FIXTURE_DAY)
    assert len(weeks) == TRAJECTORY_WEEKS
    assert all(window.window_days == 7 for window in weeks)
    assert weeks[-1].active_days >= 1  # the fixture day lands in the last week
    assert set(dimension_scores(weeks[-1])) == set(DIMENSION_IDS)


def test_copy_passes_privacy_scanners() -> None:
    surface = " ".join(DIMENSION_LABELS.values())
    for window in (_window(), _window(active_days=1, cached_tokens=0)):
        for reading in compute_performance(window, None).readings:
            surface += " " + " ".join(reading.evidence)
    assert lexicon_violations(surface) == []
    assert leak_findings(surface) == []


def test_behavior_by_day_excludes_sidechain_marks() -> None:
    """P3: per-day behavior scoring reads interactive marks only — the
    sidechain fixture's 4 events and its fabricated switching never reach
    the performance dimensions (cost accounting keeps them elsewhere)."""
    from practicegraph.analysis.performance import behavior_by_day

    store, _health = build_fixture_history()
    behavior = behavior_by_day(store, FIXTURE_DAY, FIXTURE_DAY)
    day = behavior[FIXTURE_DAY.isoformat()]
    assert day.events == 16  # 20 marks on disk, 4 sidechain gated out
    assert day.switches == 0  # unfiltered interleaving would count 8


def test_unconfirmed_schedule_keeps_schedule_counters_missing() -> None:
    from practicegraph.analysis.performance import behavior_by_day

    store, _health = build_fixture_history()
    schedule = replace(
        compatibility_utc_schedule(), version=0, confirmed=False
    )
    day = behavior_by_day(store, FIXTURE_DAY, FIXTURE_DAY, schedule)[
        FIXTURE_DAY.isoformat()
    ]
    assert day.outside_preferred_events is None
    assert day.quiet_hours_events is None
    assert day.on_preferred_day is None


def test_gather_from_fixture_history() -> None:
    store, _health = build_fixture_history()
    inputs = window_inputs_from_store(store, FIXTURE_DAY)
    assert inputs.active_days >= 1
    assert inputs.cached_tokens + inputs.fresh_input_tokens > 0
    # P1 counters flow store -> window (3 claude tool_result blocks + 4 codex
    # outcomes; failures via is_error / exit_code / "execution error:" prefix;
    # rework: the userModified edit + the rejected codex patch).
    assert inputs.commands_run == 5
    assert inputs.commands_failed == 3
    assert inputs.commands_slow == 1
    assert inputs.rework_edits == 2
    assert inputs.tool_calls == 3
    profile = gather_performance(store, FIXTURE_DAY)
    assert profile is not None
    assert len(profile.readings) == len(DIMENSION_IDS)
    assert 0 <= profile.overall <= 100
    by_id = {r.dimension_id: r for r in profile.readings}
    # The demo day is far below both volume gates: honest neutral.
    assert by_id["execution_quality"].neutral is True
