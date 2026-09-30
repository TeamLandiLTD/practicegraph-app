"""Alert engine (FR-ALR).

Closed categories and reason codes, per-category toggles and 1/day frequency
caps, a global quiet-hours window, and persisted fired-state dedupe. Delivery
is a WinRT toast through the native shell (`practicegraph-shell toast`);
notification text is closed — display names and integers, never content
(FR-ALR-4). Every failure is fail-open: an alert that cannot be delivered is
recorded with a closed delivery code and never breaks the tick (NFR-REL-1).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from practicegraph.analysis.focus import NUDGE_STREAK_MIN, OngoingStreak
from practicegraph.analysis.insights import SpendPace
from practicegraph.report.format import count, percent
from practicegraph.store import Store

ENV_SHELL_EXE = "PRACTICEGRAPH_SHELL_EXE"

# Closed vocabularies (FR-ALR-2), pinned by tests.
CATEGORIES: tuple[str, ...] = (
    "daily_report_ready",
    "spend_pace",
    "source_health",
    "focus_break",
)
REASONS: dict[str, str] = {
    "daily_report_ready": "report_generated",
    "spend_pace": "projection_over_budget",
    "source_health": "parser_drift",
    "focus_break": "long_streak",
}
DELIVERY_CODES: tuple[str, ...] = ("delivered", "unavailable", "suppressed_quiet_hours")

_META_FOCUS_STREAK_KEY = "focus_nudge_fired_streak_start"
_META_FOCUS_LAST_FIRED_KEY = "focus_nudge_last_fired_at"
# The break nudge follows a cadence, not a daily cap (design decision 2026-08-21,
# from the recovery evidence: a few minutes away every 50-90 minutes). A
# nudge may fire again after this spacing — but only for a NEW streak, so a
# person who took the break is never nagged and a person who ignored it is
# reminded once per stretch, not once per day.
FOCUS_BREAK_SPACING_MIN = 90


def alert_text(category: str, metric: int, rest: bool = False) -> tuple[str, str]:
    """Closed notification copy: display names + integers only (FR-ALR-4)."""
    if category == "daily_report_ready":
        return ("PracticeGraph", "Your daily report is ready.")
    if category == "spend_pace":
        return (
            "PracticeGraph - spend pace",
            f"Projected month spend is {count(metric)}% of the configured budget.",
        )
    if category == "source_health":
        return (
            "PracticeGraph - source health",
            f"{count(metric)} log records were skipped safely. An agent update may "
            "be available.",
        )
    if rest:
        # The escalated moment (mirrors the app's break plan): the day has
        # been heavy, so the nudge names the long break, not the short one.
        return (
            "PracticeGraph - time for the long break",
            f"{count(metric)} minutes without a pause on a heavy day. "
            "25 minutes away from every screen restores more than a short one.",
        )
    return (
        "PracticeGraph - time for a break?",
        f"You have been at it {count(metric)} minutes without a 15-minute pause.",
    )


def in_quiet_hours(local_hhmm: str, quiet_start: str, quiet_end: str) -> bool:
    """Global quiet-hours window; supports windows that wrap midnight."""
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= local_hhmm < quiet_end
    return local_hhmm >= quiet_start or local_hhmm < quiet_end


def _shell_exe() -> Path | None:
    override = os.environ.get(ENV_SHELL_EXE)
    if override:
        path = Path(override)
        return path if path.is_file() else None
    if os.name == "nt":
        sibling = Path(sys.executable).parent.parent / "practicegraph-shell.exe"
        return sibling if sibling.is_file() else None
    if sys.platform == "darwin":
        # The engine ships inside the .app (Contents/Resources/engine/… or
        # Contents/Resources/…), so the shell binary sits a fixed hop away in
        # Contents/MacOS — wherever the bundle itself was installed. The
        # /Applications path is the packaging default, kept as the last try.
        engine = Path(sys.executable)
        for contents in (engine.parent.parent.parent, engine.parent.parent):
            candidate = contents / "MacOS" / "PracticeGraphShell"
            if candidate.is_file():
                return candidate
        default = Path(
            "/Applications/PracticeGraph.app/Contents/MacOS/PracticeGraphShell"
        )
        return default if default.is_file() else None
    return None


def deliver_toast(title: str, body: str, launch: str | None = None) -> str:
    """Fire a notification via the native shell; closed delivery codes only.

    `launch` is a closed deep-link verb (the shell validates it again): the
    break nudge passes "break" so tapping the notification opens the app
    straight into the guided break instead of dead-ending. On Windows that
    is the shell's `toast` mode; on macOS its `--notify` mode — both are
    short-lived second processes of the same shell binary."""
    shell = _shell_exe()
    if shell is None:
        return "unavailable"
    try:
        if os.name == "nt":
            flags = 0x0800_0000  # CREATE_NO_WINDOW
            args = [str(shell), "toast", "--title", title, "--body", body]
            if launch is not None:
                args += ["--launch", launch]
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=20,
                creationflags=flags,
            )
        elif sys.platform == "darwin":
            args = [str(shell), "--notify", launch or "open-report",
                    title, body]
            result = subprocess.run(args, capture_output=True, timeout=20)
        else:
            return "unavailable"
        return "delivered" if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


@dataclass(frozen=True, slots=True)
class AlertContext:
    """Everything one evaluation cycle may act on. Counters only."""

    day: str
    report_generated_first_time: bool
    drift_records: int
    pace: SpendPace | None
    streak: OngoingStreak | None
    focus_coaching: bool
    # The escalation counters the app's break plan uses, mirrored here so
    # the toast can name the long break on the same signals: a run of
    # re-fires after failures, or two completed blocks already behind.
    refires_today: int = 0
    blocks_completed: int = 0


def evaluate(
    store: Store,
    enabled: dict[str, bool],
    context: AlertContext,
    local_hhmm: str,
    quiet_start: str,
    quiet_end: str,
    now: datetime,
    deliver: Callable[[str, str, str | None], str] = deliver_toast,
) -> list[tuple[str, str]]:
    """Evaluate every category, isolated and deduped. Returns
    (category, delivery) for each alert fired this cycle."""
    quiet = in_quiet_hours(local_hhmm, quiet_start, quiet_end)
    fired: list[tuple[str, str]] = []

    def _fire(
        category: str, metric: int,
        launch: str | None = None, rest: bool = False,
    ) -> None:
        if quiet:
            # Quiet hours suppress delivery without consuming the daily cap:
            # the alert may still fire later in the day (FR-ALR-2).
            return
        title, body = alert_text(category, metric, rest=rest)
        delivery = deliver(title, body, launch)
        store.record_alert(category, context.day, REASONS[category], delivery, now)
        fired.append((category, delivery))

    if (
        enabled.get("daily_report_ready", False)
        and context.report_generated_first_time
        and not store.alert_already_fired("daily_report_ready", context.day)
    ):
        _fire("daily_report_ready", 0)

    if (
        enabled.get("spend_pace", True)
        and context.pace is not None
        and context.pace.over_budget
        and not store.alert_already_fired("spend_pace", context.day)
    ):
        pct = percent(context.pace.projected_micro_usd, context.pace.budget_micro_usd)
        _fire("spend_pace", pct)

    if (
        enabled.get("source_health", True)
        and context.drift_records > 0
        and not store.alert_already_fired("source_health", context.day)
    ):
        _fire("source_health", context.drift_records)

    # Cadence, not a daily cap (design decision 2026-08-21): a nudge may fire
    # again after FOCUS_BREAK_SPACING_MIN — but only for a NEW streak (the
    # per-streak dedupe below), so taking the break resets everything and
    # ignoring one earns at most one reminder per stretch.
    last_fired = store.meta_get(_META_FOCUS_LAST_FIRED_KEY)
    spaced = True
    if last_fired is not None:
        try:
            elapsed = (now - datetime.fromisoformat(last_fired)).total_seconds()
            spaced = elapsed >= FOCUS_BREAK_SPACING_MIN * 60
        except ValueError:
            spaced = True
    if (
        enabled.get("focus_break", True)
        and context.focus_coaching  # FR-FOC-5: the one switch gates nudges too
        and context.streak is not None
        and context.streak.minutes >= NUDGE_STREAK_MIN
        and spaced
        and store.meta_get(_META_FOCUS_STREAK_KEY) != context.streak.started_at
    ):
        # The escalated moment mirrors the app's break plan: a run of
        # re-fires, or two blocks already done, names the long break.
        rest = context.refires_today >= 3 or context.blocks_completed >= 2
        _fire("focus_break", context.streak.minutes, launch="break", rest=rest)
        # A given streak never re-fires, even across days (FR-FOC-4).
        store.meta_set(_META_FOCUS_STREAK_KEY, context.streak.started_at)
        store.meta_set(_META_FOCUS_LAST_FIRED_KEY, now.isoformat())

    return fired
