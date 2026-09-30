"""Alert engine (FR-ALR): closed vocabularies, quiet hours, caps, dedupe,
and the focus nudge's never-refire rule (FR-FOC-4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from practicegraph.alerts import (
    CATEGORIES,
    DELIVERY_CODES,
    FOCUS_BREAK_SPACING_MIN,
    REASONS,
    AlertContext,
    alert_text,
    deliver_toast,
    evaluate,
    in_quiet_hours,
)
from practicegraph.analysis.focus import OngoingStreak
from practicegraph.analysis.insights import SpendPace
from practicegraph.config import DEFAULT_ALERT_TOGGLES
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
ENABLED = dict.fromkeys(CATEGORIES, True)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _context(**overrides: object) -> AlertContext:
    base: dict[str, object] = {
        "day": "2026-07-02",
        "report_generated_first_time": False,
        "drift_records": 0,
        "pace": None,
        "streak": None,
        "focus_coaching": True,
    }
    base.update(overrides)
    return AlertContext(**base)  # type: ignore[arg-type]


def _deliver_recorder(log: list[tuple[str, str, str | None]]) -> object:
    def deliver(title: str, body: str, launch: str | None = None) -> str:
        log.append((title, body, launch))
        return "delivered"

    return deliver


def test_vocabularies_are_pinned() -> None:
    assert CATEGORIES == (
        "daily_report_ready", "spend_pace", "source_health", "focus_break",
    )
    assert set(REASONS) == set(CATEGORIES)
    assert DELIVERY_CODES == ("delivered", "unavailable", "suppressed_quiet_hours")
    assert set(DEFAULT_ALERT_TOGGLES) == set(CATEGORIES)


def test_alert_copy_is_closed_and_clean() -> None:
    """FR-ALR-4: display names + integers; no content, no leaks, no lexicon."""
    for category in CATEGORIES:
        title, body = alert_text(category, 137)
        assert lexicon_violations(title + body) == []
        assert leak_findings(title + body) == []
    # The escalated break copy passes the same gates.
    title, body = alert_text("focus_break", 137, rest=True)
    assert "long break" in title
    assert lexicon_violations(title + body) == []
    assert leak_findings(title + body) == []


def test_quiet_hours_window_wraps_midnight() -> None:
    assert in_quiet_hours("23:00", "22:00", "07:00")
    assert in_quiet_hours("03:00", "22:00", "07:00")
    assert not in_quiet_hours("12:00", "22:00", "07:00")
    assert in_quiet_hours("10:00", "09:00", "11:00")
    assert not in_quiet_hours("08:00", "09:00", "11:00")


def test_source_health_alert_fires_once_per_day(tmp_path: Path) -> None:
    store = _store(tmp_path)
    delivered: list[tuple[str, str]] = []
    context = _context(drift_records=4)
    fired = evaluate(store, ENABLED, context, "12:00", "22:00", "07:00", NOW,
                     deliver=_deliver_recorder(delivered))  # type: ignore[arg-type]
    assert fired == [("source_health", "delivered")]
    assert "4" in delivered[0][1]
    # Frequency cap: the same category never fires twice in a day (FR-ALR-2).
    again = evaluate(store, ENABLED, context, "13:00", "22:00", "07:00", NOW,
                     deliver=_deliver_recorder(delivered))  # type: ignore[arg-type]
    assert again == []


def test_quiet_hours_suppress_without_consuming_the_cap(tmp_path: Path) -> None:
    store = _store(tmp_path)
    context = _context(drift_records=2)
    quiet = evaluate(store, ENABLED, context, "23:30", "22:00", "07:00", NOW,
                     deliver=_deliver_recorder([]))  # type: ignore[arg-type]
    assert quiet == []
    assert not store.alert_already_fired("source_health", "2026-07-02")
    later = evaluate(store, ENABLED, context, "12:00", "22:00", "07:00", NOW,
                     deliver=_deliver_recorder([]))  # type: ignore[arg-type]
    assert later == [("source_health", "delivered")]


def test_disabled_category_never_fires(tmp_path: Path) -> None:
    store = _store(tmp_path)
    toggles = dict.fromkeys(CATEGORIES, False)
    context = _context(drift_records=9, report_generated_first_time=True)
    assert evaluate(store, toggles, context, "12:00", "22:00", "07:00", NOW,
                    deliver=_deliver_recorder([])) == []  # type: ignore[arg-type]


def test_spend_pace_alert(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pace = SpendPace(
        month_to_date_micro_usd=400_000,
        projected_micro_usd=1_200_000,
        budget_micro_usd=1_000_000,
    )
    delivered: list[tuple[str, str]] = []
    fired = evaluate(store, ENABLED, _context(pace=pace), "12:00", "22:00", "07:00",
                     NOW, deliver=_deliver_recorder(delivered))  # type: ignore[arg-type]
    assert fired == [("spend_pace", "delivered")]
    assert "120" in delivered[0][1]  # projected 120% of budget


def test_focus_nudge_fires_once_per_streak(tmp_path: Path) -> None:
    """FR-FOC-4: never re-fires for the same streak, and the focus master
    switch gates it entirely (FR-FOC-5)."""
    store = _store(tmp_path)
    streak = OngoingStreak(started_at="2026-07-02T09:00:00+00:00", minutes=125)

    off = evaluate(store, ENABLED, _context(streak=streak, focus_coaching=False),
                   "12:00", "22:00", "07:00", NOW,
                   deliver=_deliver_recorder([]))  # type: ignore[arg-type]
    assert off == []

    delivered: list[tuple[str, str, str | None]] = []
    fired = evaluate(store, ENABLED, _context(streak=streak), "12:00", "22:00",
                     "07:00", NOW, deliver=_deliver_recorder(delivered))  # type: ignore[arg-type]
    assert fired == [("focus_break", "delivered")]
    # The toast is a door: tapping it opens the app into the guided break.
    assert delivered[0][2] == "break"

    # Same streak the next day: the streak-start guard still blocks it.
    next_day = _context(day="2026-07-03", streak=streak)
    later = NOW + timedelta(hours=20)
    assert evaluate(store, ENABLED, next_day, "12:00", "22:00", "07:00", later,
                    deliver=_deliver_recorder([])) == []  # type: ignore[arg-type]

    # A new streak on the new day fires again.
    new_streak = OngoingStreak(started_at="2026-07-03T09:00:00+00:00", minutes=130)
    assert evaluate(store, ENABLED, _context(day="2026-07-03", streak=new_streak),
                    "12:00", "22:00", "07:00", later,
                    deliver=_deliver_recorder([])) == [
        ("focus_break", "delivered")
    ]  # type: ignore[arg-type]


def test_focus_nudge_follows_a_cadence_not_a_daily_cap(tmp_path: Path) -> None:
    """Design decision 2026-08-21: a few minutes away every 50-90 minutes is the
    evidence-backed rhythm, so a NEW streak may nudge again the same day —
    but never inside the spacing window, and never for the same streak."""
    store = _store(tmp_path)
    first = OngoingStreak(started_at="2026-07-02T07:00:00+00:00", minutes=125)
    assert evaluate(store, ENABLED, _context(streak=first), "12:00", "22:00",
                    "07:00", NOW, deliver=_deliver_recorder([])) == [
        ("focus_break", "delivered")
    ]  # type: ignore[arg-type]

    # A new streak forty minutes later: inside the spacing, no nudge.
    second = OngoingStreak(started_at="2026-07-02T10:00:00+00:00", minutes=121)
    soon = NOW + timedelta(minutes=40)
    assert evaluate(store, ENABLED, _context(streak=second), "12:00", "22:00",
                    "07:00", soon, deliver=_deliver_recorder([])) == []  # type: ignore[arg-type]

    # The same new streak past the spacing: the day's second nudge.
    past = NOW + timedelta(minutes=FOCUS_BREAK_SPACING_MIN + 1)
    assert evaluate(store, ENABLED, _context(streak=second), "12:00", "22:00",
                    "07:00", past, deliver=_deliver_recorder([])) == [
        ("focus_break", "delivered")
    ]  # type: ignore[arg-type]


def test_focus_nudge_escalates_to_the_long_break(tmp_path: Path) -> None:
    """The toast mirrors the app's break plan: a run of re-fires, or two
    completed blocks, names the 25-minute rest instead of the short break."""
    store = _store(tmp_path)
    streak = OngoingStreak(started_at="2026-07-02T09:00:00+00:00", minutes=140)
    delivered: list[tuple[str, str, str | None]] = []
    fired = evaluate(store, ENABLED,
                     _context(streak=streak, refires_today=3), "12:00",
                     "22:00", "07:00", NOW,
                     deliver=_deliver_recorder(delivered))  # type: ignore[arg-type]
    assert fired == [("focus_break", "delivered")]
    assert "long break" in delivered[0][0]
    assert "25 minutes" in delivered[0][1]
    assert delivered[0][2] == "break"


def test_short_streak_does_not_nudge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    streak = OngoingStreak(started_at="2026-07-02T11:00:00+00:00", minutes=60)
    assert evaluate(store, ENABLED, _context(streak=streak), "12:00", "22:00",
                    "07:00", NOW, deliver=_deliver_recorder([])) == []  # type: ignore[arg-type]


def test_delivery_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def broken(title: str, body: str, launch: str | None = None) -> str:
        return "unavailable"

    fired = evaluate(store, ENABLED, _context(drift_records=1), "12:00", "22:00",
                     "07:00", NOW, deliver=broken)
    assert fired == [("source_health", "unavailable")]
    assert store.alert_already_fired("source_health", "2026-07-02")


def test_suite_never_reaches_an_installed_shell() -> None:
    """Every agent tick in this suite may post a notification; none may launch
    a real PracticeGraph.app (the /Applications fallback did, once per run)."""
    assert deliver_toast("title", "body") == "unavailable"
