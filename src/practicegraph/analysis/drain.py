"""The felt-drain probe: how drained the day felt, asked before anything shows.

The strain reading earns its place the way
the reliance reading did — by a kill test: do the verification-load
components predict how drained the person SAYS they feel? That needs a
self-report, gathered on the calibration mirror's terms:

**The ordering is the feature.** The answer is asked before the
verification components are shown for the day; an answer given after
seeing "1,235 failed runs" is not a report of how the day felt. The
viewmodel withholds the components while today's probe is pending.

**Its own versioned measure.** `felt_drain/v1`, four ordered brackets,
direction ascending (more drained). Never the legacy 1-5 daily note — that
is `legacy_daily_feel/v1`, excluded by rule from every new construct.

**Autonomy.** Opt-out via the coaching switch, at most once a local day,
only on a day with activity, and skipping costs nothing: a skipped day is
recorded as skipped so it is never asked twice.

The ledger is local meta JSON (the calibration precedent), sized for the
30-day kill test. No surface ever renders an individual answer as a
judgment; the kill test is an offline analysis that joins the ledger with
the components and is read by a person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from practicegraph.store import Store

DRAIN_MEASURE_ID = "felt_drain/v1"
DRAIN_SCALE_DIRECTION = "ascending"  # later brackets = more drained
DRAIN_LEDGER_KEY = "drain_probes:v1"
DRAIN_MAX_ENTRIES = 120

# Closed, ordered brackets. The wire carries ids and labels only.
DRAIN_BRACKETS: tuple[tuple[str, str], ...] = (
    ("fresh", "Fresh"),
    ("fine", "Fine"),
    ("worn", "Worn"),
    ("drained", "Drained"),
)
DRAIN_SKIP = "skip"

DRAIN_COPY: dict[str, str] = {
    "question": "Before the numbers: how drained do you feel right now?",
    "intro": (
        "One word a day, asked before the numbers show - so the answer is "
        "yours, not the page's. After a month the two are compared."
    ),
    "skip": "Not today",
    "answered": "Noted for today — the components are below.",
}


@dataclass(frozen=True)
class DrainSurface:
    available: bool          # coaching on
    pending: bool            # ask now, withhold the components
    question: str
    intro: str
    options: tuple[tuple[str, str], ...]
    skip_label: str
    answered_today: str | None   # bracket id, "skip", or None
    answered_count: int          # real answers in the ledger (skips excluded)


def read_ledger(store: Store) -> list[dict[str, object]]:
    raw = store.meta_get(DRAIN_LEDGER_KEY)
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        return []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _write_ledger(store: Store, entries: list[dict[str, object]]) -> None:
    store.meta_set(DRAIN_LEDGER_KEY, json.dumps(entries[-DRAIN_MAX_ENTRIES:]))


def _entry_for(entries: list[dict[str, object]], day: date) -> dict[str, object] | None:
    key = day.isoformat()
    for entry in reversed(entries):
        if entry.get("day") == key:
            return entry
    return None


def probe_pending(store: Store, today: date, coaching_enabled: bool) -> bool:
    """Ask today iff coaching is on, the day has activity, and no answer or
    skip is on the ledger for it."""
    if not coaching_enabled:
        return False
    if store.session_count_for_day(today.isoformat()) == 0:
        return False
    return _entry_for(read_ledger(store), today) is None


def record_drain(store: Store, today: date, felt: str, now: datetime) -> bool:
    """Record today's bracket (or a skip). Unknown brackets are refused;
    a second answer for the same day is refused, never overwritten."""
    valid = {bracket_id for bracket_id, _ in DRAIN_BRACKETS} | {DRAIN_SKIP}
    if felt not in valid:
        return False
    entries = read_ledger(store)
    if _entry_for(entries, today) is not None:
        return False
    entries.append({
        "day": today.isoformat(),
        "measure": DRAIN_MEASURE_ID,
        "felt": felt,
        "recorded_at": now.isoformat(),
    })
    _write_ledger(store, entries)
    return True


def compose_drain(store: Store, today: date, coaching_enabled: bool) -> DrainSurface:
    entries = read_ledger(store)
    todays = _entry_for(entries, today)
    answered = sum(1 for e in entries if e.get("felt") != DRAIN_SKIP)
    return DrainSurface(
        available=coaching_enabled,
        pending=probe_pending(store, today, coaching_enabled),
        question=DRAIN_COPY["question"],
        intro=DRAIN_COPY["intro"],
        options=DRAIN_BRACKETS,
        skip_label=DRAIN_COPY["skip"],
        answered_today=str(todays["felt"]) if todays else None,
        answered_count=answered,
    )
