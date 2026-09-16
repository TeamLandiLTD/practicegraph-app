"""Local setup-improvement journey. No repository contents or findings enter telemetry."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from practicegraph.analysis.capability_ledger import read_practice
from practicegraph.analysis.harness_playbooks import (
    TOOLS,
    compatible,
    safe_source,
    text_value,
    valid_entry,
)
from practicegraph.analysis.setup_inspection import CHECKS, FILES, LOCKS, STATES, inspect_project
from practicegraph.analysis.workunits import episodes_between
from practicegraph.catalog import feed_status, load_playbooks
from practicegraph.content import content_digest
from practicegraph.store import Store

SCHEMA = "practicegraph.setup-improvements/1"
ACTIVE_STATES = ("inspected", "prepared", "attempted")
STATES_ALL = (*ACTIVE_STATES, "reviewed", "dismissed")
FEEDBACK = ("helpful", "not_helpful", "unsure", "not_tried")
Record = dict[str, Any]


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _records(conn: sqlite3.Connection) -> list[Record]:
    return [json.loads(row[0]) for row in conn.execute("SELECT body FROM setup_improvements")]


def _save(conn: sqlite3.Connection, record: Record) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO setup_improvements VALUES (?,?)",
        (record["id"], json.dumps(record, sort_keys=True)),
    )


def _get(conn: sqlite3.Connection, key: object) -> Record:
    if not isinstance(key, str):
        raise ValueError("invalid_request")
    row = conn.execute("SELECT body FROM setup_improvements WHERE id=?", (key,)).fetchone()
    if not row:
        raise ValueError("missing_improvement")
    return dict(json.loads(row[0]))


def _active(records: list[Record]) -> Record | None:
    return next((r for r in records if r["state"] in ACTIVE_STATES), None)


def work_facts(store: Store, now: datetime, days: int = 30) -> list[Record]:
    """Use edited, idle episodes; model evidence is conservative session-file/day context."""
    start = (now - timedelta(days=days)).date()
    context: dict[str, set[tuple[str, str]]] = {}
    for session, tool, model in store.setup_session_context(
        start.isoformat(), now.date().isoformat()
    ):
        context.setdefault(session, set()).add((tool, model))
    results = []
    for episode in episodes_between(store, start, now.date()):
        if (
            episode.assistant_turns < 5
            or episode.last_ts > now - timedelta(minutes=30)
            or not episode.cwd_hash
            or episode.files_created + episode.files_edited == 0
        ):
            continue
        pairs = sorted(
            {pair for session in episode.session_keys for pair in context.get(session, ())}
        )
        tools = sorted({tool for tool, _model in pairs if tool in TOOLS})
        known = bool(pairs) and all(
            model and model != "unknown" and tool in TOOLS for tool, model in pairs
        )
        results.append(
            {
                "key": _hash(
                    [
                        episode.cwd_hash,
                        episode.branch_hash,
                        episode.first_ts.isoformat(),
                        list(episode.session_keys),
                    ]
                )[:24],
                "project": episode.cwd_hash,
                "branch": episode.branch_hash,
                "first": int(episode.first_ts.timestamp()),
                "last": int(episode.last_ts.timestamp()),
                "tests": episode.test_run_attempts,
                "tools": tools,
                "model_signature": _hash(pairs),
                "context_known": known,
            }
        )
    return sorted(results, key=lambda r: (r["last"], r["key"]))


def _project_facts(facts: list[Record], inspection: Record, tool: str) -> list[Record]:
    return [
        f
        for f in facts
        if f["project"] in inspection["project_ids"] and (not f["tools"] or tool in f["tools"])
    ]


def _current_entry(
    data_dir: Path, record: Record, now: datetime, versions: dict[str, str | None]
) -> tuple[Record | None, str]:
    edition = load_playbooks(data_dir)
    if edition is None:
        return None, "missing_catalog"
    inspection = record["inspection"]
    gap = (
        any(f["tests"] == 0 for f in record["baseline"])
        or not inspection["verification_mentioned"]
        or (not inspection["declared_checks"] and not inspection["pytest_config"])
    )
    if not gap:
        return None, "no_verification_gap"
    frozen = record.get("entry")
    if frozen:
        entry = next((e for e in edition.entries if e["id"] == frozen["id"]), None)
        if entry is None or entry["status"] == "withdrawn":
            return None, "withdrawn"
        if entry != frozen:
            return None, "edition_changed"
        candidates = [entry]
    else:
        candidates = sorted(edition.entries, key=lambda e: e["id"])
    if feed_status(data_dir, "harness-playbooks", now=now)["state"] != "current":
        return None, "stale_catalog"
    for entry in candidates:
        if (
            entry["status"] != "active"
            or entry["recipe"] != "verification_setup"
            or not set(entry["stacks"]) & set(record["inspection"]["stacks"])
        ):
            continue
        if (now.date() - datetime.fromisoformat(entry["reviewed_on"]).date()).days > 30:
            continue
        if compatible(entry, (record["tool"],), versions):
            return entry, "ready"
    return None, "no_compatible_playbook"


def render_brief(record: Record) -> str:
    """Version 1 renderer stays stable for exported records and their digest."""
    entry = record["entry"]
    inspection = record["inspection"]
    lines = [entry["title"], "", "Observed locally:"]
    baseline = record["baseline"]
    if baseline:
        count = sum(f["tests"] > 0 for f in baseline)
        lines.append(
            f"Verification attempts were visible in {count} of {len(baseline)} "
            "recent edited work episodes in the selected folder."
        )
    else:
        lines.append(
            "No matching edited work episodes were available. "
            "This is a requested setup check, not a measured failure."
        )
    if inspection["declared_checks"]:
        lines.append(
            "package.json declares these script names: "
            + ", ".join(inspection["declared_checks"])
            + ". Their commands were not run."
        )
    if inspection["pytest_config"]:
        lines.append("Pytest configuration was found. Its checks were not run.")
    if inspection["verification_mentioned"]:
        lines.append(
            "A recognized testing command is mentioned in the inspected guidance; "
            "that does not establish that the instructions are current."
        )
    if not inspection["declared_checks"] and not inspection["pytest_config"]:
        lines.append(
            "No supported verification declaration was found in the inspected files. "
            "Other checks may exist."
        )
    lines += [
        "",
        "Curated guidance (review before using):",
        entry["explanation"],
        entry["guidance"],
        "",
        "Task for the coding harness:",
        "Inspect this project's setup and choose the smallest relevant verification check. "
        "Review scripts before running them. Use the project's established environment and package "
        "manager. Run the check within your authorized scope, report its result, and propose a "
        "concise update to the project instructions showing how to repeat it. Preserve existing "
        "guidance and identify anything still unverified.",
        "",
        "Limitations:",
        entry["limitations"],
        "This brief did not execute commands or establish test success. "
        "Log absence is not proof that no testing occurred.",
        "",
        "Sources:",
        *entry["sources"],
        f"Playbook {entry['id']} revision {entry['revision']} · "
        f"edition {record['edition']['version']}.",
    ]
    return "\n".join(lines)


def _advance(
    record: Record, facts: list[Record], versions: dict[str, str | None], now: int
) -> bool:
    if (
        record["state"] not in ("attempted", "reviewed")
        or record["attempted_at"] is None
        or len(record["followup"]) >= 3
    ):
        return False
    # Completed snapshots survive ingestion retention and future context changes.
    baseline = record["baseline"]
    anchor = baseline[-1] if baseline else None
    if not anchor or not anchor["context_known"]:
        return False
    if not record["versions"].get(record["tool"]) or record["versions"] != versions:
        return False
    retained = {f["key"] for f in record["followup"]}
    changed = False
    for fact in _project_facts(facts, record["inspection"], record["tool"]):
        if fact["first"] <= record["attempted_at"] or fact["key"] in retained:
            continue
        if (
            not fact["context_known"]
            or fact["tools"] != anchor["tools"]
            or fact["model_signature"] != anchor["model_signature"]
            or fact["branch"] != anchor["branch"]
        ):
            continue
        record["followup"].append(fact)
        retained.add(fact["key"])
        changed = True
        if len(record["followup"]) == 3:
            break
    if changed:
        record["updated_at"] = now
    return changed


def _comparison(record: Record, versions: dict[str, str | None]) -> Record:
    before, after = record["baseline"], record["followup"]
    known = bool(before) and all(f["context_known"] for f in before)
    if len(after) >= 3:
        state = "observed"
    elif record["attempted_at"] is None:
        state = "not_attempted"
    elif not known or not record["versions"].get(record["tool"]):
        state = "context_unknown"
    elif record["versions"] != versions:
        state = "context_changed"
    else:
        state = "waiting"
    return {
        "state": state,
        "before_count": len(before),
        "after_count": len(after),
        "before_verified": sum(f["tests"] > 0 for f in before),
        "after_verified": sum(f["tests"] > 0 for f in after),
    }


def refresh_followups(store: Store, now: datetime, versions: dict[str, str | None]) -> int:
    """Capture later evidence during local collection, even while Practice is not open."""
    with store.local_transaction() as conn:
        pending = [
            r
            for r in _records(conn)
            if r["state"] in ("attempted", "reviewed")
            and r["attempted_at"] is not None
            and len(r["followup"]) < 3
        ]
    if not pending:
        return 0
    facts = work_facts(store, now, days=90)
    changed = 0
    with store.local_transaction() as conn:
        for record in _records(conn):
            if _advance(
                record, facts, {record["tool"]: versions.get(record["tool"])}, int(now.timestamp())
            ):
                _save(conn, record)
                changed += 1
    return changed


def reading(store: Store, data_dir: Path, now: datetime, versions: dict[str, str | None]) -> Record:
    versions = {tool: versions.get(tool) for tool in TOOLS}
    facts = work_facts(store, now)
    with store.local_transaction() as conn:
        records = _records(conn)
        for record in records:
            if _advance(
                record, facts, {record["tool"]: versions[record["tool"]]}, int(now.timestamp())
            ):
                _save(conn, record)
        active = _active(records)

    def public(record: Record) -> Record:
        _entry, availability = _current_entry(data_dir, record, now, versions)
        return {
            **record,
            "availability": availability,
            "brief": render_brief(record) if record["entry"] else None,
            "comparison": _comparison(record, {record["tool"]: versions[record["tool"]]}),
        }

    history = sorted(
        (r for r in records if r["state"] not in ACTIVE_STATES),
        key=lambda r: r["created_at"],
        reverse=True,
    )[:50]
    return {
        "schema": SCHEMA,
        "catalog": feed_status(data_dir, "harness-playbooks", now=now),
        "versions": versions,
        "active": public(active) if active else None,
        "history": [public(r) for r in history],
        "verification_gaps": sum(
            f["tests"] == 0
            for f in facts
            if f["last"] >= int((now - timedelta(days=7)).timestamp())
        ),
        "coach_active": read_practice(store) is not None,
    }


def change(
    store: Store, data_dir: Path, body: Record, now: datetime, versions: dict[str, str | None]
) -> Record | None:
    fields = {
        "inspect": {"id", "path", "tool", "label"},
        "prepare": {"id"},
        "handoff": {"id"},
        "attempt": {"id"},
        "review": {"id", "feedback"},
        "dismiss": {"id"},
        "link_practice": {"id", "practice_id"},
        "clear": {"confirm"},
        "restore": {"backup"},
    }
    action = body.get("action")
    if (
        not isinstance(action, str)
        or action not in fields
        or set(body) != fields[action] | {"action"}
    ):
        raise ValueError("invalid_request")
    stamp = int(now.timestamp())
    inspection = None
    if action == "inspect":
        if (
            not isinstance(body["id"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", body["id"])
            or body["tool"] not in TOOLS
            or not text_value(body["label"], 80)
            or "\n" in body["label"]
        ):
            raise ValueError("invalid_request")
        if read_practice(store) is not None:
            raise ValueError("finish_coach_first")
        with store.local_transaction() as conn:
            existing = _active(_records(conn))
            if existing:
                if existing["id"] == body["id"]:
                    return None  # Lost-response retry cannot create another inspection.
                raise ValueError("finish_improvement_first")
        inspection = inspect_project(body["path"])
    facts = work_facts(store, now) if action in ("inspect", "attempt", "review") else []
    with store.local_transaction() as conn:
        records = _records(conn)
        if action == "clear":
            if body["confirm"] is not True:
                raise ValueError("confirmation_required")
            conn.execute("DELETE FROM setup_improvements")
            return None
        if action == "restore":
            _restore(conn, body["backup"], stamp)
            return None
        if action == "inspect":
            if _active(records):
                raise ValueError("finish_improvement_first")
            assert inspection is not None
            tool = body["tool"]
            matched = _project_facts(facts, inspection, tool)
            # Compare only one recorded branch/tool/model context, frozen at inspection.
            anchor = matched[-1] if matched else None
            baseline = [
                f
                for f in matched
                if anchor
                and f["branch"] == anchor["branch"]
                and f["tools"] == anchor["tools"]
                and f["model_signature"] == anchor["model_signature"]
            ][-3:]
            record = {
                "id": body["id"],
                "label": body["label"].strip(),
                "created_at": stamp,
                "updated_at": stamp,
                "state": "inspected",
                "tool": tool,
                "versions": {tool: versions.get(tool)},
                "inspection": inspection,
                "baseline": baseline,
                "followup": [],
                "entry": None,
                "edition": None,
                "brief_sha256": None,
                "attempted_at": None,
                "feedback": None,
                "practice_id": None,
            }
            if any(r["id"] == record["id"] for r in records):
                raise ValueError("record_conflict")
            _save(conn, record)
            return None
        record = _get(conn, body.get("id"))
        if action == "prepare":
            if record["state"] not in ("inspected", "prepared"):
                raise ValueError("invalid_transition")
            entry, reason = _current_entry(data_dir, record, now, versions)
            if entry is None:
                raise ValueError(reason)
            if record["state"] == "prepared":
                return None
            edition = load_playbooks(data_dir)
            assert edition is not None
            status = feed_status(data_dir, "harness-playbooks", now=now)
            record["entry"] = entry
            record["edition"] = {
                "version": edition.version,
                "published_on": edition.published_on,
                "entry_sha256": content_digest(entry),
                "source_url": status["source_url"],
            }
            record["state"] = "prepared"
            record["brief_sha256"] = hashlib.sha256(render_brief(record).encode()).hexdigest()
        elif action == "handoff":
            if record["state"] not in ("prepared", "attempted", "reviewed"):
                raise ValueError("invalid_transition")
            entry, reason = _current_entry(data_dir, record, now, versions)
            if entry is None:
                raise ValueError(reason)
            return {"brief": render_brief(record)}
        elif action == "attempt":
            if record["state"] == "attempted":
                return None
            if record["state"] != "prepared":
                raise ValueError("invalid_transition")
            if versions.get(record["tool"]) != record["versions"].get(record["tool"]):
                raise ValueError("context_changed")
            entry, reason = _current_entry(data_dir, record, now, versions)
            if entry is None:
                raise ValueError(reason)
            record.update(state="attempted", attempted_at=stamp)
        elif action == "review":
            if body["feedback"] not in FEEDBACK or record["state"] not in (
                "prepared",
                "attempted",
                "reviewed",
            ):
                raise ValueError("invalid_transition")
            if record["attempted_at"] is None and body["feedback"] != "not_tried":
                raise ValueError("mark_attempt_first")
            if record["attempted_at"] is not None and body["feedback"] == "not_tried":
                raise ValueError("invalid_transition")
            _advance(record, facts, {record["tool"]: versions.get(record["tool"])}, stamp)
            record.update(state="reviewed", feedback=body["feedback"])
        elif action == "dismiss":
            if record["state"] == "reviewed":
                raise ValueError("invalid_transition")
            record["state"] = "dismissed"
        elif action == "link_practice":
            if record["attempted_at"] is None:
                raise ValueError("mark_attempt_first")
            key = body["practice_id"]
            if key is not None:
                if not isinstance(key, str):
                    raise ValueError("invalid_request")
                row = conn.execute(
                    "SELECT body FROM practice_time_entries WHERE id=?", (key,)
                ).fetchone()
                if not row or json.loads(row[0])["state"] != "confirmed":
                    raise ValueError("unconfirmed_practice")
            record["practice_id"] = key
        record["updated_at"] = stamp
        _save(conn, record)
    return None


def export_history(store: Store) -> Record:
    with store.local_transaction() as conn:
        return {"schema": SCHEMA, "records": _records(conn)}


def _hex(value: object, size: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[a-f0-9]{{{size}}}", value) is not None


def _valid_inspection(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "project_ids",
        "stacks",
        "files",
        "declared_checks",
        "pytest_config",
        "verification_mentioned",
        "lockfiles",
    }:
        return False
    if (
        not isinstance(value["project_ids"], list)
        or not 1 <= len(value["project_ids"]) <= 8
        or not all(_hex(k, 16) for k in value["project_ids"])
        or any(type(value[k]) is not bool for k in ("pytest_config", "verification_mentioned"))
    ):
        return False
    for key, allowed in (
        ("stacks", ("python", "javascript")),
        ("declared_checks", CHECKS),
        ("lockfiles", LOCKS),
    ):
        items = value[key]
        if (
            not isinstance(items, list)
            or len(items) > len(allowed)
            or any(i not in allowed for i in items)
        ):
            return False
    files = value["files"]
    return (
        isinstance(files, list)
        and len(files) == len(FILES)
        and all(
            isinstance(f, dict)
            and set(f) == {"name", "state", "sha256"}
            and f["name"] == name
            and f["state"] in STATES
            and (f["sha256"] is None or _hex(f["sha256"], 64))
            for f, name in zip(files, FILES, strict=True)
        )
    )


def _valid_fact(value: Any, stamp: int) -> bool:
    fields = {
        "key",
        "project",
        "branch",
        "first",
        "last",
        "tests",
        "tools",
        "model_signature",
        "context_known",
    }
    return (
        isinstance(value, dict)
        and set(value) == fields
        and _hex(value["key"], 24)
        and _hex(value["project"], 16)
        and (value["branch"] == "" or _hex(value["branch"], 16))
        and type(value["first"]) is int
        and type(value["last"]) is int
        and 0 <= value["first"] <= value["last"] <= stamp
        and type(value["tests"]) is int
        and 0 <= value["tests"] <= 1000000
        and isinstance(value["tools"], list)
        and len(value["tools"]) <= 2
        and all(t in TOOLS for t in value["tools"])
        and len(set(value["tools"])) == len(value["tools"])
        and (not value["context_known"] or bool(value["tools"]))
        and _hex(value["model_signature"], 64)
        and type(value["context_known"]) is bool
    )


def _validate_record(value: Any, stamp: int) -> Record:
    fields = {
        "id",
        "label",
        "created_at",
        "updated_at",
        "state",
        "tool",
        "versions",
        "inspection",
        "baseline",
        "followup",
        "entry",
        "edition",
        "brief_sha256",
        "attempted_at",
        "feedback",
        "practice_id",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid_backup")
    try:
        if (
            not _hex(value["id"], 32)
            or not text_value(value["label"], 80)
            or "\n" in value["label"]
            or value["state"] not in STATES_ALL
            or value["tool"] not in TOOLS
            or type(value["created_at"]) is not int
            or type(value["updated_at"]) is not int
            or not 0 <= value["created_at"] <= value["updated_at"] <= stamp
            or not _valid_inspection(value["inspection"])
            or not isinstance(value["versions"], dict)
            or set(value["versions"]) != {value["tool"]}
            or any(
                v is not None
                and (not isinstance(v, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,63}", v))
                for v in value["versions"].values()
            )
        ):
            raise ValueError()
        for name in ("baseline", "followup"):
            values = value[name]
            if (
                not isinstance(values, list)
                or len(values) > 3
                or any(
                    not _valid_fact(f, stamp)
                    or f["project"] not in value["inspection"]["project_ids"]
                    for f in values
                )
                or len({f["key"] for f in values}) != len(values)
            ):
                raise ValueError()
        attempt = value["attempted_at"]
        if attempt is not None and (
            type(attempt) is not int or not value["created_at"] <= attempt <= value["updated_at"]
        ):
            raise ValueError()
        if value["state"] == "attempted" and attempt is None:
            raise ValueError()
        if value["feedback"] is not None and value["feedback"] not in FEEDBACK:
            raise ValueError()
        if value["state"] == "reviewed" and value["feedback"] is None:
            raise ValueError()
        if (
            (value["state"] in ("inspected", "prepared") and attempt is not None)
            or (value["state"] != "reviewed" and value["feedback"] is not None)
            or (
                value["state"] == "reviewed"
                and ((attempt is None) != (value["feedback"] == "not_tried"))
            )
        ):
            raise ValueError()
        if value["practice_id"] is not None and not (
            attempt is not None
            and isinstance(value["practice_id"], str)
            and re.fullmatch(r"(?:[a-f0-9]{24}|observed-\d{4}-\d{2}-\d{2})", value["practice_id"])
        ):
            raise ValueError()
        if value["entry"] is None:
            if (
                value["edition"] is not None
                or value["brief_sha256"] is not None
                or value["state"] not in ("inspected", "dismissed")
                or attempt is not None
            ):
                raise ValueError()
        else:
            edition = value["edition"]
            if (
                value["state"] == "inspected"
                or not valid_entry(value["entry"])
                or value["entry"]["recipe"] != "verification_setup"
                or not isinstance(edition, dict)
                or set(edition) != {"version", "published_on", "entry_sha256", "source_url"}
                or not isinstance(edition["version"], str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", edition["version"])
                or datetime.fromisoformat(edition["published_on"]).date().isoformat()
                != edition["published_on"]
                or edition["entry_sha256"] != content_digest(value["entry"])
                or (edition["source_url"] is not None and not safe_source(edition["source_url"]))
                or value["brief_sha256"] != hashlib.sha256(render_brief(value).encode()).hexdigest()
            ):
                raise ValueError()
        if value["followup"] and (
            attempt is None or any(f["first"] <= attempt for f in value["followup"])
        ):
            raise ValueError()
        baseline = value["baseline"]
        if any(f["last"] > value["created_at"] for f in baseline):
            raise ValueError()
        if value["followup"] and (not baseline or not value["versions"].get(value["tool"])):
            raise ValueError()
        if baseline:
            anchor = baseline[-1]
            if any(
                f["branch"] != anchor["branch"]
                or f["tools"] != anchor["tools"]
                or f["model_signature"] != anchor["model_signature"]
                for f in [*baseline, *value["followup"]]
            ):
                raise ValueError()
            if value["followup"] and not all(
                f["context_known"] for f in [*baseline, *value["followup"]]
            ):
                raise ValueError()
        if set(f["key"] for f in baseline) & set(f["key"] for f in value["followup"]):
            raise ValueError()
        return dict(value)
    except (TypeError, ValueError, KeyError):
        raise ValueError("invalid_backup") from None


def _restore(conn: sqlite3.Connection, backup: Any, stamp: int) -> None:
    if (
        not isinstance(backup, dict)
        or set(backup) != {"schema", "records"}
        or backup["schema"] != SCHEMA
        or not isinstance(backup["records"], list)
        or len(backup["records"]) > 5000
    ):
        raise ValueError("invalid_backup")
    incoming = [_validate_record(r, stamp) for r in backup["records"]]
    if len({r["id"] for r in incoming}) != len(incoming):
        raise ValueError("invalid_backup")
    existing = {r["id"]: r for r in _records(conn)}
    for record in incoming:
        if record["id"] in existing and record != existing[record["id"]]:
            raise ValueError("record_conflict")
        existing[record["id"]] = record
    if sum(r["state"] in ACTIVE_STATES for r in existing.values()) > 1:
        raise ValueError("finish_improvement_first")
    for record in incoming:
        _save(conn, record)
