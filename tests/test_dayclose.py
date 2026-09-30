"""Close the day (A2): the shutdown-flag module. These tests pin that the
closed state round-trips through the meta table, that a malformed day is
refused rather than written, that closing is idempotent, and that the panel
copy stays calm and inside the lexicon (no exclamation marks)."""

from __future__ import annotations

from pathlib import Path

from practicegraph.analysis.dayclose import (
    DAYCLOSE_COPY,
    is_day_closed,
    record_day_close,
    valid_day,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def test_close_round_trips_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert is_day_closed(store, "2026-07-10") is False
    assert record_day_close(store, "2026-07-10") is True
    assert is_day_closed(store, "2026-07-10") is True
    # Closing again is a no-op that still reports success (last write wins).
    assert record_day_close(store, "2026-07-10") is True
    assert is_day_closed(store, "2026-07-10") is True
    # A different day is unaffected.
    assert is_day_closed(store, "2026-07-11") is False


def test_valid_day_accepts_real_dates_only() -> None:
    assert valid_day("2026-07-10") is True
    assert valid_day("2026-02-29") is False  # 2026 is not a leap year
    assert valid_day("2026-13-01") is False  # no month 13
    assert valid_day("2026-07-40") is False  # no day 40
    assert valid_day("2026-7-10") is False   # not zero-padded
    assert valid_day("07-10-2026") is False  # wrong order
    assert valid_day("") is False
    assert valid_day("today") is False


def test_malformed_day_is_refused_not_written(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert record_day_close(store, "2026-13-40") is False
    # Nothing was written under any variant of the bad key.
    assert is_day_closed(store, "2026-13-40") is False


def test_dayclose_copy_is_calm_and_lexicon_clean() -> None:
    for text in DAYCLOSE_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text  # a shutdown is quiet by design
