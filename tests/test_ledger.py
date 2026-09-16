"""Coaching acknowledgment ledger (A3): the relationship feature. These tests
pin that an entry is recorded once per week (idempotent across ticks), that the
acknowledgment fires exactly at the delta threshold and only inside the [7, 21]
day window, that it never scorekeeps (no count, closed lexicon, no streak/row/
chain tokens), that the ledger is capped, and that composition is deterministic
and reads purely (no write on a view read).

The score derivation (gather_performance -> compose_coaching) is exercised
elsewhere; here we control the pillar scores via current_pillars so the ledger
decision logic — window, delta, idempotency, cap — is pinned exactly.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import practicegraph.report.coach as coach
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.coach import (
    ACK_COPY,
    ACK_MIN_DELTA,
    LEDGER_MAX_ENTRIES,
    LEDGER_META_KEY,
    CoachPillar,
    compose_acknowledgment,
    mark_acknowledgment,
    read_ledger,
    record_ledger_entry,
)
from practicegraph.store import Store

MON_W1 = date(2026, 6, 22)  # a Monday
MON_W2 = date(2026, 6, 29)  # the next Monday


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _pillar(pillar: str, score: int, tone: str = "watch") -> CoachPillar:
    return CoachPillar(
        pillar=pillar, label=pillar.title(), score=score, tone=tone,
        summary="s", cue="c", measures="m",
    )


def _patch_pillars(monkeypatch, mapping: dict[date, list[CoachPillar]]) -> None:
    """Make current_pillars return controlled, watch-first pillars per day."""
    def fake(store: Store, today: date, schedule: object = None) -> list[CoachPillar] | None:
        return mapping.get(today)
    monkeypatch.setattr(coach, "current_pillars", fake)


def test_records_one_entry_per_week_idempotently(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _patch_pillars(monkeypatch, {MON_W1: [_pillar("focus", 40)]})
    assert record_ledger_entry(store, MON_W1) is True
    # Repeated ticks in the same week add nothing.
    assert record_ledger_entry(store, MON_W1) is False
    assert record_ledger_entry(store, MON_W1 + timedelta(days=3)) is False
    entries = read_ledger(store)
    assert len(entries) == 1
    assert entries[0]["week_start"] == MON_W1.isoformat()
    assert entries[0]["pillar"] == "focus"
    assert entries[0]["baseline_score"] == 40


def test_full_loop_cue_week1_ack_week2(tmp_path, monkeypatch) -> None:
    """Cue recorded week 1 at 40; week 2 the pillar is 40 + delta -> acknowledged
    once, with both integers, from the closed copy."""
    store = _store(tmp_path)
    today_w2 = MON_W2  # exactly 7 days after MON_W1
    _patch_pillars(monkeypatch, {
        MON_W1: [_pillar("focus", 40)],
        today_w2: [_pillar("focus", 40 + ACK_MIN_DELTA)],
    })
    record_ledger_entry(store, MON_W1)
    ack = compose_acknowledgment(store, today_w2)
    assert ack is not None
    assert ack.pillar == "focus"
    assert ack.from_score == 40
    assert ack.to_score == 40 + ACK_MIN_DELTA
    assert str(40) in ack.text and str(40 + ACK_MIN_DELTA) in ack.text


def test_ack_fires_at_delta_and_never_below(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _patch_pillars(monkeypatch, {MON_W1: [_pillar("pattern", 50)]})
    record_ledger_entry(store, MON_W1)
    # delta-1 (here 7) -> silence.
    monkeypatch.setattr(coach, "current_pillars",
                        lambda s, t, p=None: [_pillar("pattern", 50 + ACK_MIN_DELTA - 1)])
    assert compose_acknowledgment(store, MON_W2) is None
    # exactly delta -> fires.
    monkeypatch.setattr(coach, "current_pillars",
                        lambda s, t, p=None: [_pillar("pattern", 50 + ACK_MIN_DELTA)])
    assert compose_acknowledgment(store, MON_W2) is not None


def test_window_bounds_six_days_and_twentytwo_days(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _patch_pillars(monkeypatch, {MON_W1: [_pillar("craft", 30)]})
    record_ledger_entry(store, MON_W1)
    improved = [_pillar("craft", 30 + ACK_MIN_DELTA + 5)]
    monkeypatch.setattr(coach, "current_pillars", lambda s, t, p=None: improved)
    # 6 days after the entry -> too soon, None.
    assert compose_acknowledgment(store, MON_W1 + timedelta(days=6)) is None
    # 7 and 21 days -> in window.
    assert compose_acknowledgment(store, MON_W1 + timedelta(days=7)) is not None
    assert compose_acknowledgment(store, MON_W1 + timedelta(days=21)) is not None
    # 22 days -> too old, None.
    assert compose_acknowledgment(store, MON_W1 + timedelta(days=22)) is None


def test_compose_is_pure_and_deterministic(tmp_path, monkeypatch) -> None:
    """A view read composes the ack without mutating the ledger — double-compose
    is identical and the stored bytes are unchanged."""
    store = _store(tmp_path)
    _patch_pillars(monkeypatch, {MON_W1: [_pillar("focus", 40)]})
    record_ledger_entry(store, MON_W1)
    monkeypatch.setattr(coach, "current_pillars",
                        lambda s, t, p=None: [_pillar("focus", 60)])
    before = store.meta_get(LEDGER_META_KEY)
    first = compose_acknowledgment(store, MON_W2)
    second = compose_acknowledgment(store, MON_W2)
    assert first == second and first is not None
    # No write on read: the ledger bytes are untouched, and nothing is acked.
    assert store.meta_get(LEDGER_META_KEY) == before
    assert all(not e.get("acked") for e in read_ledger(store))


def test_mark_retires_only_past_window_entries(tmp_path, monkeypatch) -> None:
    """The tick-only mark retires entries older than the window so a past cue
    can never fire again; an in-window entry is left alone."""
    store = _store(tmp_path)
    _patch_pillars(monkeypatch, {MON_W1: [_pillar("focus", 40)]})
    record_ledger_entry(store, MON_W1)
    # Still in window (14 days): mark does nothing.
    assert mark_acknowledgment(store, MON_W1 + timedelta(days=14)) is False
    assert all(not e.get("acked") for e in read_ledger(store))
    # Past the window (25 days): the entry is retired.
    assert mark_acknowledgment(store, MON_W1 + timedelta(days=25)) is True
    assert all(e.get("acked") for e in read_ledger(store))
    # A retired entry never fires, even if the pillar looks improved.
    monkeypatch.setattr(coach, "current_pillars",
                        lambda s, t, p=None: [_pillar("focus", 90)])
    assert compose_acknowledgment(store, MON_W1 + timedelta(days=20)) is None


def test_ledger_is_capped_oldest_dropped(tmp_path) -> None:
    store = _store(tmp_path)
    # Write more than the cap directly, then persist through the module helper.
    entries = [
        {"week_start": (MON_W1 + timedelta(days=7 * i)).isoformat(),
         "pillar": "focus", "cue_key": "focus-watch", "baseline_score": i}
        for i in range(LEDGER_MAX_ENTRIES + 5)
    ]
    coach._write_ledger(store, entries)
    kept = read_ledger(store)
    assert len(kept) == LEDGER_MAX_ENTRIES
    # The oldest were dropped; the newest survive.
    assert kept[0]["baseline_score"] == 5
    assert kept[-1]["baseline_score"] == LEDGER_MAX_ENTRIES + 4


def test_ack_copy_is_lexicon_clean_and_never_scorekeeps() -> None:
    """The acknowledgment copy stays inside the lexicon and carries no
    streak/row/chain/count vocabulary — acknowledgment, never scorekeeping."""
    forbidden = ("streak", "row", "chain", "in a row", "days straight")
    for text in ACK_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        low = text.lower()
        for token in forbidden:
            assert token not in low, (token, text)


def test_ledger_survives_corrupt_meta(tmp_path) -> None:
    store = _store(tmp_path)
    store.meta_set(LEDGER_META_KEY, "not json at all")
    assert read_ledger(store) == []
    store.meta_set(LEDGER_META_KEY, json.dumps({"not": "a list"}))
    assert read_ledger(store) == []
