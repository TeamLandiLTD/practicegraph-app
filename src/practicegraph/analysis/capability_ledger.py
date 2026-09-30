"""Bounded, local-only outcome and active-practice records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from practicegraph.analysis.capability_units import CapabilityUnitSummary
from practicegraph.store import Store

CAPABILITY_OUTCOMES_KEY = "capability_outcomes:v1"
CAPABILITY_PRACTICE_KEY = "capability_practice:v1"
CAPABILITY_OUTCOME_IDS = ("yes", "partly", "no")
CAPABILITY_RULE_PATHS = ("shared", "knowledge", "software")
CAPABILITY_OUTCOME_MAX_ENTRIES = 48
CAPABILITY_REVIEWS_KEY = "capability_reviews:v1"
CAPABILITY_SKIP_KEY = "capability_skip_day:v1"
CAPABILITY_FEEDBACK_IDS = ("helpful", "not_helpful", "uncertain", "not_tried")


@dataclass(frozen=True, slots=True)
class PracticeReview:
    practice_id: str
    title: str
    baseline_unit_key: str
    accepted_day: str
    finished_day: str
    feedback: str


def read_reviews(store: Store) -> tuple[PracticeReview, ...]:
    """Read bounded, closed self-reports; never interpret them as measured skill."""
    raw = store.meta_get(CAPABILITY_REVIEWS_KEY)
    if raw is None:
        return ()
    try:
        values = json.loads(raw)
        fields = set(PracticeReview.__dataclass_fields__)
        if not isinstance(values, list) or any(
            not isinstance(value, dict)
            or set(value) != fields
            or any(type(part) is not str for part in value.values())
            or value["feedback"] not in CAPABILITY_FEEDBACK_IDS
            for value in values
        ):
            raise ValueError("malformed capability reviews")
        return tuple(PracticeReview(**value) for value in values[-48:])
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed capability reviews") from exc


def finish_practice(store: Store, feedback: str, today: date) -> PracticeReview:
    """Keep the person's review before releasing the single active practice."""
    practice = read_practice(store)
    if practice is None or feedback not in CAPABILITY_FEEDBACK_IDS:
        raise ValueError("no active practice or invalid feedback")
    previous = read_reviews(store)
    review = PracticeReview(
        practice.practice_id, practice.title, practice.baseline_unit_key,
        practice.accepted_day, today.isoformat(), feedback,
    )
    store.meta_set(
        CAPABILITY_REVIEWS_KEY,
        json.dumps([asdict(item) for item in (*previous, review)[-48:]], sort_keys=True),
    )
    clear_practice(store)
    return review


@dataclass(frozen=True, slots=True)
class OutcomeEntry:
    unit_key: str
    day: str
    outcome: str
    last_ts: str
    assistant_turns: int
    prompt_tokens: int
    cost_micro_usd: int
    retries: int
    command_failures: int
    git_commit_attempts: int
    test_run_attempts: int
    files_created: int
    doc_files_created: int
    export_writes: int
    files_edited: int
    knowledge_evidence: bool
    software_evidence: bool


@dataclass(frozen=True, slots=True)
class PracticeEntry:
    practice_id: str
    title: str
    path: str
    accepted_day: str
    baseline_unit_key: str
    baseline_last_ts: str
    baseline_value: int
    accepted_at: str = ""


_OUTCOME_STR_FIELDS = ("unit_key", "day", "outcome", "last_ts")
_OUTCOME_INT_FIELDS = (
    "assistant_turns",
    "prompt_tokens",
    "cost_micro_usd",
    "retries",
    "command_failures",
    "git_commit_attempts",
    "test_run_attempts",
    "files_created",
    "doc_files_created",
    "export_writes",
    "files_edited",
)
_OUTCOME_BOOL_FIELDS = ("knowledge_evidence", "software_evidence")
_OUTCOME_FIELDS = frozenset((*_OUTCOME_STR_FIELDS, *_OUTCOME_INT_FIELDS, *_OUTCOME_BOOL_FIELDS))
_PRACTICE_STR_FIELDS = (
    "practice_id",
    "title",
    "path",
    "accepted_day",
    "baseline_unit_key",
    "baseline_last_ts",
    "accepted_at",
)
_PRACTICE_FIELDS = frozenset((*_PRACTICE_STR_FIELDS, "baseline_value"))


def _valid_outcome_entry(value: object) -> OutcomeEntry | None:
    if not isinstance(value, dict) or set(value) != _OUTCOME_FIELDS:
        return None
    if any(type(value[field]) is not str for field in _OUTCOME_STR_FIELDS):
        return None
    if any(type(value[field]) is not int for field in _OUTCOME_INT_FIELDS):
        return None
    if any(type(value[field]) is not bool for field in _OUTCOME_BOOL_FIELDS):
        return None
    if value["outcome"] not in CAPABILITY_OUTCOME_IDS:
        return None
    return OutcomeEntry(
        unit_key=value["unit_key"],
        day=value["day"],
        outcome=value["outcome"],
        last_ts=value["last_ts"],
        assistant_turns=value["assistant_turns"],
        prompt_tokens=value["prompt_tokens"],
        cost_micro_usd=value["cost_micro_usd"],
        retries=value["retries"],
        command_failures=value["command_failures"],
        git_commit_attempts=value["git_commit_attempts"],
        test_run_attempts=value["test_run_attempts"],
        files_created=value["files_created"],
        doc_files_created=value["doc_files_created"],
        export_writes=value["export_writes"],
        files_edited=value["files_edited"],
        knowledge_evidence=value["knowledge_evidence"],
        software_evidence=value["software_evidence"],
    )


