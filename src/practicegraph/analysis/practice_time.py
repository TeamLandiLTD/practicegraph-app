"""Private practice-time journal. Confirmed time is an input, never a skill score.

Timers persist server-side and require a heartbeat at least every 90 seconds.
A missed heartbeat pauses at the last acknowledged instant; downtime is never
backfilled. Recent human-event intervals are optional estimates until reviewed.
No prompts, paths, tool identities, or journal values enter aggregate telemetry.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, time, timedelta
from typing import Any

from practicegraph.analysis.schedule import ScheduleProfile
from practicegraph.store import MARK_HUMAN_INITIATED, Store

SKILLS = {
    "ai-development": "AI-assisted development",
    "ai-research": "Research and analysis",
    "ai-writing": "Writing with AI",
    "task-framing": "Framing useful tasks",
    "verification": "Reviewing and verifying results",
}
REFLECTIONS = ("learned", "needs_practice", "unsure", "")
LEASE_SECONDS = 90
MAX_SESSION_SECONDS = 12 * 3600
SCHEMA = "practicegraph.practice-time/1"
WIRE_FORBIDDEN_TERMS = ("practice_time", "confirmed_seconds", "practice_journal")
Entry = dict[str, Any]
Intervals = list[list[int]]


def _setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM practice_time_settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO practice_time_settings VALUES (?,?)", (key, json.dumps(value))
    )


def _entries(conn: sqlite3.Connection) -> list[Entry]:
    return [json.loads(row[0]) for row in conn.execute("SELECT body FROM practice_time_entries")]


def _entry(conn: sqlite3.Connection, entry_id: object) -> Entry:
    if not isinstance(entry_id, str):
        raise ValueError("invalid_session")
    row = conn.execute("SELECT body FROM practice_time_entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        raise ValueError("missing_session")
    return dict(json.loads(row[0]))


def _save(conn: sqlite3.Connection, entry: Entry) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO practice_time_entries VALUES (?,?)",
        (entry["id"], json.dumps(entry, separators=(",", ":"))),
    )


def union(intervals: Intervals) -> Intervals:
    merged: Intervals = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def subtract(intervals: Intervals, occupied: Intervals) -> Intervals:
    result = union(intervals)
    for left, right in union(occupied):
        parts = []
        for start, end in result:
            if end <= left or start >= right:
                parts.append([start, end])
            else:
                if start < left:
                    parts.append([start, left])
                if end > right:
                    parts.append([right, end])
        result = parts
    return result


def seconds(intervals: Intervals) -> int:
    return sum(end - start for start, end in union(intervals))


def _trim(intervals: Intervals, amount: int) -> Intervals:
    result = []
    for start, end in intervals:
        take = min(end - start, amount)
        if take:
            result.append([start, start + take])
            amount -= take
    return result


def _active(conn: sqlite3.Connection) -> Entry | None:
    entry_id = _setting(conn, "active")
    return _entry(conn, entry_id) if entry_id else None


def _tick(conn: sqlite3.Connection, entry: Entry, now: int, *, pause: bool = False) -> None:
    if entry["running"]:
        gap = now - entry["last_tick"]
        if 0 <= gap <= LEASE_SECONDS:
            remaining = MAX_SESSION_SECONDS - seconds(entry["intervals"])
            entry["intervals"] = union(
                [
                    *entry["intervals"],
                    [entry["last_tick"], entry["last_tick"] + min(gap, remaining)],
                ]
            )
            entry["last_tick"] = now
        else:
            pause = True
        entry["running"] = not pause and seconds(entry["intervals"]) < MAX_SESSION_SECONDS
        _save(conn, entry)


def _expire(conn: sqlite3.Connection, now: int) -> Entry | None:
    active = _active(conn)
    if active and active["running"] and not 0 <= now - active["last_tick"] <= LEASE_SECONDS:
        active["running"] = False
        _save(conn, active)
    return active


def _breaks(conn: sqlite3.Connection, now: int) -> Intervals:
    values = _setting(conn, "breaks", [])
    since = _setting(conn, "break_since")
    return [*values, *([[since, now]] if since is not None and since < now else [])]


def record_break(store: Store, now: datetime, *, starting: bool) -> None:
    """Existing break controls pause practice too; starting practice ends a break."""
    stamp = int(now.timestamp())
    with store.practice_time_transaction() as conn:
        if not _setting(conn, "enabled", False):
            return
        active = _active(conn)
        if starting and active:
            _tick(conn, active, stamp, pause=True)
        since = _setting(conn, "break_since")
        if starting and since is None:
            _set(conn, "break_since", stamp)
        elif not starting and since is not None:
            # Only the seven-day suggestion horizon needs break exclusions.
            prior = [pair for pair in _breaks(conn, stamp) if pair[1] >= stamp - 8 * 86400]
            _set(conn, "breaks", union(prior))
            _set(conn, "break_since", None)


def _suggestions(
    store: Store,
    conn: sqlite3.Connection,
    now: datetime,
    schedule: ScheduleProfile,
) -> list[Entry]:
    today = now.astimezone(schedule.zone).date()
    cutoff = today - timedelta(days=6)
    stamps: set[int] = set()
    for row in store.marks_between(
        (cutoff - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()
    ):
        mark = row[1:]
        if not mark[MARK_HUMAN_INITIATED]:
            continue
        parsed = datetime.fromisoformat(mark[1].replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed <= now:
            stamps.add(int(parsed.timestamp()))
    intervals = [
        [a, b] for a, b in zip(sorted(stamps), sorted(stamps)[1:], strict=False) if 0 < b - a <= 300
    ]
    entries = _entries(conn)
    oldest = int(datetime.combine(cutoff, time(), schedule.zone).timestamp())
    occupied = [pair for entry in entries for pair in entry["intervals"] if pair[1] > oldest]
    intervals = subtract(intervals, [*occupied, *_breaks(conn, int(now.timestamp()))])
    existing = {entry["id"] for entry in entries}
    candidates = []
    for offset in range(7):
        day = today - timedelta(days=offset)
        key = f"observed-{day.isoformat()}"
        if key in existing:
            continue
        start = int(datetime.combine(day, time(), schedule.zone).timestamp())
        end = int(datetime.combine(day + timedelta(days=1), time(), schedule.zone).timestamp())
        parts = union([[max(a, start), min(b, end)] for a, b in intervals])
        if seconds(parts) >= 60:
            candidates.append(
                {"id": key, "day": day.isoformat(), "intervals": parts, "seconds": seconds(parts)}
            )
    return candidates


def _new(entry_id: str, skill: str, now: int, source: str, intervals: Intervals) -> Entry:
    return {
        "id": entry_id,
        "skill": skill,
        "created_at": now,
        "source": source,
        "intervals": intervals,
        "state": "draft",
        "running": False,
        "last_tick": now,
        "reflection": "",
    }


def _validate_entry(value: Any, now: int) -> Entry:
    expected = set(_new("", "", 0, "", []))
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or not isinstance(value["id"], str)
        or not re.fullmatch(r"(?:[a-f0-9]{24}|observed-\d{4}-\d{2}-\d{2})", value["id"])
        or value["skill"] not in SKILLS
        or value["source"] not in ("timer", "observed", "manual")
        or value["state"] not in ("draft", "confirmed", "dismissed")
        or type(value["running"]) is not bool
        or type(value["created_at"]) is not int
        or not 0 <= value["created_at"] <= now
        or type(value["last_tick"]) is not int
        or not 0 <= value["last_tick"] <= now
        or value["reflection"] not in REFLECTIONS
        or not isinstance(value["intervals"], list)
    ):
        raise ValueError("invalid_backup")
    for pair in value["intervals"]:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(type(n) is not int for n in pair)
            or not 0 <= pair[0] < pair[1] <= now
        ):
            raise ValueError("invalid_backup")
    # A local activity day can exceed 24 hours across a DST transition.
    # Timer/manual sessions still obey the recording cap. The HTTP byte limit
    # bounds import size without rejecting our own exports after many pauses.
    maximum = 26 * 3600 if value["source"] == "observed" else MAX_SESSION_SECONDS
    if value["intervals"] != union(value["intervals"]) or seconds(value["intervals"]) > maximum:
        raise ValueError("invalid_backup")
    return dict(value, running=False)


def _goals(value: Any) -> dict[str, int | None]:
    if not isinstance(value, dict) or set(value) - set(SKILLS):
        raise ValueError("invalid_goal")
    if any(n is not None and (type(n) is not int or not 1 <= n <= 100000) for n in value.values()):
        raise ValueError("invalid_goal")
    return dict(value)


def change(store: Store, body: dict[str, Any], now: datetime, schedule: ScheduleProfile) -> None:
    """Apply closed commands atomically; validation failure leaves no partial write."""
    entry: Entry | None
    action = body.get("action")
    fields = {
        "enable": {"enabled"},
        "start": {"skill"},
        "resume": {"id"},
        "pause": {"id"},
        "heartbeat": {"id"},
        "finish": {"id"},
        "dismiss": {"id"},
        "confirm": {"id", "skill", "seconds", "reflection"},
        "goal": {"skill", "hours"},
        "clear": {"confirm"},
        "restore": {"backup"},
        "manual": {"skill", "start", "end", "reflection"},
    }
    if (
        not isinstance(action, str)
        or action not in fields
        or set(body) != fields[action] | {"action"}
    ):
        raise ValueError("invalid_request")
    stamp = int(now.timestamp())
    with store.practice_time_transaction() as conn:
        active = _expire(conn, stamp)
        if action == "clear":
            if body["confirm"] is not True:
                raise ValueError("confirmation_required")
            conn.execute("DELETE FROM practice_time_entries")
            conn.execute("DELETE FROM practice_time_settings")
            return
        if action == "restore":
            backup = body["backup"]
            if (
                not isinstance(backup, dict)
                or set(backup) != {"schema", "entries", "goals"}
                or backup["schema"] != SCHEMA
                or not isinstance(backup["entries"], list)
                or len(backup["entries"]) > 50000
            ):
                raise ValueError("invalid_backup")
            incoming = [_validate_entry(value, stamp) for value in backup["entries"]]
            goals = _goals(backup["goals"])
            existing = {entry["id"]: entry for entry in _entries(conn)}
            if active:
                raise ValueError("finish_active_first")
            if len({entry["id"] for entry in incoming}) != len(incoming):
                raise ValueError("invalid_backup")
            for imported in incoming:
                if imported["id"] in existing and imported != existing[imported["id"]]:
                    raise ValueError("backup_conflict")
                existing[imported["id"]] = imported
            intervals = [
                pair
                for entry in existing.values()
                if entry["state"] != "dismissed"
                for pair in entry["intervals"]
            ]
            if sum(b - a for a, b in intervals) != seconds(intervals):
                raise ValueError("overlapping_time")
            for imported in incoming:
                _save(conn, imported)
            _set(conn, "goals", {**goals, **_setting(conn, "goals", {})})
            _set(conn, "enabled", True)
            return
        if action == "enable":
            if type(body["enabled"]) is not bool:
                raise ValueError("invalid_request")
            if active and not body["enabled"]:
                _tick(conn, active, stamp, pause=True)
            _set(conn, "enabled", body["enabled"])
            return
        if not _setting(conn, "enabled", False):
            raise ValueError("history_disabled")
        if action == "manual":
            begin, end = body["start"], body["end"]
            if (
                body["skill"] not in SKILLS
                or body["reflection"] not in REFLECTIONS
                or type(begin) is not int
                or type(end) is not int
                or not 0 <= begin < end <= stamp
                or end - begin > MAX_SESSION_SECONDS
            ):
                raise ValueError("invalid_duration")
            if active:
                raise ValueError("finish_active_first")
            occupied = [
                pair
                for item in _entries(conn)
                if item["state"] != "dismissed"
                for pair in item["intervals"]
            ]
            if seconds(subtract([[begin, end]], occupied)) != end - begin:
                raise ValueError("overlapping_time")
            manual = _new(secrets.token_hex(12), body["skill"], begin, "manual", [[begin, end]])
            manual.update(state="confirmed", reflection=body["reflection"], last_tick=end)
            _save(conn, manual)
            return
        if action == "goal":
            goal = _goals({body["skill"]: body["hours"]})
            _set(conn, "goals", {**_setting(conn, "goals", {}), **goal})
            return
        if action == "start":
            if active:
                raise ValueError("finish_active_first")
            if body["skill"] not in SKILLS:
                raise ValueError("invalid_skill")
            entry = _new(secrets.token_hex(12), body["skill"], stamp, "timer", [])
            entry["running"] = True
            _set(conn, "breaks", _breaks(conn, stamp))
            _set(conn, "break_since", None)
            _save(conn, entry)
            _set(conn, "active", entry["id"])
            return
        entry = None
        if isinstance(body.get("id"), str) and body["id"].startswith("observed-"):
            suggestion = next(
                (s for s in _suggestions(store, conn, now, schedule) if s["id"] == body["id"]), None
            )
            if suggestion:
                entry = _new(
                    suggestion["id"],
                    "ai-development",
                    suggestion["intervals"][0][0],
                    "observed",
                    suggestion["intervals"],
                )
        entry = entry or _entry(conn, body.get("id"))
        if action in ("heartbeat", "pause", "resume", "finish"):
            if not active or entry["id"] != active["id"]:
                raise ValueError("session_changed")
            if action == "resume":
                if seconds(entry["intervals"]) >= MAX_SESSION_SECONDS:
                    raise ValueError("session_limit")
                # A delayed duplicate resume cannot move an already-running clock forward.
                if not entry["running"]:
                    entry.update(running=True, last_tick=stamp)
                    _set(conn, "breaks", _breaks(conn, stamp))
                    _set(conn, "break_since", None)
                    _save(conn, entry)
            else:
                _tick(conn, entry, stamp, pause=action != "heartbeat")
                if action == "finish":
                    _set(conn, "active", None)
            return
        if active and entry["id"] == active["id"]:
            raise ValueError("finish_active_first")
        if action == "dismiss":
            entry.update(state="dismissed", running=False)
        elif action == "confirm":
            amount = body["seconds"]
            if (
                entry["state"] == "dismissed"
                or body["skill"] not in SKILLS
                or body["reflection"] not in REFLECTIONS
                or type(amount) is not int
                or not 0 < amount <= seconds(entry["intervals"])
            ):
                raise ValueError("invalid_duration")
            other = [
                pair
                for value in _entries(conn)
                if value["id"] != entry["id"] and value["state"] == "confirmed"
                for pair in value["intervals"]
            ]
            if seconds(subtract(entry["intervals"], other)) != seconds(entry["intervals"]):
                raise ValueError("overlapping_time")
            entry.update(
                skill=body["skill"],
                state="confirmed",
                reflection=body["reflection"],
                intervals=_trim(entry["intervals"], amount),
                running=False,
            )
        _save(conn, entry)


def export_history(store: Store, now: datetime) -> dict[str, Any]:
    with store.practice_time_transaction() as conn:
        _expire(conn, int(now.timestamp()))
        return {
            "schema": SCHEMA,
            "goals": _setting(conn, "goals", {}),
            "entries": [dict(entry, running=False) for entry in _entries(conn)],
        }


def reading(store: Store, now: datetime, schedule: ScheduleProfile) -> dict[str, Any]:
    stamp = int(now.timestamp())
    with store.practice_time_transaction() as conn:
        active = _expire(conn, stamp)
        enabled = _setting(conn, "enabled", False)
        entries = _entries(conn)
        goals = _setting(conn, "goals", {})
        today = now.astimezone(schedule.zone).date()
        monday = today - timedelta(days=today.weekday())
        week_start = int(datetime.combine(monday, time(), schedule.zone).timestamp())
        totals = []
        for skill, label in SKILLS.items():
            parts = [
                pair
                for entry in entries
                if entry["skill"] == skill and entry["state"] == "confirmed"
                for pair in entry["intervals"]
            ]
            total = seconds(parts)
            hours = total / 3600
            next_milestone = next(
                (n for n in (1, 5, 10, 25, 50, 100, 150, 250, 500, 1000) if n > hours),
                (int(hours // 500) + 1) * 500,
            )
            totals.append(
                {
                    "id": skill,
                    "label": label,
                    "confirmed_seconds": total,
                    "week_seconds": seconds([[max(a, week_start), b] for a, b in parts]),
                    "next_milestone_hours": next_milestone,
                    "goal_hours": goals.get(skill),
                }
            )

        def public(entry: Entry) -> Entry:
            return {
                **{key: entry[key] for key in ("id", "skill", "source", "state", "reflection")},
                "day": datetime.fromtimestamp(entry["created_at"], schedule.zone)
                .date()
                .isoformat(),
                "seconds": seconds(entry["intervals"]),
                "running": entry["running"],
                "last_tick": entry["last_tick"],
            }

        return {
            "enabled": enabled,
            "skills": totals,
            "active": public(active) if active else None,
            "week_start": monday.isoformat(),
            "timezone": schedule.timezone_name,
            "pending": [
                public(entry)
                for entry in sorted(entries, key=lambda e: e["created_at"], reverse=True)
                if entry["state"] == "draft" and (not active or entry["id"] != active["id"])
            ],
            "history": [
                public(entry)
                for entry in sorted(entries, key=lambda e: e["created_at"], reverse=True)
                if entry["state"] == "confirmed"
            ][:100],
            "suggestions": [
                {key: value for key, value in item.items() if key != "intervals"}
                for item in _suggestions(store, conn, now, schedule)
            ]
            if enabled
            else [],
        }
