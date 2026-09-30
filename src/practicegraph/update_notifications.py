"""Announce a verified newer release once, as a native toast.

The manifest pull (`catalog.pull_update_manifest`) already verifies the
Ed25519 signature and caches the document; the dashboard shows the update
card from that cache. This module adds the one thing a person who has not
opened the app would otherwise miss: a single notification per version,
outside quiet hours, that opens the app on the update card. It never
downloads or installs anything - the toast carries a sentence and a door.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from practicegraph.alerts import deliver_toast
from practicegraph.catalog import load_update_offer
from practicegraph.news_notifications import quiet_now
from practicegraph.store import Store

STATE_KEY = "update_notified_version"

UPDATE_TOAST: dict[str, str] = {
    "title": "PracticeGraph {version} is available",
    "body": "You are on {current}. Open PracticeGraph to download the installer.",
}


def deliver_update(title: str, body: str, launch: str | None = None) -> str:
    """Reviewed copy only reaches the OS; `update` is a closed launch verb."""
    return deliver_toast(title, body, launch=launch)


def evaluate_update(
    store: Store, data_dir: Path, now: datetime, current: str,
    deliver: Callable[[str, str, str | None], str] = deliver_update,
) -> str:
    """One toast per verified newer version; closed outcome strings only."""
    offer = load_update_offer(data_dir, current)
    if offer is None:
        return "skipped_current"
    if store.meta_get(STATE_KEY) == offer.version:
        return "skipped_already_notified"
    if quiet_now(data_dir, now):
        return "skipped_quiet"
    title = UPDATE_TOAST["title"].format(version=offer.version)
    body = UPDATE_TOAST["body"].format(current=current)
    try:
        result = deliver(title, body, "update")
    except Exception:
        result = "unavailable"
    if result == "delivered":
        store.meta_set(STATE_KEY, offer.version)
        return "delivered"
    return "unavailable"
