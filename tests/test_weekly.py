"""The weekly reading (A4): the Monday ritual. These tests pin that a reading is
withheld (None) until BOTH full weeks clear the active-day gate, that each
week-over-week line renders only when both weeks carry the metric (the sample
gate), that show_now is true only Monday/Tuesday with today injected, that the
numbers match the range helpers to the integer, that composition is
deterministic, and that the copy stays inside the lexicon.

The theme pillar comes from the coach's own gather; here we hold it fixed via
current_pillars so the reading's data logic — the gate, the lines, the numbers —
is what is pinned. The spend/behavior come from real seeded per-day rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import practicegraph.report.weekly as weekly
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.coach import CoachPillar
from practicegraph.report.weekly import (
    WEEKLY_COPY,
    WEEKLY_MIN_ACTIVE_DAYS,
    compose_weekly,
)
from practicegraph.store import Store

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
# today = Wednesday 2026-07-08.
#   last week (Mon-Sun): 2026-06-29 .. 2026-07-05
#   week before        : 2026-06-22 .. 2026-06-28
WEDNESDAY = date(2026, 7, 8)
LAST_WEEK_MON = date(2026, 6, 29)
PREV_WEEK_MON = date(2026, 6, 22)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _contrib(cost_micro_usd: int) -> list[int]:
    """An 18-int contribution list (ContributionRow layout) with cost set."""
    values = [0] * 18
    values[0] = 1              # assistant_turns (so the day is 'active')
    values[10] = cost_micro_usd
    return values


def _seed_day(store: Store, day: date, cost: int, hour: int, marks: int) -> None:
    """Seed one active day: a priced contribution plus `marks` interactive marks
    at the given UTC hour (so late-night lands when hour >= 22). Mark tuples are
    the DayMarkRow contract: (day, session_key, ts_utc, kind, ...)."""
    store.replace_file_data(
        source_id="claude_code_cli",
        path_hash=f"h-{day.isoformat()}",
        cursor=(1, 1, 8),
        contributions=[(day.isoformat(), "claude_code", "claude-sonnet-4", _contrib(cost))],
        marks=[
            (day.isoformat(), "sess-1",
             f"{day.isoformat()}T{hour:02d}:{m:02d}:00+00:00",
             "assistant", 0, 0, 0, 0, 0, 1, "", "", 0)
            for m in range(marks)
        ],
        health=None,
        turn_keys=(),
        now=NOW,
    )


def _fix_theme(monkeypatch, pillar: str = "focus") -> None:
    lead = CoachPillar(pillar=pillar, label=pillar.title(), score=40, tone="watch",
                       summary=f"{pillar} summary", cue="cue", measures="m")
    monkeypatch.setattr(weekly, "current_pillars", lambda s, t, p=None: [lead])


def _seed_two_full_weeks(store: Store) -> None:
    """Both weeks active enough, with spend and daytime marks every day, plus a
    couple of late-night days per week so the late line has both cohorts."""
    for i in range(7):
        # last week: rising spend, one late day (hour 23 on the last day).
        d = LAST_WEEK_MON + timedelta(days=i)
        _seed_day(store, d, cost=200_000, hour=23 if i == 6 else 10, marks=4)
    for i in range(7):
        d = PREV_WEEK_MON + timedelta(days=i)
        _seed_day(store, d, cost=150_000, hour=23 if i == 6 else 10, marks=3)


def test_withheld_until_both_weeks_clear_the_gate(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _fix_theme(monkeypatch)
    # Only two active days last week -> below the gate -> None.
    for i in range(2):
        _seed_day(store, LAST_WEEK_MON + timedelta(days=i), 100_000, 10, 3)
    for i in range(WEEKLY_MIN_ACTIVE_DAYS + 1):
        _seed_day(store, PREV_WEEK_MON + timedelta(days=i), 100_000, 10, 3)
    assert compose_weekly(store, WEDNESDAY) is None


def test_full_two_weeks_compose_lines_and_theme(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _fix_theme(monkeypatch, "focus")
    _seed_two_full_weeks(store)
    reading = compose_weekly(store, WEDNESDAY)
    assert reading is not None
    assert reading.theme_pillar == "focus"
    assert reading.next_focus == "focus summary"
    # Spend line present with both weeks' totals (7 * 200k vs 7 * 150k).
    spend_line = WEEKLY_COPY["spend"].format(this="$1.40", prior="$1.05")
    assert spend_line in reading.lines
    # Capability per dollar (W1.4): 7 priced turns each week, so last week is
    # $1.40/7 = $0.2000 a turn against $0.1500 the week before — exact.
    per_turn_line = WEEKLY_COPY["per_turn"].format(this="$0.2000", prior="$0.1500")
    assert per_turn_line in reading.lines
    # 3-5 lines, all lexicon-clean.
    assert 1 <= len(reading.lines) <= 5
    for line in reading.lines:
        assert lexicon_violations(line) == []
    # No advice note unless the advisor's audit line is passed in (AM-4).
    assert reading.advice_note == ""


def test_line_renders_only_when_both_weeks_have_the_metric(tmp_path, monkeypatch) -> None:
    """The late-night line needs a late stretch in BOTH weeks; with a late day
    in only one week, no late line is manufactured (no '0 vs N')."""
    store = _store(tmp_path)
    _fix_theme(monkeypatch)
    for i in range(7):  # last week: one late day
        _seed_day(store, LAST_WEEK_MON + timedelta(days=i),
                  200_000, 23 if i == 6 else 10, 4)
    for i in range(7):  # prev week: no late day at all
        _seed_day(store, PREV_WEEK_MON + timedelta(days=i), 150_000, 10, 3)
    reading = compose_weekly(store, WEDNESDAY)
    assert reading is not None
    assert not any("Late-night" in line for line in reading.lines)


def test_show_now_true_monday_tuesday_false_wednesday(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _fix_theme(monkeypatch)
    _seed_two_full_weeks(store)
    # Monday and Tuesday of the week AFTER the two seeded weeks -> show_now true.
    monday = date(2026, 7, 6)
    tuesday = date(2026, 7, 7)
    wednesday = date(2026, 7, 8)
    assert monday.weekday() == 0 and tuesday.weekday() == 1
    assert compose_weekly(store, monday).show_now is True
    assert compose_weekly(store, tuesday).show_now is True
    assert compose_weekly(store, wednesday).show_now is False


def test_double_compose_is_identical(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _fix_theme(monkeypatch)
    _seed_two_full_weeks(store)
    first = compose_weekly(store, WEDNESDAY)
    second = compose_weekly(store, WEDNESDAY)
    assert first == second


def test_advice_note_rides_the_reading_verbatim(tmp_path, monkeypatch) -> None:
    """AM-4: the Advisor's already-composed self-audit line is passed through
    untouched — the weekly never re-derives or re-words it."""
    store = _store(tmp_path)
    _fix_theme(monkeypatch)
    _seed_two_full_weeks(store)
    note = "Since 2026-06-01: premium share of spend 90% → 60%."
    reading = compose_weekly(store, WEDNESDAY, advice_audit=note)
    assert reading is not None
    assert reading.advice_note == note


def test_weekly_copy_is_lexicon_clean() -> None:
    for text in WEEKLY_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        # No streak / completion vocabulary — a reading, not a scoreboard.
        low = text.lower()
        for token in ("streak", "in a row", "% complete", "days straight"):
            assert token not in low
