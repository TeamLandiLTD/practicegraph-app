"""Quota runway (A1): the daily-utility glance. These tests pin that the
snapshot is exact arithmetic over stored readings, deterministic (INV-6),
honestly absent when there are no readings (withheld, not zero), that the
register bands sit on the named thresholds, and that the only strings the
module mints stay inside the copy lexicon."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from practicegraph.analysis.runway import (
    RUNWAY_COMFORT_MAX_PCT,
    RUNWAY_LABEL,
    RUNWAY_WATCH_MAX_PCT,
    RunwaySnapshot,
    compose_runway,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _seed(store: Store, marks: list[tuple[str, str, int, int]]) -> None:
    """Seed rate-limit readings through the real per-file replace path
    (path_hash, day, ts_utc, used_pct_tenths, window_minutes)."""
    store.replace_file_data(
        source_id="codex_cli",
        path_hash="h-gauge",
        cursor=(1, 1, 7),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        rate_limit_marks=marks,
    )


def test_snapshot_reads_latest_and_peak_per_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            # 5h window: peaks at 87.5% mid-day, latest reading is 42.0%.
            ("2026-07-01", "2026-07-01T09:00:00+00:00", 620, 300),
            ("2026-07-01", "2026-07-01T13:00:00+00:00", 875, 300),
            ("2026-07-01", "2026-07-01T18:00:00+00:00", 420, 300),
            # weekly window: single reading at 33.0%.
            ("2026-07-01", "2026-07-01T13:00:00+00:00", 330, 10080),
        ],
    )
    snap = compose_runway(store, "2026-07-01")
    assert snap.available is True
    by_bucket = {b.bucket: b for b in snap.buckets}
    assert set(by_bucket) == {"5h", "week"}
    # 5h: latest 420 tenths -> 42%, peak 875 tenths -> 88% (half-up).
    assert by_bucket["5h"].latest_pct == 42
    assert by_bucket["5h"].peak_pct == 88
    assert by_bucket["5h"].register == "comfort"
    assert by_bucket["5h"].label == RUNWAY_LABEL["5h"]
    # week: single reading 330 tenths -> 33%.
    assert by_bucket["week"].latest_pct == 33
    assert by_bucket["week"].peak_pct == 33
    # Buckets are ordered 5h before week.
    assert [b.bucket for b in snap.buckets] == ["5h", "week"]


def test_accounts_pools_and_secondary_windows_do_not_merge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.replace_file_data(
        source_id="codex_cli", path_hash="gauge", cursor=(1, 1, 13),
        contributions=[], marks=[], health=None, turn_keys=(), now=NOW,
        rate_limit_marks=[
            ("2026-07-01", "2026-07-01T09:00:00+00:00", 400, 300,
             "primary", "2026-07-01T14:00:00+00:00", "account-a", "pool-a"),
            ("2026-07-01", "2026-07-01T09:01:00+00:00", 900, 300,
             "primary", "2026-07-01T15:00:00+00:00", "account-b", "pool-a"),
            ("2026-07-01", "2026-07-01T09:02:00+00:00", 100, 300,
             "primary", "", "account-a", "pool-b"),
            ("2026-07-01", "2026-07-01T09:02:00+00:00", 500, 10080,
             "secondary", "", "account-a", "pool-a"),
        ],
    )
    buckets = compose_runway(store, "2026-07-01").buckets
    assert len(buckets) == 4
    a = next(b for b in buckets if b.account_hash == "account-a"
             and b.pool_hash == "pool-a" and b.window_kind == "primary")
    assert a.latest_pct == 40 and a.resets_at == "2026-07-01T14:00:00+00:00"


def test_unknown_window_is_bucketed_as_other(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, [("2026-07-01", "2026-07-01T09:00:00+00:00", 500, 4321)])
    snap = compose_runway(store, "2026-07-01")
    assert [b.bucket for b in snap.buckets] == ["other"]
    assert snap.buckets[0].label == RUNWAY_LABEL["other"]


def test_no_readings_is_unavailable_never_zero(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snap = compose_runway(store, "2026-07-01")
    assert snap == RunwaySnapshot(available=False, buckets=())
    # A day with readings elsewhere is still unavailable on an empty day.
    _seed(store, [("2026-07-02", "2026-07-02T09:00:00+00:00", 500, 300)])
    assert compose_runway(store, "2026-07-01").available is False


def test_double_compose_is_identical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            ("2026-07-01", "2026-07-01T09:00:00+00:00", 620, 300),
            ("2026-07-01", "2026-07-01T18:00:00+00:00", 815, 10080),
        ],
    )
    first = compose_runway(store, "2026-07-01")
    second = compose_runway(store, "2026-07-01")
    assert first == second


def test_register_bands_sit_on_the_named_thresholds(tmp_path: Path) -> None:
    """comfort at/below 60, watch through 80, tight above — checked at the
    exact boundary tenths so an off-by-one in the band shows."""
    store = _store(tmp_path)
    # 60% exactly -> comfort; 61% -> watch; 80% -> watch; 81% -> tight.
    cases = {
        RUNWAY_COMFORT_MAX_PCT * 10: "comfort",       # 600 tenths = 60%
        RUNWAY_COMFORT_MAX_PCT * 10 + 10: "watch",    # 610 tenths = 61%
        RUNWAY_WATCH_MAX_PCT * 10: "watch",           # 800 tenths = 80%
        RUNWAY_WATCH_MAX_PCT * 10 + 10: "tight",      # 810 tenths = 81%
    }
    for tenths, expected in cases.items():
        _seed(store, [("2026-07-01", "2026-07-01T09:00:00+00:00", tenths, 300)])
        snap = compose_runway(store, "2026-07-01")
        assert snap.buckets[0].register == expected, (tenths, expected)


def test_runway_labels_are_lexicon_clean() -> None:
    for text in RUNWAY_LABEL.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_provider_and_exact_window_keep_separate_latest_readings(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for source, window, used, hour in [
        ("codex_cli", 300, 700, 10), ("claude_code", 300, 200, 11),
        ("codex_cli", 4321, 100, 12), ("codex_cli", 4322, 900, 13),
    ]:
        store.replace_file_data(
            source_id=source, path_hash=f"gauge-{source}-{window}",
            cursor=(1, 1, 7), contributions=[], marks=[], health=None,
            turn_keys=(), now=NOW,
            rate_limit_marks=[("2026-07-01", f"2026-07-01T{hour}:00:00+00:00", used, window)],
        )
    buckets = {
        (b.source_id, b.window_minutes): b for b in compose_runway(store, "2026-07-01").buckets
    }
    assert len(buckets) == 4
    assert buckets["codex_cli", 300].latest_pct == 70
    assert buckets["claude_code", 300].latest_pct == 20
    assert buckets["codex_cli", 4321].latest_pct == 10
    assert buckets["codex_cli", 4322].latest_pct == 90
    assert buckets["claude_code", 300].observed_at == "2026-07-01T11:00:00+00:00"


def test_orphaned_gauge_keeps_unknown_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, [("2026-07-01", "2026-07-01T09:00:00+00:00", 500, 300)])
    with store._connect() as conn:
        conn.execute("DELETE FROM file_cursors")
    bucket = compose_runway(store, "2026-07-01").buckets[0]
    assert bucket.source_id == "unknown"
    assert bucket.latest_pct == 50
