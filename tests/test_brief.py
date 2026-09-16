"""The daily brief: closed template catalogs, deterministic assembly, and
copy discipline (FR-FOC-8) over every sentence the verdict can produce."""

from __future__ import annotations

from datetime import date

from conftest import build_fixture_snapshot
from practicegraph.analysis.performance import (
    DIMENSION_IDS,
    WindowInputs,
    compute_performance,
)
from practicegraph.history import DayPoint, WeekOverWeek
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.brief import (
    ACTIONS,
    ALL_STRONG_ACTION,
    CELEBRATE_LEAD,
    COST_HEAVY,
    COST_TYPICAL,
    FLAG_LEAD,
    QUEUE_SWITCHING_COPY,
    build_brief,
)

MONDAY = date(2026, 6, 29)
THURSDAY = date(2026, 7, 2)


def _window(**overrides: int) -> WindowInputs:
    base: dict[str, int] = {
        "window_days": 28, "active_days": 20, "deep_block_days": 12,
        "longest_block_min": 148, "quiet_hours_activity_pct": 8, "no_pause_days": 3,
        "switch_count": 40, "parallel_days": 2, "burst_windows": 10,
        "breaks_completed": 4, "cached_tokens": 8_000_000,
        "fresh_input_tokens": 1_500_000, "assistant_turns": 900,
        "premium_cost_micro_usd": 4_000_000, "priced_cost_micro_usd": 9_000_000,
        "unpriced_turns": 0, "tool_calls": 1200, "rework_edits": 60,
        "commands_run": 300, "commands_failed": 30, "commands_slow": 15,
        "attention_active_days": 20, "attention_human_events": 100,
        "attention_known_events": 100, "attention_known_coverage_pct": 100,
        "attention_switch_count": 16, "attention_high_switch_days": 4,
        "attention_coordination_windows": 0, "attention_coordination_days": 0,
        "attention_prompt_burst_windows": 0, "attention_prompt_burst_days": 0,
        "attention_confident": True,
    }
    base.update(overrides)
    return WindowInputs(**base)


def _weekly(high_now: int = 3, high_usual: int = 1) -> list[WindowInputs]:
    weeks = [
        _window(
            window_days=7, active_days=5, attention_active_days=5,
            attention_high_switch_days=high_usual,
        )
        for _ in range(11)
    ]
    weeks.append(_window(
        window_days=7, active_days=5, attention_active_days=5,
        attention_high_switch_days=high_now,
    ))
    return weeks


def _costs(today: int = 500_000) -> list[DayPoint]:
    return [
        DayPoint(f"2026-06-{day:02d}", cost, cost * 100, 10)
        for day, cost in enumerate(
            (200_000, 300_000, 400_000, 500_000, 600_000, 700_000, 800_000), start=1
        )
    ] + [DayPoint("2026-07-02", today, today * 100, 10)]


def test_catalogs_cover_every_dimension() -> None:
    for catalog in (CELEBRATE_LEAD, FLAG_LEAD, ACTIONS):
        assert set(catalog) == set(DIMENSION_IDS)


def test_brief_assembles_celebrate_flag_and_action() -> None:
    # Weak single-threading window: switching is flagged with the multiple.
    profile = compute_performance(_window(attention_high_switch_days=20), None)
    brief = build_brief(
        build_fixture_snapshot(), profile, _weekly(), _costs(), None, THURSDAY
    )
    assert brief is not None
    assert brief == build_brief(  # deterministic assembly (INV-6)
        build_fixture_snapshot(), profile, _weekly(), _costs(), None, THURSDAY
    )
    assert brief.celebrate.startswith(CELEBRATE_LEAD["model_economy"])
    assert brief.flag is not None
    assert brief.flag.startswith(FLAG_LEAD["single_threading"])
    assert "about 3.0x your usual weekly pace" in brief.flag
    assert (brief.action_label, brief.action_href) == ACTIONS["single_threading"]
    assert brief.weekly_line is None  # not a Monday


def test_all_strong_profile_keeps_the_streak() -> None:
    profile = compute_performance(
        _window(deep_block_days=20, longest_block_min=200, switch_count=0,
                burst_windows=0, parallel_days=0, no_pause_days=0,
                quiet_hours_activity_pct=0, premium_cost_micro_usd=1_000_000,
                attention_high_switch_days=0, attention_prompt_burst_days=0),
        None,
    )
    brief = build_brief(
        build_fixture_snapshot(), profile, _weekly(), _costs(), None, THURSDAY
    )
    assert brief is not None
    assert brief.flag is None
    assert (brief.action_label, brief.action_href) == ALL_STRONG_ACTION


def test_cost_typicality_and_monday_review() -> None:
    profile = compute_performance(_window(), None)
    snapshot = build_fixture_snapshot()
    wow = WeekOverWeek(cost_delta_pct=12, tokens_delta_pct=-3)
    monday = build_brief(snapshot, profile, _weekly(), _costs(), wow, MONDAY)
    assert monday is not None
    assert monday.weekly_line is not None
    assert "+12%" in monday.weekly_line and "-3%" in monday.weekly_line
    # Fixture day costs ~42,005 micro-USD — below every seeded day: light.
    assert "light day" in monday.cost_line
    tiny_days = [DayPoint(f"2026-06-{day:02d}", 10_000, 1_000_000, 5)
                 for day in range(1, 9)]
    heavy = build_brief(snapshot, profile, _weekly(), tiny_days, wow, THURSDAY)
    assert heavy is not None  # today's fixture cost sits above every tiny day
    assert COST_HEAVY in heavy.cost_line
    same_days = [DayPoint(f"2026-06-{day:02d}", 42_005, 1_000_000, 5)
                 for day in range(1, 9)]
    typical = build_brief(snapshot, profile, _weekly(), same_days, wow, THURSDAY)
    assert typical is not None and COST_TYPICAL in typical.cost_line
    # No profile -> no brief (renderer shows the honest empty state).
    assert build_brief(snapshot, None, [], [], None, THURSDAY) is None


def test_every_template_passes_the_scanners() -> None:
    surface = " ".join(CELEBRATE_LEAD.values()) + " ".join(FLAG_LEAD.values())
    surface += " ".join(label for label, _href in ACTIONS.values())
    surface += " ".join(ALL_STRONG_ACTION) + " ".join(QUEUE_SWITCHING_COPY)
    for window in (
        _window(),
        _window(switch_count=1500),
        _window(deep_block_days=0, longest_block_min=10),
        _window(quiet_hours_activity_pct=45, no_pause_days=15),
        _window(cached_tokens=100_000, fresh_input_tokens=9_000_000),
        _window(premium_cost_micro_usd=9_000_000, unpriced_turns=40),
        _window(commands_failed=200, commands_slow=100, rework_edits=600),
    ):
        profile = compute_performance(window, None)
        brief = build_brief(
            build_fixture_snapshot(), profile, _weekly(), _costs(),
            WeekOverWeek(5, 5), MONDAY,
        )
        assert brief is not None
        surface += " ".join(
            filter(None, (brief.cost_line, brief.celebrate, brief.flag,
                          brief.action_label, brief.weekly_line))
        )
    assert lexicon_violations(surface) == []
    assert leak_findings(surface) == []
