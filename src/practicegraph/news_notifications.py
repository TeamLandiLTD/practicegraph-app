"""Private news attention state and bounded delivery of public editorial copy.

Reading and notification choices never enter organizational aggregates. The
catalog can supply display text and urgency, not commands or window settings.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from practicegraph.alerts import _shell_exe, deliver_toast, in_quiet_hours
from practicegraph.analysis.news import NewsItem, news_item_to_entry
from practicegraph.analysis.schedule import read_schedule_profile
from practicegraph.catalog import load_news
from practicegraph.config import read_prefs
from practicegraph.store import Store

STATE_KEY = "news_attention_v1"
MODES = ("off", "important", "urgent")
MAX_PER_DAY = 3
SPACING_SECONDS = 60 * 60
RETRY_SECONDS = 15 * 60
Record = dict[str, Any]


def _decode(raw: str | None) -> Record:
    try:
        value = json.loads(raw or "{}")
    except (ValueError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    state: Record = {
        "mode": value.get("mode") if value.get("mode") in MODES else "important",
        "urgent_popup": value.get("urgent_popup", True) is True,
    }
    for key in ("snoozed_until", "last_attempt", "last_delivery"):
        n = value.get(key)
        state[key] = n if type(n) is int and n >= 0 else 0
    for key in ("seen", "sent"):
        mapping = value.get(key, {})
        state[key] = {
            k: v for k, v in list(mapping.items())[-2048:]
            if isinstance(k, str) and type(v) is int and 0 <= v <= 2
        } if isinstance(mapping, dict) else {}
    stamps = value.get("deliveries", [])
    state["deliveries"] = [n for n in stamps if type(n) is int and n >= 0][-MAX_PER_DAY:] \
        if isinstance(stamps, list) else []
    return state


def _load(conn: sqlite3.Connection) -> Record:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (STATE_KEY,)).fetchone()
    return _decode(row[0] if row else None)


def _save(conn: sqlite3.Connection, state: Record) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (STATE_KEY, json.dumps(state)),
    )


def rank(item: NewsItem) -> int:
    return (2 if item.attention.urgency == "urgent" else 1) if item.attention else 0


def active(item: NewsItem, now: datetime) -> bool:
    if item.attention is None:
        return False
    start = datetime.fromisoformat(item.attention.starts_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(item.attention.expires_at.replace("Z", "+00:00"))
    return start <= now.astimezone(UTC) < end


def quiet_now(data_dir: Path, now: datetime) -> bool:
    """Inside the reader's quiet hours (confirmed schedule, else preferences)."""
    schedule = read_schedule_profile(data_dir)
    prefs = read_prefs(data_dir)
    local = now.astimezone(schedule.zone) if schedule.confirmed else now.astimezone()
    start, end = (schedule.quiet_start, schedule.quiet_end) if schedule.confirmed else (
        prefs.quiet_start, prefs.quiet_end,
    )
    return in_quiet_hours(local.strftime("%H:%M"), start, end)


