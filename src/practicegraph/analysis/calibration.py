"""The Calibration Mirror: what a session felt like, against what it was.

The one instrument only this product can build. Session grain (W2.0) knows
exactly how long a session ran; nobody else holds that history locally. So
once in a while, at a natural stopping point, we ask for the estimate *first*
and show the clock *second* — and keep both.

**The ordering is the feature.** An estimate made after seeing the answer is
not an estimate. The pending probe therefore carries the question and the
options and nothing else: the actual length is resolved server-side at record
time, never sent to the client beforehand. A test pins that.

**Why a new measure rather than the existing one.** The product already stores
a 1-5 daily note, but it is `legacy_daily_feel/v1` — retained for historical
continuity and excluded by rule from every new construct. Reusing it here would
launder legacy data into a new claim. This defines its own versioned measure
instead, with declared anchors and direction.

**What it may say.** The mirror reports the estimate, the clock, and the plain
arithmetic between them. It never grades the person: a gap is a fact about
estimation, not a verdict about competence, and "you are miscalibrated" is a
sentence this module cannot produce. Bracket comparison keeps it honest —
buckets, never a spurious "you were off by 37%". Thin history stays silent
(no summary under the floor), and no durable-change claim is ever made from
measurement alone: if calibration improves, the ledger will show it in the
person's own numbers or we say nothing.

Autonomy (PRINCIPLES §5): the probe is opt-out via the coaching switch, offered
at most once a day, only for a session that has already ended, and skipping one
costs nothing — a declined probe simply never returns for that session.

Determinism (INV-6): the reading and the eligibility are functions of (store,
today). Local-only forever (NFR-PRV-6): the ledger lives in the meta table, the
session identity inside it never reaches a surface, and nothing here feeds an
emit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta

from practicegraph.analysis.sessions import RECEIPT_MIN_TURNS, session_minutes
from practicegraph.report.format import count, duration_hm
from practicegraph.store import SessionRow, Store

# The wire field-name scan pins these out of any payload (test_wire).
# Deliberately NOT the bare word "estimate": cost figures are estimates and
# the payload carries `is_estimate` legitimately. The banned vocabulary is the
# self-report's own, which must never travel.
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "calibration",
    "felt_session_length",
    "felt_bucket",
)

# The measure, versioned like any instrument: id, anchors, and direction are
# part of the contract. `descriptive` — a longer or shorter estimate is not
# better or worse, so no scale direction is implied.
CALIBRATION_MEASURE_ID = "felt_session_length/v1"
CALIBRATION_SCALE_DIRECTION = "descriptive"

CALIBRATION_LEDGER_KEY = "calibration_probes:v1"
CALIBRATION_MAX_ENTRIES = 24  # oldest dropped; this is a mirror, not an archive

# A session worth estimating: past the receipt floor and long enough that the
# estimate is a real judgment rather than a glance at a two-minute exchange.
CALIBRATION_MIN_TURNS = max(RECEIPT_MIN_TURNS, 20)

# How far back a session may be and still be worth asking about — an estimate
# of last week's work is memory, not calibration.
CALIBRATION_MAX_AGE_DAYS = 2

# Nothing is summarised under this many answered probes (withheld, not zero).
CALIBRATION_MIN_FOR_SUMMARY = 3

# Closed length brackets, ascending and contiguous. `ceiling_min` is exclusive
# upper bound in minutes; the last bracket is open-ended (None).
CALIBRATION_BUCKETS: tuple[tuple[str, str, int | None], ...] = (
    ("under_30m", "under 30 minutes", 30),
    ("30_60m", "30 to 60 minutes", 60),
    ("1_2h", "1 to 2 hours", 120),
    ("2_4h", "2 to 4 hours", 240),
    ("over_4h", "over 4 hours", None),
)

CALIBRATION_BUCKET_IDS: tuple[str, ...] = tuple(b[0] for b in CALIBRATION_BUCKETS)
CALIBRATION_BUCKET_LABELS: dict[str, str] = {b[0]: b[1] for b in CALIBRATION_BUCKETS}

# Closed copy (NFR-QLT-3, lexicon-scanned). The question never hints at an
# answer, and no line grades the person.
CALIBRATION_COPY: dict[str, str] = {
    "eyebrow": "How long did that feel?",
    "question": "Before you look - how long did that session feel?",
    "intro": (
        "Guess first, then see the clock. Both are kept so you can see how "
        "they track."
    ),
    "skip": "Skip this one",
    "match": (
        "You estimated {felt}; it ran {actual}. Same bracket."
    ),
    "under": (
        "You estimated {felt}; it ran {actual} - longer than it felt."
    ),
    "over": (
        "You estimated {felt}; it ran {actual} - shorter than it felt."
    ),
    "summary": (
        "Across your last {total} estimates, {matched} landed in the same "
        "bracket as the clock."
    ),
    "summary-lean-under": (
        "When they differ, the session has usually run longer than it felt."
    ),
    "summary-lean-over": (
        "When they differ, the session has usually run shorter than it felt."
    ),
}


@dataclass(frozen=True, slots=True)
class CalibrationProbe:
    """A pending question. Carries NO actual length and no session identity —
    the whole point is that the estimate precedes the answer."""

    question: str
    intro: str
    options: tuple[tuple[str, str], ...]  # (bucket_id, label)


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """One answered probe, rendered: the estimate, the clock, the arithmetic."""

    felt_label: str
    actual_label: str
    actual_minutes: int
    bucket_delta: int  # actual bracket minus felt bracket; 0 = same bracket
    line: str


@dataclass(frozen=True, slots=True)
class CalibrationReading:
    """The surface: a pending probe, the most recent result, and — once there
    is enough history — a neutral aggregate. Any of them may be absent."""

    available: bool
    probe: CalibrationProbe | None
    last: CalibrationResult | None
    summary: str


_EMPTY = CalibrationReading(
    available=False, probe=None, last=None, summary=""
)


def bucket_for_minutes(minutes: int) -> str:
    """The bracket a real duration falls in — the same closed set the person
    chooses from, so the comparison is like for like."""
    for bucket_id, _label, ceiling in CALIBRATION_BUCKETS:
        if ceiling is None or minutes < ceiling:
            return bucket_id
    return CALIBRATION_BUCKET_IDS[-1]


def _bucket_index(bucket_id: str) -> int:
    return CALIBRATION_BUCKET_IDS.index(bucket_id)


def read_ledger(store: Store) -> list[dict[str, object]]:
    """The answered-probe ledger (local meta JSON, the coach_ledger precedent).
    Malformed content fails closed to empty rather than raising."""
    raw = store.meta_get(CALIBRATION_LEDGER_KEY)
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(entries, list):
        return []
    clean: list[dict[str, object]] = []
    for entry in entries:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("session_key"), str)
            and entry.get("felt") in CALIBRATION_BUCKET_IDS
            and isinstance(entry.get("actual_minutes"), int)
            and isinstance(entry.get("day"), str)
        ):
            clean.append(entry)
    return clean


def _write_ledger(store: Store, entries: list[dict[str, object]]) -> None:
    trimmed = entries[-CALIBRATION_MAX_ENTRIES:]
    store.meta_set(
        CALIBRATION_LEDGER_KEY, json.dumps(trimmed, sort_keys=True)
    )


def _eligible_session(
    store: Store, today: date, answered: set[str]
) -> SessionRow | None:
    """The session worth asking about: the most recent substantial one that
    ended, is still fresh, and has not been asked about already."""
    start = (today - timedelta(days=CALIBRATION_MAX_AGE_DAYS)).isoformat()
    rows = store.sessions_between(start, today.isoformat())
    candidates = [
        row
        for row in rows
        if row.assistant_turns >= CALIBRATION_MIN_TURNS
        and row.session_key not in answered
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.last_ts)


def pending_session_key(store: Store, today: date, coaching_enabled: bool) -> str:
    """The session a probe would ask about, or "" when none is eligible.

    The endpoint uses this to resolve the answer server-side, which is why the
    client never needs — and never receives — a session identity.
    """
    if not coaching_enabled:
        return ""
    entries = read_ledger(store)
    answered = {str(entry["session_key"]) for entry in entries}
    # One probe per local day, at most (PRINCIPLES §2: no nagging cadence).
    if any(str(entry["day"]) == today.isoformat() for entry in entries):
        return ""
    row = _eligible_session(store, today, answered)
    return row.session_key if row is not None else ""


def record_estimate(
    store: Store, today: date, felt_bucket: str, coaching_enabled: bool = True
) -> bool:
    """Record one estimate against the pending session, resolving the actual
    length server-side. False when the bucket is unknown or nothing is pending
    — never a write that invents a session."""
    if felt_bucket not in CALIBRATION_BUCKET_IDS:
        return False
    session_key = pending_session_key(store, today, coaching_enabled)
    if not session_key:
        return False
    start = (today - timedelta(days=CALIBRATION_MAX_AGE_DAYS)).isoformat()
    row = next(
        (
            candidate
            for candidate in store.sessions_between(start, today.isoformat())
            if candidate.session_key == session_key
        ),
        None,
    )
    if row is None:
        return False
    entries = read_ledger(store)
    entries.append(
        {
            "session_key": session_key,
            "day": today.isoformat(),
            "felt": felt_bucket,
            "actual_minutes": session_minutes(row),
            "measure_id": CALIBRATION_MEASURE_ID,
        }
    )
    _write_ledger(store, entries)
    return True


def _result(entry: dict[str, object]) -> CalibrationResult:
    """Render one ledger entry. Types are guaranteed by ``read_ledger``, which
    is the only way entries enter — the narrowing here is belt-and-braces."""
    felt = str(entry["felt"])
    raw_minutes = entry["actual_minutes"]
    actual_minutes = raw_minutes if isinstance(raw_minutes, int) else 0
    actual = bucket_for_minutes(actual_minutes)
    delta = _bucket_index(actual) - _bucket_index(felt)
    key = "match" if delta == 0 else ("under" if delta > 0 else "over")
    return CalibrationResult(
        felt_label=CALIBRATION_BUCKET_LABELS[felt],
        actual_label=duration_hm(actual_minutes),
        actual_minutes=actual_minutes,
        bucket_delta=delta,
        line=CALIBRATION_COPY[key].format(
            felt=CALIBRATION_BUCKET_LABELS[felt],
            actual=duration_hm(actual_minutes),
        ),
    )


def _summary(entries: list[dict[str, object]]) -> str:
    """A neutral aggregate once there is enough history — how often the
    estimate and the clock agreed, and which way the misses lean. No trend
    claim: durability is demonstrated by the ledger over time or not said."""
    if len(entries) < CALIBRATION_MIN_FOR_SUMMARY:
        return ""
    results = [_result(entry) for entry in entries]
    matched = sum(1 for result in results if result.bucket_delta == 0)
    line = CALIBRATION_COPY["summary"].format(
        total=count(len(results)), matched=count(matched)
    )
    longer = sum(1 for result in results if result.bucket_delta > 0)
    shorter = sum(1 for result in results if result.bucket_delta < 0)
    if longer > shorter:
        return f"{line} {CALIBRATION_COPY['summary-lean-under']}"
    if shorter > longer:
        return f"{line} {CALIBRATION_COPY['summary-lean-over']}"
    return line


def compose_calibration(
    store: Store, today: date, coaching_enabled: bool = True
) -> CalibrationReading:
    """The calibration surface for today: a pending probe when one is due, the
    most recent answered result, and the aggregate once it is earned."""
    entries = read_ledger(store)
    session_key = pending_session_key(store, today, coaching_enabled)
    probe = (
        CalibrationProbe(
            question=CALIBRATION_COPY["question"],
            intro=CALIBRATION_COPY["intro"],
            options=tuple(
                (bucket_id, CALIBRATION_BUCKET_LABELS[bucket_id])
                for bucket_id in CALIBRATION_BUCKET_IDS
            ),
        )
        if session_key
        else None
    )
    last = _result(entries[-1]) if entries else None
    summary = _summary(entries)
    if probe is None and last is None:
        return _EMPTY
    return CalibrationReading(
        available=True, probe=probe, last=last, summary=summary
    )
