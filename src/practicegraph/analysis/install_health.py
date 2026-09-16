"""Is this install actually working, and is an older one fighting it?

Two questions the app could not previously answer about itself, both created or
sharpened by the 2026-07-26 move to a per-user install.

**1. A leftover machine-wide agent.** A per-user MSI cannot upgrade or remove a
per-machine one — different install contexts, different product scopes. So
someone upgrading gets the new install *alongside* the old `PracticeGraphAgent`
service, which keeps running as LocalSystem and keeps writing its own store in
``%ProgramData%``. Nothing breaks loudly; the person simply has two agents and
sees whichever store they happen to open. The installer cannot fix this (that
would need a custom action, and C-3 says no), so the app says it plainly and
tells them the one command that ends it.

**2. A stalled agent.** Dropping the service moved the scheduler into the tray.
That is the right trade — the agent derives from log files it re-reads, so a
closed tray costs a delay and never data — but it introduces a failure mode the
service did not have: **if the tray dies, ticks stop and nothing says so.** A
Windows service is restarted by the SCM; a tray process is not restarted by
anyone. Silence used to mean "nothing to report" and now it can mean "nobody
is reporting", and a measurement tool that cannot tell those apart is lying by
omission. So staleness is stated on the page.

Determinism (INV-6): pure comparison against the `now` passed in. Local-only
(NFR-PRV-6) — nothing here rides an emit.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

# The tick interval the tray and the service both use (shell/src/service.rs
# DEFAULT_INTERVAL_S). Staleness is measured in multiples of it so the copy
# stays true if the interval ever changes.
TICK_INTERVAL_MIN = 15
# Below this, a gap is ordinary: a laptop was asleep, a tick ran long, the
# machine was off. Four missed ticks in a row is not.
STALE_AFTER_TICKS = 4
STALE_AFTER_MIN = TICK_INTERVAL_MIN * STALE_AFTER_TICKS

LEGACY_SERVICE_NAME = "PracticeGraphAgent"

HEALTH_COPY: dict[str, str] = {
    "legacy-label": "An older install is still running",
    "legacy-line": (
        "The machine-wide PracticeGraph service is installed on this computer "
        "as well. It keeps its own copy of your history and updates on its own "
        "schedule, so the two can disagree about what you did today."
    ),
    "legacy-why": (
        "A per-user install cannot remove a machine-wide one - Windows treats "
        "them as different products. Removing the old one needs an "
        "administrator, once."
    ),
    "legacy-action": (
        "In an admin terminal: sc.exe stop PracticeGraphAgent, then uninstall "
        "PracticeGraph from Apps & features. Your history is already here; "
        "nothing is lost."
    ),
    "stale-label": "This page may be out of date",
    "stale-line": (
        "The last update was {ago} ago. Readings on this page are from then, "
        "not from now."
    ),
    "stale-why": (
        "PracticeGraph refreshes about every {interval} minutes while it is "
        "running. A longer gap usually means it was closed or the machine was "
        "asleep - nothing is lost either way, because everything here is read "
        "back from your tools' own logs the next time it runs."
    ),
    # Where to reopen the app from differs per platform; see `_stale_action`.
    "stale-action": "Open PracticeGraph from the {launcher} to refresh now.",
}

# The one launcher name in the copy that is not the same on both platforms.
# Telling a Mac user to look in the Start menu is a small thing that says
# loudly the app was not built for their machine.
LAUNCHERS: dict[str, str] = {"win32": "Start menu", "darwin": "Applications folder"}
LAUNCHER_FALLBACK = "app launcher"


def _stale_action(platform: str | None = None) -> str:
    launcher = LAUNCHERS.get(platform or sys.platform, LAUNCHER_FALLBACK)
    return HEALTH_COPY["stale-action"].format(launcher=launcher)


@dataclass(frozen=True, slots=True)
class InstallNotice:
    """One thing wrong with the install, stated plainly. Never a warning tone:
    both of these are ordinary situations with a clear next step."""

    notice_id: str  # "legacy_service" | "stale"
    label: str
    line: str
    why: str
    action: str


def minutes_since(last_tick_at: str, now: datetime) -> int | None:
    """Whole minutes since the recorded tick, or None when unreadable.

    A future timestamp reads as 0 rather than negative: a clock change is not
    evidence that the agent is ahead of itself."""
    try:
        recorded = datetime.fromisoformat(last_tick_at)
    except (TypeError, ValueError):
        return None
    if recorded.tzinfo is None or now.tzinfo is None:
        return None
    return max(int((now - recorded).total_seconds()) // 60, 0)


def _ago(minutes: int) -> str:
    if minutes < 120:
        return f"{minutes} minutes"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hours"
    return f"{hours // 24} days"


def legacy_service_present() -> bool:
    """True when the pre-per-user machine service is still installed.

    Read-only: opens the service control manager with connect rights and the
    service with query rights, which any user has. Never starts, stops or
    modifies anything - this module reports, the person decides. Returns False
    off Windows and on any failure, because "we could not tell" must not
    produce a notice telling someone to uninstall something.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.OpenSCManagerW.restype = wintypes.HANDLE
        advapi32.OpenSCManagerW.argtypes = (
            wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        )
        advapi32.OpenServiceW.restype = wintypes.HANDLE
        advapi32.OpenServiceW.argtypes = (
            wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
        )
        advapi32.CloseServiceHandle.argtypes = (wintypes.HANDLE,)

        sc_manager_connect = 0x0001
        service_query_status = 0x0004
        manager = advapi32.OpenSCManagerW(None, None, sc_manager_connect)
        if not manager:
            return False
        try:
            service = advapi32.OpenServiceW(
                manager, LEGACY_SERVICE_NAME, service_query_status
            )
            if not service:
                return False
            advapi32.CloseServiceHandle(service)
            return True
        finally:
            advapi32.CloseServiceHandle(manager)
    except Exception:
        return False


def compose_install_health(
    last_tick_at: str | None,
    now: datetime,
    *,
    legacy_service: bool = False,
) -> list[InstallNotice]:
    """Notices for this install, most consequential first.

    The leftover service leads: it makes every OTHER number on the page
    ambiguous, because two agents are writing two stores and the page shows
    one of them.
    """
    notices: list[InstallNotice] = []
    if legacy_service:
        notices.append(
            InstallNotice(
                notice_id="legacy_service",
                label=HEALTH_COPY["legacy-label"],
                line=HEALTH_COPY["legacy-line"],
                why=HEALTH_COPY["legacy-why"],
                action=HEALTH_COPY["legacy-action"],
            )
        )
    elapsed = minutes_since(last_tick_at, now) if last_tick_at else None
    if elapsed is not None and elapsed >= STALE_AFTER_MIN:
        notices.append(
            InstallNotice(
                notice_id="stale",
                label=HEALTH_COPY["stale-label"],
                line=HEALTH_COPY["stale-line"].format(ago=_ago(elapsed)),
                why=HEALTH_COPY["stale-why"].format(interval=TICK_INTERVAL_MIN),
                action=_stale_action(),
            )
        )
    return notices


def stale_threshold() -> timedelta:
    """The gap after which the page admits it is old."""
    return timedelta(minutes=STALE_AFTER_MIN)
