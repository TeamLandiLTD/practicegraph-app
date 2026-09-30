"""Legacy daily-feel history stays local and is never interpreted as output."""

from __future__ import annotations

from pathlib import Path

from practicegraph.analysis.perception import (
    LegacyDailyFeel,
    read_legacy_checkin,
    record_checkin,
)
from practicegraph.store import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def test_legacy_daily_feel_is_recorded_but_never_interpreted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert record_checkin(store, "2026-07-12", 4)
    assert read_legacy_checkin(store, "2026-07-12") == LegacyDailyFeel(
        measure_id="legacy_daily_feel/v1", rating=4
    )


def test_legacy_daily_feel_rejects_out_of_range(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert not record_checkin(store, "2026-07-12", 0)
    assert not record_checkin(store, "2026-07-12", 6)


def test_no_activity_productivity_comparison_is_exported() -> None:
    source = Path("src/practicegraph/analysis/perception.py").read_text("utf-8")
    for forbidden in ("objective", "productivity", "turns_by_day", "gap_points"):
        assert forbidden not in source.lower()
