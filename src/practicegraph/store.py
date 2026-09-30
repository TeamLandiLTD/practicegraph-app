"""Single local state store (FR-CFG-3).

One SQLite database in the data directory holds all local state. Writes go
through this module only (single-writer discipline, C-4); SQLite WAL + busy
timeout make a report-generation collision with a user command resolve by
retry, not corruption (NFR-REL-2). Migrations are additive, idempotent, and
monotonic: a rollback re-run never downgrades.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, NamedTuple

STATE_DB_NAME = "state.db"
STORE_SCHEMA_VERSION = 16

# Closed emit-queue statuses (FR-EMT-2).
QUEUE_STATUSES: tuple[str, ...] = ("pending", "retry_wait", "sent", "dead_letter")

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS emit_queue(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emit_id TEXT NOT NULL UNIQUE,
            day TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_code TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT,
            created_at TEXT NOT NULL,
            sent_at TEXT
        );
        CREATE TABLE IF NOT EXISTS engagement(
            day TEXT NOT NULL,
            counter TEXT NOT NULL,
            value INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(day, counter)
        );
        """,
    ),
    (
        # M1: incremental reads (FR-SRC-7) and daily history (FR-ANL-7);
        # state for suggestions (FR-RPT-8), focus tips (FR-FOC-3), and
        # alert dedupe (FR-ALR-2). File identity is a hash — full local
        # paths are never persisted (NFR-PRV-1, FR-SRC-7 "identity hash").
        2,
        """
        CREATE TABLE IF NOT EXISTS file_cursors(
            source_id TEXT NOT NULL,
            path_hash TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            parser_version INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_id, path_hash)
        );
        CREATE TABLE IF NOT EXISTS file_contributions(
            path_hash TEXT NOT NULL,
            source_id TEXT NOT NULL,
            day TEXT NOT NULL,
            tool TEXT NOT NULL,
            model TEXT NOT NULL,
            assistant_turns INTEGER NOT NULL DEFAULT 0,
            user_turns INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            tool_calls INTEGER NOT NULL DEFAULT 0,
            retries INTEGER NOT NULL DEFAULT 0,
            interruptions INTEGER NOT NULL DEFAULT 0,
            cost_micro_usd INTEGER NOT NULL DEFAULT 0,
            unpriced_turns INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(path_hash, day, tool, model)
        );
        CREATE INDEX IF NOT EXISTS idx_contrib_day ON file_contributions(day);
        CREATE TABLE IF NOT EXISTS file_health(
            path_hash TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            seen INTEGER NOT NULL,
            parsed INTEGER NOT NULL,
            skipped INTEGER NOT NULL,
            malformed INTEGER NOT NULL,
            unknown_field INTEGER NOT NULL,
            unsupported INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS activity_marks(
            path_hash TEXT NOT NULL,
            day TEXT NOT NULL,
            session_key TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            kind TEXT NOT NULL,
            tool_calls INTEGER NOT NULL DEFAULT 0,
            retries INTEGER NOT NULL DEFAULT 0,
            interruptions INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_marks_day ON activity_marks(day);
        CREATE TABLE IF NOT EXISTS turn_keys(
            source_id TEXT NOT NULL,
            turn_key TEXT NOT NULL,
            path_hash TEXT NOT NULL,
            PRIMARY KEY(source_id, turn_key)
        );
        CREATE TABLE IF NOT EXISTS suggestion_state(
            suggestion_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tip_dismissals(
            tip_id TEXT PRIMARY KEY,
            dismissed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fired_alerts(
            category TEXT NOT NULL,
            day TEXT NOT NULL,
            reason TEXT NOT NULL,
            fired_at TEXT NOT NULL,
            delivery TEXT NOT NULL,
            PRIMARY KEY(category, day)
        );
        """,
    ),
    (
        # 1h-TTL cache-write subset (priced at 2x input vs 1.25x for 5m).
        3,
        """
        ALTER TABLE file_contributions
            ADD COLUMN cache_creation_1h_tokens INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        # P1 execution quality: per-day rework and
        # command-outcome counters, plus the per-event command-failure mark
        # (amendment 1 — the P2 inter-event-gap input). Counters only.
        4,
        """
        ALTER TABLE file_contributions
            ADD COLUMN rework_edits INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN commands_run INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN commands_failed INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN commands_slow INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE activity_marks
            ADD COLUMN command_failures INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        # P2 session character: per-day and
        # per-event context-compaction counters — the marathon-session input.
        # Counters only, appended last (the storage contract).
        5,
        """
        ALTER TABLE file_contributions
            ADD COLUMN compactions INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE activity_marks
            ADD COLUMN compactions INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        # P3 environment context: the interactive
        # flag (sidechain records score no behavioral metrics; legacy rows
        # default interactive) and the cwd/branch identity hashes — hashes,
        # never values (the path_identity precedent). Appended in this order
        # (the storage contract).
        6,
        """
        ALTER TABLE activity_marks
            ADD COLUMN interactive INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE activity_marks
            ADD COLUMN cwd_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE activity_marks
            ADD COLUMN branch_hash TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        # P4 economics: provider rate-limit gauge
        # readings from token_count events, mirroring activity_marks per-file
        # atomic replace semantics. Two integers per reading — percent in
        # tenths and the window length. LOCAL-ONLY forever (never on the wire;
        # the field-name scan enforces it).
        7,
        """
        CREATE TABLE IF NOT EXISTS rate_limit_marks(
            path_hash TEXT NOT NULL,
            day TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            used_pct_tenths INTEGER NOT NULL,
            window_minutes INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rate_limit_day ON rate_limit_marks(day);
        """,
    ),
    (
        # Waved-through approvals: the per-event "go"-class human-reply flag
        # (length-derived boolean, never text). Appended LAST — the append-only
        # mark contract. Legacy rows default to 0 (not short).
        8,
        """
        ALTER TABLE activity_marks
            ADD COLUMN short_reply INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        # Human attention continuity: positive source-classified human-origin
        # signal. Legacy rows fail closed as background/unknown.
        9,
        """
        ALTER TABLE activity_marks
            ADD COLUMN human_initiated INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        # W2.0 session grain: money
        # and volume at SESSION scale. `file_contributions` aggregates at
        # day x tool x model and `activity_marks` carries no tokens, so no
        # existing table can answer "what did that session cost" — this one
        # can. Same per-file atomic replace lifecycle as marks; counters plus
        # the two session timestamps, never content. `session_key` is the
        # local-only key activity_marks already stores and is NEVER rendered
        # or emitted (the field-name scan enforces it).
        10,
        """
        CREATE TABLE IF NOT EXISTS session_totals(
            path_hash TEXT NOT NULL,
            day TEXT NOT NULL,
            session_key TEXT NOT NULL,
            tool TEXT NOT NULL,
            first_ts TEXT NOT NULL,
            last_ts TEXT NOT NULL,
            assistant_turns INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_micro_usd INTEGER NOT NULL DEFAULT 0,
            unpriced_turns INTEGER NOT NULL DEFAULT 0,
            compactions INTEGER NOT NULL DEFAULT 0,
            tool_calls INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(path_hash, day, session_key)
        );
        CREATE INDEX IF NOT EXISTS idx_session_day ON session_totals(day);
        """,
    ),
    (
        # W3.1 denominator: commands the bounded
        # classifier anchor-matched as git commits / test runs. ATTEMPTS, not
        # outcomes; INTEGER counters appended last on both tables (the
        # storage contract) — the schema-shape test pins that no TEXT column
        # ever rides along with them.
        11,
        """
        ALTER TABLE file_contributions
            ADD COLUMN git_commit_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN test_run_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE session_totals
            ADD COLUMN git_commit_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE session_totals
            ADD COLUMN test_run_attempts INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        # W4 work spans: maximal runs of
        # assistant turns on one (session, project, branch) with no intra-run
        # gap over the span threshold — the finest deterministic stratum the
        # work-unit reading folds at read time (episodes chain spans; threads
        # group them by project x branch). Same per-file atomic lifecycle as
        # marks; counters and two identity HASHES only (the activity_marks
        # cwd/branch precedent — values never stored). Split at midnight; the
        # reader re-chains. LOCAL-ONLY forever: the wire scan pins the
        # vocabulary out of every payload.
        12,
        """
        CREATE TABLE IF NOT EXISTS work_spans(
            path_hash TEXT NOT NULL,
            day TEXT NOT NULL,
            session_key TEXT NOT NULL,
            cwd_hash TEXT NOT NULL,
            branch_hash TEXT NOT NULL,
            first_ts TEXT NOT NULL,
            last_ts TEXT NOT NULL,
            assistant_turns INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_micro_usd INTEGER NOT NULL DEFAULT 0,
            unpriced_turns INTEGER NOT NULL DEFAULT 0,
            git_commit_attempts INTEGER NOT NULL DEFAULT 0,
            test_run_attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_work_spans_day ON work_spans(day);
        """,
    ),
    (
        # W4.1 artifact grain: what a turn LEFT BEHIND beyond commits —
        # non-scratch files created, the document subset, writes landing
        # outside the working directory (the delivery signal), and edits to
        # existing files. INTEGER counters appended last on both tables (the
        # storage contract); classified transiently at parse, paths never
        # stored (sources.classify_written_file).
        13,
        """
        ALTER TABLE file_contributions
            ADD COLUMN files_created INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN doc_files_created INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN export_writes INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE file_contributions
            ADD COLUMN files_edited INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE work_spans
            ADD COLUMN files_created INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE work_spans
            ADD COLUMN doc_files_created INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE work_spans
            ADD COLUMN export_writes INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE work_spans
            ADD COLUMN files_edited INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        14,
        """
        CREATE TABLE IF NOT EXISTS practice_time_entries(
            id TEXT PRIMARY KEY,
            body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS practice_time_settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    (
        15,
        """
        CREATE TABLE IF NOT EXISTS setup_improvements(
            id TEXT PRIMARY KEY,
            body TEXT NOT NULL
        );
        """,
    ),
    (
        16,
        """
        ALTER TABLE rate_limit_marks ADD COLUMN window_kind TEXT NOT NULL DEFAULT '';
        ALTER TABLE rate_limit_marks ADD COLUMN resets_at TEXT NOT NULL DEFAULT '';
        ALTER TABLE rate_limit_marks ADD COLUMN account_hash TEXT NOT NULL DEFAULT '';
        ALTER TABLE rate_limit_marks ADD COLUMN pool_hash TEXT NOT NULL DEFAULT '';
        CREATE TABLE session_evidence(
            path_hash TEXT NOT NULL, day TEXT NOT NULL, session_key TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE INDEX idx_session_evidence ON session_evidence(session_key);
        """,
    ),
)


class ContributionRow(NamedTuple):
    """Named view over one contribution values list (the positional layout of
    ``day_usage_rows`` / ``usage_rows_between`` / ``replace_file_data``).
    Positional order is the storage contract — new counters are APPENDED,
    never inserted — and this accessor is the readable way to consume it."""

    assistant_turns: int
    user_turns: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cache_creation_tokens: int
    reasoning_tokens: int
    tool_calls: int
    retries: int
    interruptions: int
    cost_micro_usd: int
    unpriced_turns: int
    cache_creation_1h_tokens: int
    rework_edits: int
    commands_run: int
    commands_failed: int
    commands_slow: int
    compactions: int
    git_commit_attempts: int
    test_run_attempts: int
    files_created: int
    doc_files_created: int
    export_writes: int
    files_edited: int

    @classmethod
    def from_values(cls, values: list[int]) -> ContributionRow:
        # Rows written before a column existed arrive short; zero-extend so
        # every accessor stays total (the append-only contract's read side).
        if len(values) < len(cls._fields):
            values = [*values, *([0] * (len(cls._fields) - len(values)))]
        return cls(*values)


# The write-side width of one contributions values list (columns after the
# (path_hash, source_id, day, tool, model) prefix).
_CONTRIBUTION_WIDTH = len(ContributionRow._fields)


# One activity mark: (session_key, ts_utc, kind, tool_calls, retries,
# interruptions, command_failures, compactions, interactive, cwd_hash,
# branch_hash, short_reply, human_initiated). marks_between prefixes the day. The positional
# layout is the append-only storage contract — new columns land LAST — and
# every counter read goes through the MARK_* index constants below, never
# rest[-1] or a bare literal (that ends the append-arity bug class).
MarkRow = tuple[str, str, str, int, int, int, int, int, int, str, str, int, int]
DayMarkRow = tuple[str, str, str, str, int, int, int, int, int, int, str, str, int, int]

# One rate-limit gauge row (P4): (day, ts_utc, used_pct_tenths, window_minutes).
# Same per-file-atomic lifecycle as activity marks; local-only forever.
RateLimitMarkRow = tuple[str, str, int, int] | tuple[str, str, int, int, str, str, str, str]

# One session-totals write row (W2.0): (day, session_key, tool, first_ts,
# last_ts, assistant_turns, input_tokens, cached_tokens,
# cache_creation_tokens, output_tokens, cost_micro_usd, unpriced_turns,
# compactions, tool_calls, git_commit_attempts, test_run_attempts). Same
# per-file-atomic lifecycle as marks; the last two appended by W3.1.
SessionTotalRow = tuple[
    str,
    str,
    str,
    str,
    str,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
]


# One work-span write row (W4): (day, session_key, cwd_hash, branch_hash,
# first_ts, last_ts, assistant_turns, input_tokens, cached_tokens,
# cache_creation_tokens, output_tokens, cost_micro_usd, unpriced_turns,
# git_commit_attempts, test_run_attempts). Same per-file-atomic lifecycle.
WorkSpanRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
    int,
]


class SessionRow(NamedTuple):
    """One session's aggregate, already merged across the files and days it
    spans (the read shape). ``session_key`` is a LOCAL-ONLY identity — it is
    never rendered on a surface and never emitted."""

    session_key: str
    tool: str
    first_ts: str
    last_ts: str
    assistant_turns: int
    input_tokens: int
    cached_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    cost_micro_usd: int
    unpriced_turns: int
    compactions: int
    tool_calls: int
    git_commit_attempts: int = 0
    test_run_attempts: int = 0


# Named indexes into MarkRow. Day-prefixed DayMarkRow rows shift every index
# by +1 (slice off the day first and read through these).
MARK_TOOL_CALLS: Final = 3
MARK_RETRIES: Final = 4
MARK_INTERRUPTIONS: Final = 5
MARK_FAILURES: Final = 6
MARK_COMPACTIONS: Final = 7
MARK_INTERACTIVE: Final = 8
MARK_CWD_HASH: Final = 9
MARK_BRANCH_HASH: Final = 10
MARK_SHORT_REPLY: Final = 11
MARK_HUMAN_INITIATED: Final = 12


@dataclass(frozen=True, slots=True)
class QueueRow:
    row_id: int
    emit_id: str
    day: str
    payload_json: str
    status: str
    error_code: str | None
    attempts: int


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds")


class Store:
    """Thin, transactional wrapper over the state database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def in_data_dir(cls, data_dir: Path) -> Store:
        return cls(data_dir / STATE_DB_NAME)

    def exists(self) -> bool:
        return self.path.is_file()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def schema_version(self) -> int:
        """Read the schema version without creating the database (FR-DIA-1)."""
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            return int(row[0])
        finally:
            conn.close()

    def migrate(self) -> None:
        """Apply pending migrations. Monotonic: never downgrades (FR-CFG-3)."""
        from practicegraph.secureio import private_directory

        private_directory(self.path.parent)
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

    # -- emit queue (FR-EMT-2) ------------------------------------------------

    def queue_day(self, day: str, emit_id: str, payload_json: str, now: datetime) -> bool:
        """Queue one day's aggregate exactly once. Returns False when the day
        is already claimed (any status — dead-lettered days stay claimed)."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO emit_queue(emit_id, day, payload_json, status, created_at)"
                " VALUES(?, ?, ?, 'pending', ?)",
                (emit_id, day, payload_json, _iso(now)),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def day_queued(self, day: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute("SELECT 1 FROM emit_queue WHERE day = ?", (day,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def due_emits(self, now: datetime) -> list[QueueRow]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, emit_id, day, payload_json, status, error_code, attempts"
                " FROM emit_queue"
                " WHERE status = 'pending'"
                "    OR (status = 'retry_wait' AND next_attempt_at <= ?)"
                " ORDER BY day",
                (_iso(now),),
            ).fetchall()
            return [QueueRow(*row) for row in rows]
        finally:
            conn.close()

    def mark_sent(self, row_id: int, now: datetime) -> None:
        self._update_queue(
            "UPDATE emit_queue SET status='sent', error_code=NULL, sent_at=?,"
            " next_attempt_at=NULL WHERE id=?",
            (_iso(now), row_id),
        )

    def mark_retry(
        self, row_id: int, error_code: str, next_attempt_at: datetime, attempts: int
    ) -> None:
        self._update_queue(
            "UPDATE emit_queue SET status='retry_wait', error_code=?, attempts=?,"
            " next_attempt_at=? WHERE id=?",
            (error_code, attempts, _iso(next_attempt_at), row_id),
        )

    def mark_dead_letter(self, row_id: int, error_code: str) -> None:
        """Terminal (FR-EMT-2/3): dead-lettered emits are never retried."""
        self._update_queue(
            "UPDATE emit_queue SET status='dead_letter', error_code=?,"
            " next_attempt_at=NULL WHERE id=?",
            (error_code, row_id),
        )

    def _update_queue(self, sql: str, params: tuple[object, ...]) -> None:
        conn = self._connect()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def queue_counts(self) -> dict[str, int]:
        conn = self._connect()
        try:
            counts = dict.fromkeys(QUEUE_STATUSES, 0)
            for status, n in conn.execute(
                "SELECT status, COUNT(*) FROM emit_queue GROUP BY status"
            ).fetchall():
                if status in counts:
                    counts[status] = n
            return counts
        finally:
            conn.close()

    def queue_error_counts(self) -> dict[str, int]:
        conn = self._connect()
        try:
            return dict(
                conn.execute(
                    "SELECT error_code, COUNT(*) FROM emit_queue"
                    " WHERE error_code IS NOT NULL GROUP BY error_code ORDER BY error_code"
                ).fetchall()
            )
        finally:
            conn.close()

    def next_unsent_payload(self) -> str | None:
        """Earliest not-yet-sent payload — the Privacy Center's exact preview
        of what would cross the wire next (FR-RPT-5)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM emit_queue"
                " WHERE status IN ('pending', 'retry_wait') ORDER BY day LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # -- engagement counters (FR-RPT-7) ---------------------------------------

    def engagement_add(self, day: str, counter: str, amount: int = 1) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO engagement(day, counter, value) VALUES(?, ?, ?)"
                " ON CONFLICT(day, counter) DO UPDATE SET value = value + ?",
                (day, counter, amount, amount),
            )
            conn.commit()
        finally:
            conn.close()

    def engagement_for_day(self, day: str) -> dict[str, int]:
        conn = self._connect()
        try:
            return dict(
                conn.execute(
                    "SELECT counter, value FROM engagement WHERE day = ?", (day,)
                ).fetchall()
            )
        finally:
            conn.close()

    # -- meta ------------------------------------------------------------------

    def meta_set(self, key: str, value: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def meta_get(self, key: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def meta_claim_interval(self, key: str, now: datetime, interval_s: int) -> bool:
        """Atomically claim a time-gated action across store connections."""
        claim_time = now.astimezone(UTC)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            if row is not None:
                try:
                    previous = datetime.fromisoformat(row[0]).astimezone(UTC)
                except (TypeError, ValueError):
                    previous = None
                if previous is not None and (claim_time - previous).total_seconds() < interval_s:
                    conn.rollback()
                    return False
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, _iso(claim_time)),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def meta_set_json(self, key: str, value: dict[str, str]) -> None:
        self.meta_set(key, json.dumps(value, sort_keys=True))

    # -- incremental read cursors (FR-SRC-7) -----------------------------------

    def cursor_get(self, source_id: str, path_hash: str) -> tuple[int, int, int] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT size, mtime_ns, parser_version FROM file_cursors"
                " WHERE source_id = ? AND path_hash = ?",
                (source_id, path_hash),
            ).fetchone()
            return (row[0], row[1], row[2]) if row else None
        finally:
            conn.close()

    def replace_file_data(
        self,
        source_id: str,
        path_hash: str,
        cursor: tuple[int, int, int],
        contributions: list[tuple[str, str, str, list[int]]],
        marks: list[DayMarkRow],
        health: object,
        turn_keys: tuple[str, ...],
        now: datetime,
        rate_limit_marks: list[RateLimitMarkRow] | None = None,
        session_totals: list[SessionTotalRow] | None = None,
        work_spans: list[WorkSpanRow] | None = None,
        session_evidence: list[tuple[str, str, str]] | None = None,
    ) -> None:
        """Replace everything one file contributes, atomically. Re-parsing a
        changed file can never double-count (M1 correctness rule)."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            for table in (
                "file_contributions",
                "activity_marks",
                "turn_keys",
                "rate_limit_marks",
                "session_totals",
                "work_spans",
                "session_evidence",
            ):
                conn.execute(f"DELETE FROM {table} WHERE path_hash = ?", (path_hash,))
            conn.execute("DELETE FROM file_health WHERE path_hash = ?", (path_hash,))
            for day, tool, model, values in contributions:
                # Additive transition (the marks/session precedent): callers
                # built before a counter existed hand shorter lists; missing
                # counters are zero.
                if len(values) < _CONTRIBUTION_WIDTH:
                    values = [*values, *([0] * (_CONTRIBUTION_WIDTH - len(values)))]
                conn.execute(
                    "INSERT INTO file_contributions(path_hash, source_id, day, tool,"
                    " model, assistant_turns, user_turns, input_tokens, output_tokens,"
                    " cached_tokens, cache_creation_tokens, reasoning_tokens,"
                    " tool_calls, retries, interruptions, cost_micro_usd,"
                    " unpriced_turns, cache_creation_1h_tokens, rework_edits,"
                    " commands_run, commands_failed, commands_slow, compactions,"
                    " git_commit_attempts, test_run_attempts, files_created,"
                    " doc_files_created, export_writes, files_edited)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (path_hash, source_id, day, tool, model, *values),
                )
            for mark in marks:
                # Column order = the DayMarkRow contract (append-only).
                # Accept pre-v9 in-process callers conservatively during the
                # additive transition; missing origin means not human.
                stored_mark = (*mark, 0) if len(mark) == 13 else mark
                conn.execute(
                    "INSERT INTO activity_marks(path_hash, day, session_key, ts_utc,"
                    " kind, tool_calls, retries, interruptions, command_failures,"
                    " compactions, interactive, cwd_hash, branch_hash, short_reply,"
                    " human_initiated)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (path_hash, *stored_mark),
                )
            for gauge in rate_limit_marks or []:
                stored_gauge = (*gauge, "", "", "", "") if len(gauge) == 4 else gauge
                conn.execute(
                    "INSERT INTO rate_limit_marks(path_hash, day, ts_utc,"
                    " used_pct_tenths, window_minutes, window_kind, resets_at,"
                    " account_hash, pool_hash) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (path_hash, *stored_gauge),
                )
            conn.executemany(
                "INSERT INTO session_evidence(path_hash, day, session_key, body)"
                " VALUES(?, ?, ?, ?)",
                [(path_hash, *row) for row in session_evidence or []],
            )
            for session in session_totals or []:
                # W2.0: one row per (file, day, session) — a session that
                # crosses midnight or spans files writes several, merged by
                # the reader. Column order = the SessionTotalRow contract.
                # Pre-W3.1 in-process callers may still hand 14-field rows
                # during the additive transition; missing counters are zero
                # (the marks-row precedent above).
                if len(session) == 14:
                    session = (*session, 0, 0)  # type: ignore[assignment]
                conn.execute(
                    "INSERT INTO session_totals(path_hash, day, session_key,"
                    " tool, first_ts, last_ts, assistant_turns, input_tokens,"
                    " cached_tokens, cache_creation_tokens, output_tokens,"
                    " cost_micro_usd, unpriced_turns, compactions, tool_calls,"
                    " git_commit_attempts, test_run_attempts)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (path_hash, *session),
                )
            for span in work_spans or []:
                if len(span) == 15:
                    span = (*span, 0, 0, 0, 0)  # type: ignore[assignment]
                # W4: one row per (file, day-piece, span). Column order = the
                # WorkSpanRow contract (append-only).
                conn.execute(
                    "INSERT INTO work_spans(path_hash, day, session_key,"
                    " cwd_hash, branch_hash, first_ts, last_ts,"
                    " assistant_turns, input_tokens, cached_tokens,"
                    " cache_creation_tokens, output_tokens, cost_micro_usd,"
                    " unpriced_turns, git_commit_attempts, test_run_attempts,"
                    " files_created, doc_files_created, export_writes,"
                    " files_edited)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?)",
                    (path_hash, *span),
                )
            seen = getattr(health, "seen", 0)
            conn.execute(
                "INSERT INTO file_health(path_hash, source_id, seen, parsed, skipped,"
                " malformed, unknown_field, unsupported) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    path_hash,
                    source_id,
                    seen,
                    getattr(health, "parsed", 0),
                    getattr(health, "skipped", 0),
                    getattr(health, "malformed", 0),
                    getattr(health, "unknown_field", 0),
                    getattr(health, "unsupported", 0),
                ),
            )
            for key in turn_keys:
                conn.execute(
                    "INSERT OR REPLACE INTO turn_keys(source_id, turn_key, path_hash)"
                    " VALUES(?, ?, ?)",
                    (source_id, key, path_hash),
                )
            conn.execute(
                "INSERT INTO file_cursors(source_id, path_hash, size, mtime_ns,"
                " parser_version, updated_at) VALUES(?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(source_id, path_hash) DO UPDATE SET size = excluded.size,"
                " mtime_ns = excluded.mtime_ns, parser_version = excluded.parser_version,"
                " updated_at = excluded.updated_at",
                (source_id, path_hash, cursor[0], cursor[1], cursor[2], _iso(now)),
            )
            conn.commit()
        finally:
            conn.close()

    def invalidate_all_cursors(self) -> int:
        """Force a full re-parse on the next scan — used when the active rate
        card changes so persisted cost estimates get re-priced (FR-SRC-7
        cursor-invalidation semantics, applied to configuration changes)."""
        conn = self._connect()
        try:
            cursor = conn.execute("DELETE FROM file_cursors")
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    def prune_missing_files(self, source_id: str, live_hashes: set[str]) -> int:
        """Drop state for files that no longer exist on disk (log rotation and
        deletion recovery, NFR-CMP-2).

        The stale set derives from file_health, NOT file_cursors: every file
        that ever contributed has a health row, while cursors are routinely
        DELETED by invalidate_all_cursors (rate-card changes, grain bumps).
        Deriving from cursors made pruning contingent on no gate having fired
        this scan — found 2026-07-29, when a new gate ran against an adopted
        store and its cursor-less contributions silently survived a prune
        that had always caught them before."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT path_hash FROM file_health WHERE source_id = ?", (source_id,)
            ).fetchall()
            stale = [row[0] for row in rows if row[0] not in live_hashes]
            conn.execute("BEGIN")
            for path_hash in stale:
                for table in (
                    "file_cursors",
                    "file_contributions",
                    "activity_marks",
                    "turn_keys",
                    "file_health",
                    "rate_limit_marks",
                    "session_totals",
                    "work_spans",
                    "session_evidence",
                ):
                    conn.execute(f"DELETE FROM {table} WHERE path_hash = ?", (path_hash,))
            conn.commit()
            return len(stale)
        finally:
            conn.close()

    def seen_turn_keys(self, source_id: str, exclude_path_hash: str) -> set[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT turn_key FROM turn_keys WHERE source_id = ? AND path_hash <> ?",
                (source_id, exclude_path_hash),
            ).fetchall()
            return {row[0] for row in rows}
        finally:
            conn.close()

    # -- daily history (FR-ANL-7) ----------------------------------------------

    def day_usage_rows(self, day: str) -> list[tuple[str, str, list[int]]]:
        """Per (tool, model) sums for one UTC day, sorted. The values layout
        is the ContributionRow contract (append-only)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT tool, model, SUM(assistant_turns), SUM(user_turns),"
                " SUM(input_tokens), SUM(output_tokens), SUM(cached_tokens),"
                " SUM(cache_creation_tokens), SUM(reasoning_tokens), SUM(tool_calls),"
                " SUM(retries), SUM(interruptions), SUM(cost_micro_usd),"
                " SUM(unpriced_turns), SUM(cache_creation_1h_tokens),"
                " SUM(rework_edits), SUM(commands_run), SUM(commands_failed),"
                " SUM(commands_slow), SUM(compactions),"
                " SUM(git_commit_attempts), SUM(test_run_attempts),"
                " SUM(files_created), SUM(doc_files_created),"
                " SUM(export_writes), SUM(files_edited)"
                " FROM file_contributions WHERE day = ?"
                " GROUP BY tool, model ORDER BY tool, model",
                (day,),
            ).fetchall()
            return [(row[0], row[1], [int(v) for v in row[2:]]) for row in rows]
        finally:
            conn.close()

    def session_count_for_day(self, day: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(DISTINCT session_key) FROM activity_marks WHERE day = ?",
                (day,),
            ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    def marks_for_day(self, day: str) -> list[MarkRow]:
        """(session_key, ts_utc, kind, tool_calls, retries, interruptions,
        command_failures, compactions, interactive, cwd_hash, branch_hash,
        short_reply, human_initiated) sorted by timestamp — the focus-metric
        input (FR-FOC-1).
        New columns are appended, never inserted; read via the MARK_*
        constants."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_key, ts_utc, kind, tool_calls, retries,"
                " interruptions, command_failures, compactions, interactive,"
                " cwd_hash, branch_hash, short_reply, human_initiated"
                " FROM activity_marks WHERE day = ? ORDER BY ts_utc, session_key",
                (day,),
            ).fetchall()
            return [tuple(row) for row in rows]
        finally:
            conn.close()

    def marks_between(self, from_day: str, to_day: str) -> list[DayMarkRow]:
        """day-prefixed MarkRow tuples, ordered — the multi-day activity
        timeline for profile/rhythm stats (MARK_* indexes shift +1; slice the
        day off and read through the constants)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT day, session_key, ts_utc, kind, tool_calls, retries,"
                " interruptions, command_failures, compactions, interactive,"
                " cwd_hash, branch_hash, short_reply, human_initiated"
                " FROM activity_marks"
                " WHERE day >= ? AND day <= ? ORDER BY day, ts_utc, session_key",
                (from_day, to_day),
            ).fetchall()
            return [tuple(row) for row in rows]
        finally:
            conn.close()

    def latest_rate_limit(self, day: str) -> tuple[int, int] | None:
        """The day's most recent provider rate-limit reading, as
        (used_pct_tenths, window_minutes) — None when the day has no gauge.
        LOCAL-ONLY input for the informational approaching-quota finding (P4);
        this value never feeds an emit."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT used_pct_tenths, window_minutes FROM rate_limit_marks"
                " WHERE day = ? ORDER BY ts_utc DESC LIMIT 1",
                (day,),
            ).fetchone()
            return (int(row[0]), int(row[1])) if row else None
        finally:
            conn.close()

    def rate_limit_marks_for_day(self, day: str) -> list[tuple[str, int, int]]:
        """Every provider rate-limit reading for one UTC day, as
        (ts_utc, used_pct_tenths, window_minutes) ordered by time — the input
        the runway tile buckets by window for its latest/peak reads (A1). The
        day is the caller's, so it is not repeated in each row. LOCAL-ONLY like
        every rate-limit read; this never feeds an emit."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT ts_utc, used_pct_tenths, window_minutes"
                " FROM rate_limit_marks WHERE day = ? ORDER BY ts_utc",
                (day,),
            ).fetchall()
            return [(str(r[0]), int(r[1]), int(r[2])) for r in rows]
        finally:
            conn.close()

    def rate_limit_snapshots_for_day(self, day: str) -> list[tuple[str, str, int, int]]:
        """Local gauge provenance; ambiguous legacy file ownership stays unknown."""
        conn = self._connect()
        try:
            return [
                (str(source), str(ts), int(used), int(window))
                for source, ts, used, window in conn.execute(
                    "SELECT COALESCE(c.source_id, 'unknown'), r.ts_utc,"
                    " r.used_pct_tenths, r.window_minutes FROM rate_limit_marks r"
                    " LEFT JOIN (SELECT path_hash, MIN(source_id) AS source_id"
                    " FROM file_cursors GROUP BY path_hash HAVING COUNT(DISTINCT source_id) = 1) c"
                    " ON c.path_hash = r.path_hash WHERE r.day = ?"
                    " ORDER BY r.ts_utc, c.source_id, r.window_minutes, r.used_pct_tenths",
                    (day,),
                )
            ]
        finally:
            conn.close()

    def quota_details_for_day(self, day: str) -> list[tuple[Any, ...]]:
        conn = self._connect()
        try:
            return list(conn.execute(
                "SELECT COALESCE(c.source_id, 'unknown'), r.ts_utc, r.used_pct_tenths,"
                " r.window_minutes, r.window_kind, r.resets_at, r.account_hash, r.pool_hash"
                " FROM rate_limit_marks r LEFT JOIN (SELECT path_hash, MIN(source_id) source_id"
                " FROM file_cursors GROUP BY path_hash HAVING COUNT(DISTINCT source_id)=1) c"
                " ON c.path_hash=r.path_hash WHERE r.day=?"
                " ORDER BY r.ts_utc, r.used_pct_tenths", (day,),
            ))
        finally:
            conn.close()

    def session_evidence_rows(self) -> list[tuple[str, str]]:
        conn = self._connect()
        try:
            return [(str(key), str(body)) for key, body in conn.execute(
                "SELECT session_key, body FROM session_evidence ORDER BY day, path_hash"
            )]
        finally:
            conn.close()

    @contextmanager
    def local_transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize private journal changes, including concurrent browser windows."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    practice_time_transaction = local_transaction

    def setup_session_context(self, from_day: str, to_day: str) -> list[tuple[str, str, str]]:
        """Recorded tool/model sets at session-file/day grain; never an emit input."""
        conn = self._connect()
        try:
            return [(str(key), str(tool), str(model)) for key, tool, model in conn.execute(
                "SELECT DISTINCT s.session_key, s.tool, COALESCE(c.model, '')"
                " FROM session_totals s LEFT JOIN file_contributions c"
                " ON c.path_hash=s.path_hash AND c.day=s.day AND c.tool=s.tool"
                " WHERE s.day >= ? AND s.day <= ? ORDER BY s.session_key, s.tool, c.model",
                (from_day, to_day),
            )]
        finally:
            conn.close()

    def work_spans_between(self, from_day: str, to_day: str) -> list[tuple[Any, ...]]:
        """Every work span touching the day range, raw (W4): the reader folds
        them into episodes/threads, so no grouping happens here beyond a
        stable order — (cwd, branch, first_ts) makes the chaining fold a
        single forward pass (INV-6 stable). Columns follow the WorkSpanRow
        contract minus the day (the fold re-derives days from timestamps).
        LOCAL-ONLY — work-unit grain never feeds an emit."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_key, cwd_hash, branch_hash, first_ts, last_ts,"
                " assistant_turns, input_tokens, cached_tokens,"
                " cache_creation_tokens, output_tokens, cost_micro_usd,"
                " unpriced_turns, git_commit_attempts, test_run_attempts,"
                " files_created, doc_files_created, export_writes,"
                " files_edited"
                " FROM work_spans WHERE day >= ? AND day <= ?"
                " ORDER BY cwd_hash, branch_hash, first_ts, session_key",
                (from_day, to_day),
            ).fetchall()
            return [
                (
                    str(r[0]),
                    str(r[1]),
                    str(r[2]),
                    str(r[3]),
                    str(r[4]),
                    *[int(v) for v in r[5:]],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def sessions_between(self, from_day: str, to_day: str) -> list[SessionRow]:
        """Every session touching the day range, merged across the files and
        days it spans (W2.0): counters summed, ``first_ts``/``last_ts`` taken
        as the true span. Ordered by cost then key so the read is stable
        (INV-6). LOCAL-ONLY — session grain never feeds an emit."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_key, MIN(tool), MIN(first_ts), MAX(last_ts),"
                " SUM(assistant_turns), SUM(input_tokens), SUM(cached_tokens),"
                " SUM(cache_creation_tokens), SUM(output_tokens),"
                " SUM(cost_micro_usd), SUM(unpriced_turns), SUM(compactions),"
                " SUM(tool_calls), SUM(git_commit_attempts),"
                " SUM(test_run_attempts)"
                " FROM session_totals WHERE day >= ? AND day <= ?"
                " GROUP BY session_key"
                " ORDER BY SUM(cost_micro_usd) DESC, session_key",
                (from_day, to_day),
            ).fetchall()
            return [
                SessionRow(
                    session_key=str(r[0]),
                    tool=str(r[1]),
                    first_ts=str(r[2]),
                    last_ts=str(r[3]),
                    assistant_turns=int(r[4]),
                    input_tokens=int(r[5]),
                    cached_tokens=int(r[6]),
                    cache_creation_tokens=int(r[7]),
                    output_tokens=int(r[8]),
                    cost_micro_usd=int(r[9]),
                    unpriced_turns=int(r[10]),
                    compactions=int(r[11]),
                    tool_calls=int(r[12]),
                    git_commit_attempts=int(r[13]),
                    test_run_attempts=int(r[14]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    def spend_series(self, from_day: str, to_day: str) -> list[tuple[str, int, int, int]]:
        """(day, cost_micro_usd, tokens_total, assistant_turns) per active day."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT day, SUM(cost_micro_usd),"
                " SUM(input_tokens + output_tokens + cached_tokens +"
                "     cache_creation_tokens),"
                " SUM(assistant_turns)"
                " FROM file_contributions WHERE day >= ? AND day <= ?"
                " GROUP BY day ORDER BY day",
                (from_day, to_day),
            ).fetchall()
            return [(row[0], int(row[1]), int(row[2]), int(row[3])) for row in rows]
        finally:
            conn.close()

    def active_days(self, from_day: str, to_day: str) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT day FROM file_contributions"
                " WHERE day >= ? AND day <= ? ORDER BY day",
                (from_day, to_day),
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def usage_rows_between(self, from_day: str, to_day: str) -> list[tuple[str, str, list[int]]]:
        """Per (tool, model) sums over an inclusive day range, sorted — the
        range-view input (same value layout as day_usage_rows)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT tool, model, SUM(assistant_turns), SUM(user_turns),"
                " SUM(input_tokens), SUM(output_tokens), SUM(cached_tokens),"
                " SUM(cache_creation_tokens), SUM(reasoning_tokens), SUM(tool_calls),"
                " SUM(retries), SUM(interruptions), SUM(cost_micro_usd),"
                " SUM(unpriced_turns), SUM(cache_creation_1h_tokens),"
                " SUM(rework_edits), SUM(commands_run), SUM(commands_failed),"
                " SUM(commands_slow), SUM(compactions)"
                " FROM file_contributions WHERE day >= ? AND day <= ?"
                " GROUP BY tool, model ORDER BY tool, model",
                (from_day, to_day),
            ).fetchall()
            return [(row[0], row[1], [int(v) for v in row[2:]]) for row in rows]
        finally:
            conn.close()

    def usage_rows_by_day(
        self, from_day: str, to_day: str
    ) -> list[tuple[str, str, str, list[int]]]:
        """Day-prefixed per (tool, model) sums over an inclusive day range,
        sorted — the Advisor-receipts input (day granularity keeps per-family
        active-day counts derivable, the marks_between precedent). The values
        layout is the ContributionRow contract (append-only)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT day, tool, model, SUM(assistant_turns), SUM(user_turns),"
                " SUM(input_tokens), SUM(output_tokens), SUM(cached_tokens),"
                " SUM(cache_creation_tokens), SUM(reasoning_tokens), SUM(tool_calls),"
                " SUM(retries), SUM(interruptions), SUM(cost_micro_usd),"
                " SUM(unpriced_turns), SUM(cache_creation_1h_tokens),"
                " SUM(rework_edits), SUM(commands_run), SUM(commands_failed),"
                " SUM(commands_slow), SUM(compactions),"
                " SUM(git_commit_attempts), SUM(test_run_attempts),"
                " SUM(files_created), SUM(doc_files_created),"
                " SUM(export_writes), SUM(files_edited)"
                " FROM file_contributions WHERE day >= ? AND day <= ?"
                " GROUP BY day, tool, model ORDER BY day, tool, model",
                (from_day, to_day),
            ).fetchall()
            return [(row[0], row[1], row[2], [int(v) for v in row[3:]]) for row in rows]
        finally:
            conn.close()

    def tool_last_active(self) -> dict[str, str]:
        """Tool -> most recent active day over all retained history. Keeps a
        quiet lane visible instead of silently disappearing from day views."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT tool, MAX(day) FROM file_contributions"
                " WHERE assistant_turns > 0 OR cost_micro_usd > 0 GROUP BY tool"
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        finally:
            conn.close()

    def lane_health(self, source_id: str) -> list[int]:
        """Persisted per-source parse-health totals (FR-SRC-6)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(seen),0), COALESCE(SUM(parsed),0),"
                " COALESCE(SUM(skipped),0), COALESCE(SUM(malformed),0),"
                " COALESCE(SUM(unknown_field),0), COALESCE(SUM(unsupported),0)"
                " FROM file_health WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            return [int(v) for v in row]
        finally:
            conn.close()

    # -- suggestion / tip / alert state (FR-RPT-8, FR-FOC-3, FR-ALR-2) ---------

    def suggestion_set_state(self, suggestion_id: str, state: str, now: datetime) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO suggestion_state(suggestion_id, state, updated_at)"
                " VALUES(?, ?, ?) ON CONFLICT(suggestion_id) DO UPDATE SET"
                " state = excluded.state, updated_at = excluded.updated_at",
                (suggestion_id, state, _iso(now)),
            )
            conn.commit()
        finally:
            conn.close()

    def suggestion_states(self) -> dict[str, str]:
        conn = self._connect()
        try:
            return dict(
                conn.execute("SELECT suggestion_id, state FROM suggestion_state").fetchall()
            )
        finally:
            conn.close()

    def dismiss_tip(self, tip_id: str, now: datetime) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO tip_dismissals(tip_id, dismissed_at) VALUES(?, ?)",
                (tip_id, _iso(now)),
            )
            conn.commit()
        finally:
            conn.close()

    def dismissed_tips(self) -> set[str]:
        conn = self._connect()
        try:
            return {row[0] for row in conn.execute("SELECT tip_id FROM tip_dismissals").fetchall()}
        finally:
            conn.close()

    def alert_already_fired(self, category: str, day: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM fired_alerts WHERE category = ? AND day = ?",
                (category, day),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def record_alert(
        self, category: str, day: str, reason: str, delivery: str, now: datetime
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO fired_alerts(category, day, reason, fired_at,"
                " delivery) VALUES(?, ?, ?, ?, ?)",
                (category, day, reason, _iso(now), delivery),
            )
            conn.commit()
        finally:
            conn.close()

    # -- hygiene (FR-CFG-4) ------------------------------------------------------

    def hygiene(self, cutoff_day: str, sent_emit_cap: int = 1000) -> dict[str, int]:
        """Prune aged local state. Un-sent emits are never pruned."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            pruned: dict[str, int] = {}
            for table in (
                "file_contributions",
                "activity_marks",
                "engagement",
                "fired_alerts",
                "rate_limit_marks",
                "session_totals",
                "work_spans",
                "session_evidence",
            ):
                cursor = conn.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff_day,))
                pruned[table] = cursor.rowcount
            cursor = conn.execute(
                "DELETE FROM emit_queue WHERE status = 'sent' AND day < ?",
                (cutoff_day,),
            )
            pruned["emit_queue_sent_aged"] = cursor.rowcount
            cursor = conn.execute(
                "DELETE FROM emit_queue WHERE status = 'sent' AND id NOT IN ("
                " SELECT id FROM emit_queue WHERE status = 'sent'"
                " ORDER BY day DESC LIMIT ?)",
                (sent_emit_cap,),
            )
            pruned["emit_queue_sent_capped"] = cursor.rowcount
            conn.commit()
            return pruned
        finally:
            conn.close()
