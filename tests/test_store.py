"""Local state store (FR-CFG-3) and emit queue lifecycle (FR-EMT-2)."""

from __future__ import annotations

import contextlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from practicegraph.store import STORE_SCHEMA_VERSION, ContributionRow, Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def test_migrations_are_idempotent_and_monotonic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.schema_version() == STORE_SCHEMA_VERSION
    store.migrate()  # re-run: no-op
    assert store.schema_version() == STORE_SCHEMA_VERSION
    # A rollback re-run must not downgrade: simulate a future version.
    conn = sqlite3.connect(store.path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    store.migrate()
    assert store.schema_version() == 99


def _build_at_version(tmp_path: Path, upto: int) -> Store:
    """A database migrated only through ``upto`` (simulating an old install)."""
    from practicegraph.store import _MIGRATIONS

    store = Store(tmp_path / "state.db")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(store.path)
    for version, script in _MIGRATIONS:
        if version <= upto:
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")
    conn.execute(
        "INSERT INTO file_contributions(path_hash, source_id, day, tool, model)"
        " VALUES('h', 's', '2026-07-01', 'claude_code', 'm')"
    )
    conn.execute(
        "INSERT INTO activity_marks(path_hash, day, session_key, ts_utc, kind)"
        " VALUES('h', '2026-07-01', 'k', '2026-07-01T09:00:00+00:00', 'user_turn')"
    )
    conn.commit()
    conn.close()
    return store


def test_migration_v3_upgrades_through_execution_and_compaction(tmp_path: Path) -> None:
    """P1+P2+P3 upgrade path: a v3 database gains the execution counters (v4),
    the compaction counters (v5), and the environment-context columns (v6),
    all defaulting additively (FR-CFG-3)."""
    from practicegraph.store import MARK_FAILURES, MARK_INTERACTIVE

    store = _build_at_version(tmp_path, upto=3)
    assert store.schema_version() == 3

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    _tool, _model, values = store.day_usage_rows("2026-07-01")[0]
    assert len(values) == len(ContributionRow._fields)
    # rework/run/failed/slow/compactions, the W3.1 attempt counters, then
    # the W4.1 artifact counters — every appended column backfills to zero.
    assert values[13:] == [0] * (len(ContributionRow._fields) - 13)
    mark = store.marks_for_day("2026-07-01")[0]
    assert len(mark) == 13
    assert mark[MARK_FAILURES : MARK_INTERACTIVE] == (0, 0)  # failures, compactions
    # Legacy rows read interactive, hash-less, and not-short (v8 backfill).
    assert mark[MARK_INTERACTIVE:] == (1, "", "", 0, 0)


def test_migration_v4_to_v5_adds_compaction_columns(tmp_path: Path) -> None:
    """P2 upgrade path: a v4 database gains the two compaction columns,
    appended after the execution counters (the storage contract),
    backfilled to 0."""
    from practicegraph.store import MARK_COMPACTIONS, MARK_FAILURES

    store = _build_at_version(tmp_path, upto=4)
    conn = sqlite3.connect(store.path)
    conn.execute(
        "UPDATE file_contributions SET commands_failed = 2 WHERE path_hash = 'h'"
    )
    conn.execute(
        "UPDATE activity_marks SET command_failures = 1 WHERE path_hash = 'h'"
    )
    conn.commit()
    conn.close()
    assert store.schema_version() == 4

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    _tool, _model, values = store.day_usage_rows("2026-07-01")[0]
    assert len(values) == len(ContributionRow._fields)
    assert values[15] == 2  # commands_failed kept its position
    assert values[17] == 0  # compactions appended after it, backfilled to 0
    mark = store.marks_for_day("2026-07-01")[0]
    assert mark[MARK_FAILURES] == 1  # command_failures kept its position
    assert mark[MARK_COMPACTIONS] == 0  # compactions appended, default 0


def test_migration_v5_to_v6_adds_environment_columns(tmp_path: Path) -> None:
    """P3 upgrade path: a v5 database gains ONLY interactive + the two
    identity-hash columns, appended LAST in that order (the storage
    contract); legacy rows default to interactive with absent hashes."""
    from practicegraph.store import MARK_COMPACTIONS, MARK_INTERACTIVE

    store = _build_at_version(tmp_path, upto=5)
    conn = sqlite3.connect(store.path)
    conn.execute("UPDATE activity_marks SET compactions = 4 WHERE path_hash = 'h'")
    conn.commit()
    conn.close()
    assert store.schema_version() == 5

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    mark = store.marks_for_day("2026-07-01")[0]
    assert len(mark) == 13
    assert mark[MARK_COMPACTIONS] == 4  # compactions kept its position
    # interactive, cwd_hash, branch_hash — appended in exactly that order
    # (short_reply lands after them, backfilled by v8).
    assert mark[MARK_INTERACTIVE:] == (1, "", "", 0, 0)


def test_migration_v6_to_v7_creates_rate_limit_marks(tmp_path: Path) -> None:
    """P4 upgrade path: a v6 database gains the rate_limit_marks gauge table
    (a new table, no backfill — readings only accrue from new scans). All
    pre-existing rows are untouched."""
    from practicegraph.store import MARK_INTERACTIVE

    store = _build_at_version(tmp_path, upto=6)
    assert store.schema_version() == 6

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    assert store.latest_rate_limit("2026-07-01") is None
    mark = store.marks_for_day("2026-07-01")[0]
    assert len(mark) == 13  # marks gained the appended v8 and v9 columns
    assert mark[MARK_INTERACTIVE:] == (1, "", "", 0, 0)


def test_migration_v7_to_v8_appends_short_reply(tmp_path: Path) -> None:
    """Waved-approvals upgrade path: a v7 database gains ONLY the appended
    short_reply mark column, backfilled to 0 (not short) — every earlier
    column keeps its exact position (the storage contract)."""
    from practicegraph.store import (
        MARK_BRANCH_HASH,
        MARK_INTERACTIVE,
        MARK_SHORT_REPLY,
    )

    store = _build_at_version(tmp_path, upto=7)
    conn = sqlite3.connect(store.path)
    conn.execute("UPDATE activity_marks SET interactive = 0 WHERE path_hash = 'h'")
    conn.commit()
    conn.close()
    assert store.schema_version() == 7

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    mark = store.marks_for_day("2026-07-01")[0]
    assert len(mark) == 13
    assert mark[MARK_INTERACTIVE] == 0  # pre-v8 values kept their positions
    assert mark[MARK_BRANCH_HASH] == ""
    assert mark[MARK_SHORT_REPLY] == 0  # appended LAST, backfilled to 0
    day_row = store.marks_between("2026-07-01", "2026-07-01")[0]
    assert day_row[1:] == mark  # both SELECT paths carry the new column


def test_migration_v8_to_v9_appends_human_initiated(tmp_path: Path) -> None:
    from practicegraph.store import MARK_HUMAN_INITIATED, MARK_SHORT_REPLY

    store = _build_at_version(tmp_path, upto=8)
    conn = sqlite3.connect(store.path)
    conn.execute("UPDATE activity_marks SET short_reply = 1 WHERE path_hash = 'h'")
    conn.commit()
    conn.close()

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    mark = store.marks_for_day("2026-07-01")[0]
    assert len(mark) == 13
    assert mark[MARK_SHORT_REPLY] == 1
    assert mark[MARK_HUMAN_INITIATED] == 0
    assert store.marks_between("2026-07-01", "2026-07-01")[0][1:] == mark


def test_migration_v9_to_v10_adds_session_totals(tmp_path: Path) -> None:
    """W2.0 upgrade path: a v9 database gains the session_totals table with no
    loss of existing history — the table starts empty and the next scan
    backfills it (history.SESSION_GRAIN_VERSION forces that one rescan)."""
    store = _build_at_version(tmp_path, upto=9)
    assert store.schema_version() == 9
    # Existing history survives the upgrade untouched. Captured with raw SQL:
    # the store's readers speak the CURRENT schema, and the app never reads
    # before migrate() — pre-upgrade reads are not part of the contract.
    conn = sqlite3.connect(store.path)
    before_raw = conn.execute(
        "SELECT day, tool, model, assistant_turns FROM file_contributions"
    ).fetchall()
    conn.close()

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    after = store.day_usage_rows("2026-07-01")
    assert [("2026-07-01", r[0], r[1], r[2][0]) for r in
            [(t, m, v) for t, m, v in after]] == [
        (day, tool, model, turns) for day, tool, model, turns in before_raw
    ]
    # The new table exists and is honestly empty until a rescan fills it.
    assert store.sessions_between("0001-01-01", "9999-12-31") == []

    store.replace_file_data(
        source_id="claude_code",
        path_hash="h-sess",
        cursor=(1, 1, 8),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        session_totals=[
            (
                "2026-07-01", "claude_code:s1", "claude_code",
                "2026-07-01T09:00:00+00:00", "2026-07-01T09:30:00+00:00",
                12, 1_000, 9_000, 500, 2_000, 3_000_000, 0, 1, 40,
            )
        ],
    )
    rows = store.sessions_between("2026-07-01", "2026-07-01")
    assert len(rows) == 1 and rows[0].assistant_turns == 12
    assert rows[0].cost_micro_usd == 3_000_000


def test_session_totals_follow_the_per_file_replace_lifecycle(
    tmp_path: Path,
) -> None:
    """Re-parsing a file replaces only its own session rows — the same atomic
    contract as marks, so a rescan can never double-count a session."""
    store = _store(tmp_path)

    def _write(cost: int, turns: int) -> None:
        store.replace_file_data(
            source_id="claude_code",
            path_hash="h-sess",
            cursor=(1, 1, 8),
            contributions=[],
            marks=[],
            health=None,
            turn_keys=(),
            now=NOW,
            session_totals=[
                (
                    "2026-07-01", "claude_code:s1", "claude_code",
                    "2026-07-01T09:00:00+00:00", "2026-07-01T09:30:00+00:00",
                    turns, 0, 0, 0, 0, cost, 0, 0, 0,
                )
            ],
        )

    _write(1_000_000, 5)
    _write(2_000_000, 9)  # re-parse of the SAME file
    rows = store.sessions_between("2026-07-01", "2026-07-01")
    assert len(rows) == 1
    assert rows[0].cost_micro_usd == 2_000_000  # replaced, never summed
    assert rows[0].assistant_turns == 9
    # Pruning the file's state drops its sessions with it.
    store.prune_missing_files("claude_code", set())
    assert store.sessions_between("2026-07-01", "2026-07-01") == []


def test_latest_rate_limit_reads_the_days_newest_gauge(tmp_path: Path) -> None:
    """P4: gauge rows follow the per-file atomic replace lifecycle exactly
    like activity marks, and the day helper returns the LATEST reading by
    ts_utc — the informational approaching-quota input (local-only)."""
    store = _store(tmp_path)
    store.replace_file_data(
        source_id="codex_cli",
        path_hash="h-gauge",
        cursor=(1, 1, 7),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        rate_limit_marks=[
            ("2026-07-01", "2026-07-01T09:00:00+00:00", 325, 300),
            ("2026-07-01", "2026-07-01T18:00:00+00:00", 875, 300),
            ("2026-07-02", "2026-07-02T08:00:00+00:00", 120, 300),
        ],
    )
    assert store.latest_rate_limit("2026-07-01") == (875, 300)
    assert store.latest_rate_limit("2026-07-02") == (120, 300)
    assert store.latest_rate_limit("2026-07-03") is None
    # A re-parse REPLACES the file's readings — never double-counts (M1 rule).
    store.replace_file_data(
        source_id="codex_cli",
        path_hash="h-gauge",
        cursor=(2, 2, 7),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        rate_limit_marks=[("2026-07-01", "2026-07-01T09:00:00+00:00", 325, 300)],
    )
    assert store.latest_rate_limit("2026-07-01") == (325, 300)


def test_marks_round_trip_environment_columns(tmp_path: Path) -> None:
    """P3 columns survive the write path: hashes and the interactive flag
    come back exactly, via replace_file_data -> both mark SELECTs."""
    from practicegraph.store import MARK_BRANCH_HASH, MARK_CWD_HASH, MARK_INTERACTIVE

    store = _store(tmp_path)
    store.replace_file_data(
        source_id="claude_code_cli",
        path_hash="h-env",
        cursor=(1, 1, 6),
        contributions=[],
        marks=[
            ("2026-07-01", "k", "2026-07-01T09:00:00+00:00", "user_turn",
             0, 0, 0, 0, 0, 0, "a" * 16, "b" * 16, 1, 1),
        ],
        health=None,
        turn_keys=(),
        now=NOW,
    )
    mark = store.marks_for_day("2026-07-01")[0]
    assert mark[MARK_INTERACTIVE] == 0
    assert mark[MARK_CWD_HASH] == "a" * 16
    assert mark[MARK_BRANCH_HASH] == "b" * 16
    from practicegraph.store import MARK_SHORT_REPLY

    assert mark[MARK_SHORT_REPLY] == 1  # v8 column round-trips too
    from practicegraph.store import MARK_HUMAN_INITIATED

    assert mark[MARK_HUMAN_INITIATED] == 1
    day_row = store.marks_between("2026-07-01", "2026-07-01")[0]
    assert day_row[0] == "2026-07-01"
    assert day_row[1:] == mark  # day-prefixed rows shift every index by +1


def test_schema_version_read_does_not_create(tmp_path: Path) -> None:
    store = Store(tmp_path / "absent.db")
    with contextlib.suppress(sqlite3.OperationalError):
        store.schema_version()
    assert not store.exists()


def test_queue_day_claims_exactly_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.queue_day("2026-07-02", "emit-a", "{}", NOW)
    assert not store.queue_day("2026-07-02", "emit-b", "{}", NOW)
    assert store.day_queued("2026-07-02")
    assert not store.day_queued("2026-07-01")


def test_queue_lifecycle_sent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.queue_day("2026-07-02", "emit-a", '{"x":1}', NOW)
    due = store.due_emits(NOW)
    assert len(due) == 1
    store.mark_sent(due[0].row_id, NOW)
    assert store.due_emits(NOW) == []
    assert store.queue_counts()["sent"] == 1
    assert store.next_unsent_payload() is None


def test_queue_lifecycle_retry_backoff(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.queue_day("2026-07-02", "emit-a", "{}", NOW)
    row = store.due_emits(NOW)[0]
    store.mark_retry(row.row_id, "server_unavailable", NOW + timedelta(seconds=60), 1)
    assert store.due_emits(NOW) == []  # not due yet
    later = NOW + timedelta(seconds=61)
    due_later = store.due_emits(later)
    assert len(due_later) == 1
    assert due_later[0].attempts == 1
    assert store.queue_error_counts() == {"server_unavailable": 1}


def test_queue_dead_letter_is_terminal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.queue_day("2026-07-02", "emit-a", "{}", NOW)
    row = store.due_emits(NOW)[0]
    store.mark_dead_letter(row.row_id, "schema_version_not_supported")
    assert store.due_emits(NOW + timedelta(days=365)) == []
    assert store.queue_counts()["dead_letter"] == 1
    assert store.queue_error_counts() == {"schema_version_not_supported": 1}
    # The day stays claimed: a dead-lettered day is never re-emitted.
    assert store.day_queued("2026-07-02")


def test_engagement_counters_accumulate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.engagement_add("2026-07-02", "report_generated")
    store.engagement_add("2026-07-02", "report_generated", 2)
    store.engagement_add("2026-07-03", "report_generated")
    assert store.engagement_for_day("2026-07-02") == {"report_generated": 3}


def test_meta_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.meta_get("missing") is None
    store.meta_set("k", "v1")
    store.meta_set("k", "v2")
    assert store.meta_get("k") == "v2"


def test_meta_claim_interval_allows_first_and_expired_claims(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    assert store.meta_claim_interval("news-check", NOW, 900) is True
    assert store.meta_claim_interval(
        "news-check", NOW + timedelta(seconds=899), 900
    ) is False
    assert store.meta_claim_interval(
        "news-check", NOW + timedelta(seconds=900), 900
    ) is True


def test_meta_claim_interval_has_one_winner_across_connections(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: store.meta_claim_interval("news-check", NOW, 900),
                range(8),
            )
        )

    assert results.count(True) == 1
    assert results.count(False) == 7


def test_migration_v10_to_v11_appends_attempt_counters(tmp_path: Path) -> None:
    """W3.1 upgrade path: both tables gain the two attempt counters, appended
    last and backfilled to zero — and the schema stays INTEGER-only for them
    (the spike's no-TEXT rule, checked structurally)."""
    store = _build_at_version(tmp_path, upto=10)
    assert store.schema_version() == 10

    store.migrate()
    assert store.schema_version() == STORE_SCHEMA_VERSION
    _tool, _model, values = store.day_usage_rows("2026-07-01")[0]
    # attempt counters, then the W4.1 artifact counters, all zero-backfilled
    assert values[18:] == [0] * (len(ContributionRow._fields) - 18)

    conn = sqlite3.connect(store.path)
    try:
        for table in ("file_contributions", "session_totals"):
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            by_name = {row[1]: row[2].upper() for row in info}
            assert by_name["git_commit_attempts"] == "INTEGER", table
            assert by_name["test_run_attempts"] == "INTEGER", table
        # The spike's boundary: these counters never ride with a new TEXT
        # column — the only TEXT on session_totals remains the W2.0 set.
        session_text = {
            row[1]
            for row in conn.execute("PRAGMA table_info(session_totals)")
            if row[2].upper() == "TEXT"
        }
        assert session_text == {
            "path_hash", "day", "session_key", "tool", "first_ts", "last_ts"
        }
    finally:
        conn.close()
