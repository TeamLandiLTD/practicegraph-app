"""Close the day (local-only): an optional 30-second shutdown ritual.

A Cal-Newport-style shutdown for the person who wants one — today's numbers,
the existing 1-5 daily-feel note as the closing act, and an explicit "day
closed" state. Today only: no history strip, no chain, no count of days closed
ever (that would be the streak dark-pattern PRINCIPLES.md forbids). This module
owns only the closed-state flag and its calm copy; the numbers and the checkin
are reused from the surfaces that already compute them.

Local-only forever (NFR-PRV-6, same wire field-name scan): the flag lives in the
meta table and never feeds an emit. Deterministic (INV-6): closed state is a
function of (store, day) with the day passed in.
"""

from __future__ import annotations

import re

from practicegraph.store import Store

# The wire field-name scan pins these out of any payload (test_wire).
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = ("day_closed", "dayclose")

_META_PREFIX = "day_closed:"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Closed copy for the panel (NFR-QLT-3, lexicon-scanned). Calm register, no
# exclamation marks — a shutdown is quiet by design.
DAYCLOSE_COPY: dict[str, str] = {
    "title": "Close the day",
    "intro": (
        "A short shutdown: a last look at today, one note on how it felt, done."
    ),
    "button": "Close the day",
    "closed": "Day closed.",
    # W4 validation probe (docs/COST_PER_TASK_RESEARCH.md §8): the felt count
    # is asked BEFORE any measured unit count is ever rendered anywhere — the
    # calibration mirror's no-peek rule. Optional, skippable, one tap.
    "pieces-question": (
        "Before the numbers: how many distinct pieces of work did today hold?"
    ),
    "pieces-skip": "Skip this one",
}

# The probe accepts a small closed range — a felt count is a handful, not a
# spreadsheet. Values outside are refused, never clamped (a refused write is
# honest; a silently altered one is not).
PIECES_MIN = 0
PIECES_MAX = 30

_PIECES_META_PREFIX = "wu_probe:"


def valid_day(day: str) -> bool:
    """A well-formed AND real UTC date (rejects 2026-13-40). The endpoint gate
    for the day-close write."""
    if not _DAY_RE.match(day):
        return False
    try:
        from datetime import date

        date.fromisoformat(day)
    except ValueError:
        return False
    return True


def record_day_close(store: Store, day: str) -> bool:
    """Mark one UTC day closed. Idempotent (last write wins, same value), and a
    malformed day is refused (False) rather than written. Local-only."""
    if not valid_day(day):
        return False
    store.meta_set(f"{_META_PREFIX}{day}", "1")
    return True


def is_day_closed(store: Store, day: str) -> bool:
    """Whether the given UTC day has been closed. False for any unknown or
    malformed day — the absence of a flag is simply 'not closed yet'."""
    return store.meta_get(f"{_META_PREFIX}{day}") == "1"


def record_pieces_probe(store: Store, day: str, felt: object) -> bool:
    """Journal one felt-vs-measured pair for the W4 validation (no-peek: the
    measured side is computed HERE, server-side, at record time — the client
    never sees or supplies it, so the felt answer cannot be anchored).
    Refuses malformed days, non-integers, and out-of-range counts. First
    answer per day wins — a probe is not editable, or it is not a probe."""
    if not valid_day(day):
        return False
    if isinstance(felt, bool) or not isinstance(felt, int):
        return False
    if not PIECES_MIN <= felt <= PIECES_MAX:
        return False
    key = f"{_PIECES_META_PREFIX}{day}"
    if store.meta_get(key) is not None:
        return False
    import json
    from datetime import date as _date

    from practicegraph.analysis.schedule import read_schedule_profile
    from practicegraph.analysis.workunits import measured_units_for_day

    profile = read_schedule_profile(store.path.parent)
    measured = measured_units_for_day(store, _date.fromisoformat(day), profile)
    store.meta_set(key, json.dumps({"felt": felt, "measured": measured}))
    return True
