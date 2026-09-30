"""Voluntary local learning choices; no log-based diagnosis or automatic time credit."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from practicegraph.analysis.training_catalog import (
    GOALS,
    REVIEW_DAYS,
    TOOLS,
    TrainingEdition,
    valid_entry,
)
from practicegraph.catalog import feed_status, load_training
from practicegraph.content import content_digest
from practicegraph.store import Store

SCHEMA = "practicegraph.learning/1"
KEY = "local_training_state_v1"
STATES = ("saved", "in_progress", "completed", "dismissed")
DEFAULT_PREFS: dict[str, Any] = {
    "goal": "",
    "tool": "any",
    "free_only": True,
    "max_minutes": None,
}


def _valid_prefs(prefs: Any) -> bool:
    return bool(
        isinstance(prefs, dict)
        and set(prefs) == set(DEFAULT_PREFS)
        and isinstance(prefs["goal"], str)
        and prefs["goal"] in ("", *GOALS)
        and isinstance(prefs["tool"], str)
        and prefs["tool"] in TOOLS
        and type(prefs["free_only"]) is bool
        and (
            prefs["max_minutes"] is None
            or (type(prefs["max_minutes"]) is int and prefs["max_minutes"] in (60, 240, 1200))
        )
    )


def _state(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {"prefs": dict(DEFAULT_PREFS), "records": []}
    try:
        value = json.loads(row[0])
        if not isinstance(value, dict) or set(value) != {"prefs", "records"}:
            raise ValueError
        if not _valid_prefs(value["prefs"]) or not isinstance(value["records"], list):
            raise ValueError
        if len(value["records"]) > 100:
            raise ValueError
        for record in value["records"]:
            if (
                not isinstance(record, dict)
                or set(record) != {"entry", "edition", "state", "updated_on"}
                or not valid_entry(record["entry"])
                or record["state"] not in STATES
                or not isinstance(record["edition"], str)
                or date.fromisoformat(record["updated_on"]).isoformat() != record["updated_on"]
            ):
                raise ValueError
        ids = [r["entry"]["id"] for r in value["records"]]
        if (
            len(set(ids)) != len(ids)
            or sum(r["state"] in ("saved", "in_progress") for r in value["records"]) > 1
        ):
            raise ValueError
        return dict(value)
    except (ValueError, TypeError, KeyError):
        raise ValueError("training_state_invalid") from None


def _eligible(entry: dict[str, Any], prefs: dict[str, Any], today: date) -> bool:
    age = (today - date.fromisoformat(entry["reviewed_on"])).days
    return bool(
        entry["status"] == "active"
        and 0 <= age <= REVIEW_DAYS
        and entry["goal"] == prefs["goal"]
        and (prefs["tool"] == "any" or "any" in entry["tools"] or prefs["tool"] in entry["tools"])
        and (not prefs["free_only"] or entry["cost"] == "free")
        and (
            prefs["max_minutes"] is None
            or (
                entry["duration_minutes"] is not None
                and entry["duration_minutes"] <= prefs["max_minutes"]
            )
        )
    )


def _choices(
    data_dir: Path,
    state: dict[str, Any],
    now: datetime,
    edition: TrainingEdition | None,
) -> list[dict[str, Any]]:
    if edition is None or feed_status(data_dir, "training", now=now)["state"] != "current":
        return []
    if any(r["state"] in ("saved", "in_progress") for r in state["records"]):
        return []
    excluded = {r["entry"]["id"] for r in state["records"]}
    # Edition order expresses editorial priority; no invented match/proficiency score.
    return [
        entry
        for entry in edition.entries
        if entry["id"] not in excluded
        and _eligible(entry, state["prefs"], now.astimezone(UTC).date())
    ]


def reading(store: Store, data_dir: Path, now: datetime) -> dict[str, Any]:
    with store.local_transaction() as conn:
        state = _state(conn)
    edition = load_training(data_dir)
    choices = _choices(data_dir, state, now, edition)
    status = feed_status(data_dir, "training", now=now)
    empty_reason = None
    if not choices and not any(r["state"] in ("saved", "in_progress") for r in state["records"]):
        if not state["prefs"]["goal"]:
            empty_reason = "choose_goal"
        elif edition is None or status["state"] != "current":
            empty_reason = "catalog_unavailable"
        else:
            today = now.astimezone(UTC).date()
            broad = {**DEFAULT_PREFS, "goal": state["prefs"]["goal"], "free_only": False}
            if not any(_eligible(entry, broad, today) for entry in edition.entries):
                empty_reason = "no_coverage"
            elif not any(_eligible(entry, state["prefs"], today) for entry in edition.entries):
                empty_reason = "filters"
            else:
                empty_reason = "already_recorded"
    current = {entry["id"]: entry for entry in edition.entries} if edition else {}
    records = []
    for record in state["records"]:
        entry = current.get(record["entry"]["id"])
        availability = "current"
        if entry is None or entry["status"] == "retired":
            availability = "unavailable"
        elif content_digest(entry) != content_digest(record["entry"]):
            availability = "changed"
        elif (
            not 0
            <= (now.astimezone(UTC).date() - date.fromisoformat(entry["reviewed_on"])).days
            <= REVIEW_DAYS
        ):
            availability = "stale"
        records.append({**record, "availability": availability})
    return {
        "schema": SCHEMA,
        "prefs": state["prefs"],
        "goals": GOALS,
        "tools": TOOLS,
        "feed": status,
        "empty_reason": empty_reason,
        "suggestion": choices[0] if choices else None,
        "alternatives": choices[1:3],
        "records": list(reversed(records)),
    }


def change(store: Store, data_dir: Path, body: dict[str, Any], now: datetime) -> None:
    with store.local_transaction() as conn:
        if body == {"action": "clear", "confirm": True} and body.get("confirm") is True:
            conn.execute("DELETE FROM meta WHERE key=?", (KEY,))
            return
        state = _state(conn)
        action = body.get("action")
        if action == "preferences" and set(body) == {"action", *DEFAULT_PREFS}:
            prefs = {key: body[key] for key in DEFAULT_PREFS}
            if not _valid_prefs(prefs):
                raise ValueError("invalid_training_preferences")
            state["prefs"] = prefs
        elif action == "save" and set(body) == {"action", "id", "edition"}:
            edition = load_training(data_dir)
            if not edition or body["edition"] != edition.version:
                raise ValueError("training_edition_changed")
            chosen = next(
                (e for e in _choices(data_dir, state, now, edition) if e["id"] == body["id"]), None
            )
            if chosen is None:
                raise ValueError("training_choice_unavailable")
            if len(state["records"]) >= 100:
                raise ValueError("training_history_full")
            state["records"].append(
                {
                    "entry": chosen,
                    "edition": edition.version,
                    "state": "saved",
                    "updated_on": now.date().isoformat(),
                }
            )
        elif action == "resume" and set(body) == {"action", "id"}:
            record = next((r for r in state["records"] if r["entry"]["id"] == body["id"]), None)
            if record is None or record["state"] != "dismissed":
                raise ValueError("invalid_training_progress")
            if any(r["state"] in ("saved", "in_progress") for r in state["records"]):
                raise ValueError("training_choice_unavailable")
            # An explicit return to the original snapshot, not a fresh recommendation.
            record["state"] = "saved"
            record["updated_on"] = now.date().isoformat()
        elif action == "progress" and set(body) == {"action", "id", "state"}:
            record = next((r for r in state["records"] if r["entry"]["id"] == body["id"]), None)
            if record is None or body["state"] not in ("in_progress", "completed", "dismissed"):
                raise ValueError("invalid_training_progress")
            allowed = {
                "saved": ("in_progress", "dismissed"),
                "in_progress": ("completed", "dismissed"),
                "completed": (),
                "dismissed": (),
            }
            if body["state"] != record["state"] and body["state"] not in allowed[record["state"]]:
                raise ValueError("invalid_training_progress")
            record["state"] = body["state"]
            record["updated_on"] = now.date().isoformat()
        else:
            raise ValueError("invalid_training_request")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (KEY, json.dumps(state))
        )


def export_history(store: Store) -> dict[str, Any]:
    with store.local_transaction() as conn:
        return {"schema": "practicegraph.learning-backup/1", **_state(conn)}