def _valid_practice_entry(value: object) -> PracticeEntry | None:
    if isinstance(value, dict) and "accepted_at" not in value:
        value = {**value, "accepted_at": ""}  # legacy records remain readable
    if not isinstance(value, dict) or set(value) != _PRACTICE_FIELDS:
        return None
    if any(type(value[field]) is not str for field in _PRACTICE_STR_FIELDS):
        return None
    if type(value["baseline_value"]) is not int:
        return None
    if value["path"] not in CAPABILITY_RULE_PATHS:
        return None
    return PracticeEntry(
        practice_id=value["practice_id"],
        title=value["title"],
        path=value["path"],
        accepted_day=value["accepted_day"],
        baseline_unit_key=value["baseline_unit_key"],
        baseline_last_ts=value["baseline_last_ts"],
        baseline_value=value["baseline_value"],
        accepted_at=value["accepted_at"],
    )


def _stored_outcomes(store: Store) -> tuple[OutcomeEntry, ...] | None:
    """Decode outcomes, preserving malformed state for mutation refusal."""
    raw = store.meta_get(CAPABILITY_OUTCOMES_KEY)
    if raw is None:
        return ()
    try:
        decoded: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if type(decoded) is not list:
        return None
    entries = tuple(_valid_outcome_entry(value) for value in decoded)
    if any(entry is None for entry in entries):
        return None
    valid_entries = tuple(entry for entry in entries if entry is not None)
    if len({entry.unit_key for entry in valid_entries}) != len(valid_entries):
        return None
    return valid_entries


def read_outcomes(store: Store) -> tuple[OutcomeEntry, ...]:
    """Return every valid persisted outcome, or no history on malformed data."""
    return _stored_outcomes(store) or ()


def pending_outcome_unit(
    units: tuple[CapabilityUnitSummary, ...],
    outcomes: tuple[OutcomeEntry, ...],
    today: date,
) -> CapabilityUnitSummary | None:
    """Offer the most recent unrecorded unit unless today's prompt was answered."""
    if any(entry.day == today.isoformat() for entry in outcomes):
        return None
    answered_keys = {entry.unit_key for entry in outcomes}
    return next(
        (unit for unit in reversed(units) if unit.unit_key not in answered_keys),
        None,
    )


def record_outcome(
    store: Store, unit: CapabilityUnitSummary, outcome: str, today: date
) -> OutcomeEntry:
    """Persist one closed outcome for a local capability unit."""
    if outcome not in CAPABILITY_OUTCOME_IDS:
        raise ValueError("invalid capability outcome")
    entries = _stored_outcomes(store)
    if entries is None:
        raise ValueError("malformed capability outcomes")
    if any(entry.unit_key == unit.unit_key for entry in entries):
        raise ValueError("duplicate capability outcome")
    entry = OutcomeEntry(
        unit_key=unit.unit_key,
        day=today.isoformat(),
        outcome=outcome,
        last_ts=unit.last_ts.isoformat(),
        assistant_turns=unit.assistant_turns,
        prompt_tokens=unit.prompt_tokens,
        cost_micro_usd=unit.cost_micro_usd,
        retries=unit.retries,
        command_failures=unit.command_failures,
        git_commit_attempts=unit.git_commit_attempts,
        test_run_attempts=unit.test_run_attempts,
        files_created=unit.files_created,
        doc_files_created=unit.doc_files_created,
        export_writes=unit.export_writes,
        files_edited=unit.files_edited,
        knowledge_evidence=unit.knowledge_evidence,
        software_evidence=unit.software_evidence,
    )
    retained = tuple(
        sorted(
            (*entries, entry),
            key=lambda existing: (existing.day, existing.last_ts, existing.unit_key),
        )[-CAPABILITY_OUTCOME_MAX_ENTRIES:]
    )
    store.meta_set(
        CAPABILITY_OUTCOMES_KEY,
        json.dumps([asdict(existing) for existing in retained], sort_keys=True),
    )
    return entry


def read_practice(store: Store) -> PracticeEntry | None:
    """Return the active practice only when its complete stored shape is valid."""
    raw = store.meta_get(CAPABILITY_PRACTICE_KEY)
    if raw is None:
        return None
    try:
        decoded: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return _valid_practice_entry(decoded)


def write_practice(store: Store, practice: PracticeEntry) -> None:
    """Replace the single active practice with its canonical local record."""
    raw = asdict(practice)
    if _valid_practice_entry(raw) is None:
        raise ValueError("invalid capability practice")
    store.meta_set(CAPABILITY_PRACTICE_KEY, json.dumps(raw, sort_keys=True))


def clear_practice(store: Store) -> None:
    """Clear the active-practice value without changing store schema."""
    store.meta_set(CAPABILITY_PRACTICE_KEY, json.dumps(None))


def clear_coach_history(store: Store) -> None:
    """Remove only private coach records, preserving activity and other ledgers."""
    store.meta_set(CAPABILITY_OUTCOMES_KEY, "[]")
    store.meta_set(CAPABILITY_REVIEWS_KEY, "[]")
    store.meta_set(CAPABILITY_SKIP_KEY, "")
    clear_practice(store)