def reading(store: Store, data_dir: Path, now: datetime) -> Record:
    state = _decode(store.meta_get(STATE_KEY))
    items = sorted(load_news(data_dir), key=lambda i: (-int(active(i, now)) * rank(i), i.news_id))
    entries = []
    for item in items:
        entry = news_item_to_entry(item)
        entry.pop("thumb", None)  # No extra network/image surface in a notification.
        entry["unread"] = state["seen"].get(item.news_id, -1) < rank(item)
        entry["attention_active"] = active(item, now)
        entries.append(entry)
    return {
        "items": entries,
        "unread_count": sum(bool(e["unread"]) for e in entries),
        "settings": {k: state[k] for k in ("mode", "urgent_popup", "snoozed_until")},
        "quiet_hours": quiet_now(data_dir, now),
        "limits": {"max_per_day": MAX_PER_DAY, "spacing_minutes": SPACING_SECONDS // 60},
    }


def change(store: Store, data_dir: Path, body: dict[str, object], now: datetime) -> None:
    action = body.get("action")
    if action == "settings":
        if set(body) != {"action", "mode", "urgent_popup"} or body["mode"] not in MODES \
                or type(body["urgent_popup"]) is not bool:
            raise ValueError("invalid_news_settings")
    elif action == "snooze":
        if set(body) != {"action", "minutes"} or type(body["minutes"]) is not int \
                or body["minutes"] not in (0, 60, 480, 1440):
            raise ValueError("invalid_news_snooze")
    elif action == "read":
        if set(body) != {"action", "id"} or not isinstance(body["id"], str):
            raise ValueError("invalid_news_item")
    else:
        raise ValueError("invalid_news_action")
    with store.local_transaction() as conn:
        state = _load(conn)
        if action == "settings":
            state.update(mode=body["mode"], urgent_popup=body["urgent_popup"])
        elif action == "snooze":
            minutes = int(str(body["minutes"]))
            state["snoozed_until"] = int(now.timestamp()) + minutes * 60 if minutes else 0
        else:
            item = next((i for i in load_news(data_dir) if i.news_id == body["id"]), None)
            if item is None:
                raise ValueError("unknown_news_item")
            state["seen"][item.news_id] = rank(item)
        _save(conn, state)


def deliver_news(item: NewsItem, popup: bool) -> str:
    """Only reviewed public news copy is passed to the OS; launch is a closed verb."""
    # The compact window is currently a Windows shell capability. Other native
    # shells keep ordinary delivery behavior until they implement this door.
    if os.name != "nt":
        return "unavailable"
    delivery = deliver_toast(item.title, item.hook, launch="news")
    if popup:
        shell = _shell_exe()
        if shell is not None:
            try:
                result = subprocess.run(
                    [str(shell), "news-popup"], capture_output=True, timeout=20,
                    creationflags=0x0800_0000,
                )
                if result.returncode == 0:
                    delivery = "delivered"
            except (OSError, subprocess.SubprocessError):
                pass  # The OS toast may still have succeeded.
    return delivery


def evaluate_news(
    store: Store, data_dir: Path, now: datetime,
    deliver: Callable[[NewsItem, bool], str] = deliver_news,
) -> str:
    if quiet_now(data_dir, now):
        return "skipped_quiet"
    stamp = int(now.timestamp())
    items = sorted(load_news(data_dir), key=lambda i: (-rank(i), i.news_id))
    with store.local_transaction() as conn:
        state = _load(conn)
        if state["mode"] == "off" or state["snoozed_until"] > stamp:
            return "skipped_quiet"
        candidates = [i for i in items if active(i, now)
                      and rank(i) >= (2 if state["mode"] == "urgent" else 1)
                      and state["seen"].get(i.news_id, -1) < rank(i)
                      and state["sent"].get(i.news_id, -1) < rank(i)]
        if not candidates:
            return "skipped_quiet"
        # Rolling 24-hour budget avoids timezone changes or restarts resetting caps.
        recent = [n for n in state["deliveries"] if stamp - n < 86400]
        if len(recent) >= MAX_PER_DAY or stamp - state["last_delivery"] < SPACING_SECONDS \
                or stamp - state["last_attempt"] < RETRY_SECONDS:
            return "skipped_quiet"
        item = candidates[0]
        popup = rank(item) == 2 and state["urgent_popup"]
        state["last_attempt"] = stamp  # Atomic reservation across simultaneous agent ticks.
        _save(conn, state)
    try:
        result = deliver(item, popup)
    except Exception:
        result = "unavailable"
    if result == "delivered":
        with store.local_transaction() as conn:
            state = _load(conn)  # Preserve reads/snoozes made while delivery was in flight.
            state["sent"][item.news_id] = rank(item)
            state["last_delivery"] = stamp
            state["deliveries"] = [n for n in state["deliveries"] if stamp - n < 86400] + [stamp]
            _save(conn, state)
        return "ok"
    return "error"
