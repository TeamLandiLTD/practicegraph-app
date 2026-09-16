"""Pluggable aggregate storage (FR-API-4): production-shaped SQLite plus an
in-memory implementation for tests. Append-only-biased: emits are inserted,
idempotent on emit_id and authenticated source/day, and never mutated. The only
removal is whole-row retention pruning by day (``prune_days_before``)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol

SERVER_SCHEMA_VERSION = 2

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS emits(
            emit_id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            day TEXT NOT NULL,
            received_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_emits_org_day ON emits(org_id, day);
        """,
    ),
    (
        2,
        """
        ALTER TABLE emits ADD COLUMN source_id TEXT;
        CREATE UNIQUE INDEX emits_source_day ON emits(org_id, source_id, day)
        WHERE source_id IS NOT NULL;
    """,
    ),
)


class ServerStorage(Protocol):
    def put_emit(
        self,
        org_id: str,
        emit_id: str,
        day: str,
        payload: dict[str, Any],
        received_at: str,
        *,
        source_id: str | None = None,
    ) -> bool:
        """Store at most one contribution per authenticated source/day.

        Historical/unattributed imports remain stored but never enter a cohort.
        True if newly stored; repeated emit IDs or source/day pairs are no-ops.
        """
        ...

    def payloads_by_day(
        self, org_id: str, from_day: str, to_day: str
    ) -> dict[str, list[dict[str, Any]]]:
        """All stored payloads grouped by day within [from_day, to_day]."""
        ...

    def prune_days_before(self, cutoff_day: str) -> int:
        """Retention: delete emits whose day sorts strictly before cutoff_day.

        Days are ISO YYYY-MM-DD strings, so plain string comparison is a
        deterministic date comparison. Returns the number of rows removed.
        """
        ...


class MemoryStorage:
    def __init__(self) -> None:
        self._emits: dict[str, tuple[str, str, dict[str, Any], str | None]] = {}
        import threading

        self._lock = threading.Lock()

    def put_emit(
        self,
        org_id: str,
        emit_id: str,
        day: str,
        payload: dict[str, Any],
        received_at: str,
        *,
        source_id: str | None = None,
    ) -> bool:
        with self._lock:
            if emit_id in self._emits or (
                source_id is not None
                and any(
                    (org, stored_day, source) == (org_id, day, source_id)
                    for org, stored_day, _, source in self._emits.values()
                )
            ):
                return False
            self._emits[emit_id] = (org_id, day, payload, source_id)
            return True

    def payloads_by_day(
        self, org_id: str, from_day: str, to_day: str
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        with self._lock:
            rows = list(self._emits.values())
        for stored_org, day, payload, source in rows:
            if source is not None and stored_org == org_id and from_day <= day <= to_day:
                grouped.setdefault(day, []).append(payload)
        return grouped

    def prune_days_before(self, cutoff_day: str) -> int:
        with self._lock:
            expired = [
                emit_id for emit_id, (_, day, _, _) in self._emits.items() if day < cutoff_day
            ]
            for emit_id in expired:
                del self._emits[emit_id]
            return len(expired)


class SqliteStorage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _migrate(self) -> None:
        conn = self._connect()
        try:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for version, script in _MIGRATIONS:
                if version <= current:
                    continue
                conn.executescript(script)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
        finally:
            conn.close()

    def put_emit(
        self,
        org_id: str,
        emit_id: str,
        day: str,
        payload: dict[str, Any],
        received_at: str,
        *,
        source_id: str | None = None,
    ) -> bool:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO emits(emit_id, org_id, day, received_at, payload_json, source_id)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (emit_id, org_id, day, received_at, json.dumps(payload, sort_keys=True), source_id),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def payloads_by_day(
        self, org_id: str, from_day: str, to_day: str
    ) -> dict[str, list[dict[str, Any]]]:
        conn = self._connect()
        try:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for day, payload_json in conn.execute(
                "SELECT day, payload_json FROM emits"
                " WHERE source_id IS NOT NULL AND org_id = ?"
                " AND day >= ? AND day <= ? ORDER BY day, emit_id",
                (org_id, from_day, to_day),
            ).fetchall():
                grouped.setdefault(day, []).append(json.loads(payload_json))
            return grouped
        finally:
            conn.close()

    def prune_days_before(self, cutoff_day: str) -> int:
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM emits WHERE day < ?", (cutoff_day,))
            conn.commit()
            return int(cursor.rowcount)
        finally:
            conn.close()
