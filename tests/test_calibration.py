"""The Calibration Mirror: the estimate before the clock.

The load-bearing test here is the **no-peek** one: a pending probe must carry
no duration and no session identity, or the instrument measures nothing. The
rest pins the bracket arithmetic, the offer cadence (one per day, once per
session, off with the coaching switch), the sample gate on the aggregate,
determinism, and the copy scans.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from pathlib import Path

from practicegraph.analysis.calibration import (
    CALIBRATION_BUCKET_IDS,
    CALIBRATION_COPY,
    CALIBRATION_LEDGER_KEY,
    CALIBRATION_MAX_AGE_DAYS,
    CALIBRATION_MAX_ENTRIES,
    CALIBRATION_MIN_FOR_SUMMARY,
    CALIBRATION_MIN_TURNS,
    bucket_for_minutes,
    compose_calibration,
    pending_session_key,
    read_ledger,
    record_estimate,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
TODAY = date(2026, 7, 3)
SECRET_KEY = "claude_code:11111111-2222-3333-4444-555555555555"


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _seed_session(
    store: Store,
    *,
    key: str = SECRET_KEY,
    day: str = "2026-07-03",
    first: str = "09:00:00",
    last: str = "12:30:00",
    turns: int = CALIBRATION_MIN_TURNS,
    path_hash: str = "h-cal",
) -> None:
    store.replace_file_data(
        source_id="claude_code",
        path_hash=path_hash,
        cursor=(1, 1, 8),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        session_totals=[
            (
                day, key, "claude_code",
                f"{day}T{first}+00:00", f"{day}T{last}+00:00",
                turns, 0, 0, 0, 0, 1_000_000, 0, 0, 0,
            )
        ],
    )


def test_pending_probe_reveals_nothing_it_should_not(tmp_path: Path) -> None:
    """THE test: a pending probe may not carry the actual length, the session
    identity, or anything else that would let the estimate be informed."""
    store = _store(tmp_path)
    _seed_session(store)  # a 3h30m session
    reading = compose_calibration(store, TODAY)
    assert reading.available is True
    assert reading.probe is not None
    encoded = json.dumps(dataclasses.asdict(reading.probe))
    # No session IDENTITY. (The word "session" is fine and expected — the
    # question asks about one; what must never appear is *which* one.)
    assert SECRET_KEY not in encoded
    assert "11111111" not in encoded
    assert "session_key" not in encoded
    assert "claude_code:" not in encoded
    # No duration in any form: not the minutes, not the bracket it falls in.
    assert "210" not in encoded
    assert "3h" not in encoded
    assert bucket_for_minutes(210) == "2_4h"
    # ...the bracket is only present as one of the five neutral CHOICES.
    labels = [label for _id, label in reading.probe.options]
    assert labels == ["under 30 minutes", "30 to 60 minutes", "1 to 2 hours",
                      "2 to 4 hours", "over 4 hours"]
    # And nothing has been answered yet, so there is no result to leak from.
    assert reading.last is None


def test_bucket_boundaries_are_exact() -> None:
    assert bucket_for_minutes(0) == "under_30m"
    assert bucket_for_minutes(29) == "under_30m"
    assert bucket_for_minutes(30) == "30_60m"
    assert bucket_for_minutes(59) == "30_60m"
    assert bucket_for_minutes(60) == "1_2h"
    assert bucket_for_minutes(119) == "1_2h"
    assert bucket_for_minutes(120) == "2_4h"
    assert bucket_for_minutes(239) == "2_4h"
    assert bucket_for_minutes(240) == "over_4h"
    assert bucket_for_minutes(10_000) == "over_4h"


def test_recording_resolves_the_session_and_renders_the_gap(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_session(store)  # 210 minutes -> "2 to 4 hours"
    assert record_estimate(store, TODAY, "1_2h") is True
    entries = read_ledger(store)
    assert len(entries) == 1
    assert entries[0]["actual_minutes"] == 210
    assert entries[0]["felt"] == "1_2h"
    assert entries[0]["session_key"] == SECRET_KEY

    reading = compose_calibration(store, TODAY)
    assert reading.last is not None
    assert reading.last.bucket_delta == 1  # ran one bracket longer than felt
    assert reading.last.line == CALIBRATION_COPY["under"].format(
        felt="1 to 2 hours", actual="3h 30m"
    )
    # Answering retires the probe for that session.
    assert reading.probe is None


def test_estimate_matching_and_overshooting_read_correctly(
    tmp_path: Path,
) -> None:
    same = _store(tmp_path / "same")
    _seed_session(same)
    assert record_estimate(same, TODAY, "2_4h") is True
    result = compose_calibration(same, TODAY).last
    assert result is not None and result.bucket_delta == 0
    assert "Same bracket" in result.line

    over = _store(tmp_path / "over")
    _seed_session(over)
    assert record_estimate(over, TODAY, "over_4h") is True
    result = compose_calibration(over, TODAY).last
    assert result is not None and result.bucket_delta == -1
    assert "shorter than it felt" in result.line


def test_offer_cadence_is_capped_and_respects_the_switch(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_session(store)
    # The coaching master switch turns the probe off entirely (PRINCIPLES §5).
    assert pending_session_key(store, TODAY, coaching_enabled=False) == ""
    assert compose_calibration(store, TODAY, coaching_enabled=False).probe is None
    assert pending_session_key(store, TODAY, coaching_enabled=True) == SECRET_KEY

    # One per local day: after answering, a second eligible session waits.
    assert record_estimate(store, TODAY, "1_2h") is True
    _seed_session(store, key="claude_code:another", path_hash="h-cal2")
    assert pending_session_key(store, TODAY, coaching_enabled=True) == ""
    # ...and is offered the next day.
    assert pending_session_key(store, date(2026, 7, 4), True) == "claude_code:another"


def test_thin_and_stale_sessions_are_never_probed(tmp_path: Path) -> None:
    thin = _store(tmp_path / "thin")
    _seed_session(thin, turns=CALIBRATION_MIN_TURNS - 1)
    assert pending_session_key(thin, TODAY, True) == ""
    assert compose_calibration(thin, TODAY) == compose_calibration(thin, TODAY)
    assert compose_calibration(thin, TODAY).available is False

    stale = _store(tmp_path / "stale")
    old = (TODAY - __import__("datetime").timedelta(
        days=CALIBRATION_MAX_AGE_DAYS + 1
    )).isoformat()
    _seed_session(stale, day=old)
    assert pending_session_key(stale, TODAY, True) == ""


def test_recording_refuses_unknown_buckets_and_empty_history(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_session(store)
    assert record_estimate(store, TODAY, "about an hour") is False
    assert record_estimate(store, TODAY, "") is False
    assert read_ledger(store) == []
    # Nothing pending -> nothing recorded, never an invented session.
    empty = _store(tmp_path / "empty")
    assert record_estimate(empty, TODAY, "1_2h") is False


def _seed_answered(store: Store, felts: list[str]) -> None:
    entries = [
        {
            "session_key": f"claude_code:s{i}",
            "day": f"2026-06-{10 + i:02d}",
            "felt": felt,
            "actual_minutes": 210,  # always "2 to 4 hours"
            "measure_id": "felt_session_length/v1",
        }
        for i, felt in enumerate(felts)
    ]
    store.meta_set(CALIBRATION_LEDGER_KEY, json.dumps(entries, sort_keys=True))


def test_summary_is_withheld_until_it_is_earned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_answered(store, ["2_4h"] * (CALIBRATION_MIN_FOR_SUMMARY - 1))
    assert compose_calibration(store, TODAY).summary == ""
    _seed_answered(store, ["2_4h"] * CALIBRATION_MIN_FOR_SUMMARY)
    summary = compose_calibration(store, TODAY).summary
    assert summary.startswith(
        CALIBRATION_COPY["summary"].format(
            total=str(CALIBRATION_MIN_FOR_SUMMARY),
            matched=str(CALIBRATION_MIN_FOR_SUMMARY),
        )
    )


def test_summary_reports_the_lean_both_ways(tmp_path: Path) -> None:
    under = _store(tmp_path / "under")
    # Estimated short, ran long -> the sessions ran longer than they felt.
    _seed_answered(under, ["under_30m", "30_60m", "1_2h", "2_4h"])
    assert CALIBRATION_COPY["summary-lean-under"] in compose_calibration(
        under, TODAY
    ).summary

    over = _store(tmp_path / "over")
    _seed_answered(over, ["over_4h", "over_4h", "over_4h", "2_4h"])
    assert CALIBRATION_COPY["summary-lean-over"] in compose_calibration(
        over, TODAY
    ).summary


def test_ledger_is_capped_and_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_answered(store, ["2_4h"] * (CALIBRATION_MAX_ENTRIES + 5))
    _seed_session(store)
    assert record_estimate(store, TODAY, "1_2h") is True
    assert len(read_ledger(store)) == CALIBRATION_MAX_ENTRIES
    # Malformed content never raises — it reads as no history.
    store.meta_set(CALIBRATION_LEDGER_KEY, "{not json")
    assert read_ledger(store) == []
    store.meta_set(CALIBRATION_LEDGER_KEY, json.dumps([{"felt": "nonsense"}]))
    assert read_ledger(store) == []


def test_double_compose_is_identical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_session(store)
    record_estimate(store, TODAY, "1_2h")
    assert compose_calibration(store, TODAY) == compose_calibration(store, TODAY)


def test_no_result_ever_carries_the_session_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_session(store)
    record_estimate(store, TODAY, "1_2h")
    reading = compose_calibration(store, TODAY)
    encoded = json.dumps(dataclasses.asdict(reading))
    assert SECRET_KEY not in encoded
    assert "session_key" not in encoded


def test_copy_is_clean_and_never_grades_the_person(tmp_path: Path) -> None:
    for text in CALIBRATION_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
        low = text.lower()
        # A gap is a fact about estimation, never a verdict about the person.
        for token in ("wrong", "bad", "poor", "should have", "miscalibrat",
                      "accurate", "streak", "in a row", "score"):
            assert token not in low, text
    store = _store(tmp_path)
    _seed_session(store)
    record_estimate(store, TODAY, "1_2h")
    reading = compose_calibration(store, TODAY)
    assert reading.last is not None
    for line in (reading.last.line, reading.summary):
        assert lexicon_violations(line) == []
        assert leak_findings(line) == []


def test_measure_buckets_are_pinned() -> None:
    """The measure is versioned; its brackets are part of that contract and
    cannot drift silently, or old ledger entries stop meaning what they meant."""
    assert CALIBRATION_BUCKET_IDS == (
        "under_30m", "30_60m", "1_2h", "2_4h", "over_4h",
    )
