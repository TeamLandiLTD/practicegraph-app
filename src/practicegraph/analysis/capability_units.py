"""Closed local-only summaries of substantial, idle capability work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256

from practicegraph.analysis.schedule import (
    ScheduleProfile,
    compatibility_utc_schedule,
    local_day,
    local_day_bounds_utc,
)
from practicegraph.analysis.workunits import episodes_between
from practicegraph.store import MARK_FAILURES, MARK_RETRIES, Store

CAPABILITY_UNIT_MIN_TURNS = 5
CAPABILITY_UNIT_IDLE_MIN = 30
CAPABILITY_UNIT_MAX_AGE_DAYS = 2


@dataclass(frozen=True, slots=True)
class CapabilityUnitSummary:
    """A local capability unit; its identity must never leave local analysis."""

    unit_key: str
    first_ts: datetime
    last_ts: datetime
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


def closed_capability_units(
    store: Store,
    today: date,
    as_of: datetime,
    *,
    starting_after: datetime | None = None,
    schedule: ScheduleProfile | None = None,
) -> tuple[CapabilityUnitSummary, ...]:
    """Return closed substantial episodes inside one explicit local horizon."""
    profile = schedule or compatibility_utc_schedule()
    from_day = (
        local_day(starting_after, profile)
        if starting_after is not None
        else today - timedelta(days=CAPABILITY_UNIT_MAX_AGE_DAYS)
    )
    if from_day > today:
        return ()
    start, _ = local_day_bounds_utc(from_day, profile)
    _, end = local_day_bounds_utc(today, profile)
    episodes = episodes_between(store, start.date(), end.date())
    marks = store.marks_between(start.date().isoformat(), end.date().isoformat())
    closed_before = as_of - timedelta(minutes=CAPABILITY_UNIT_IDLE_MIN)
    units: list[CapabilityUnitSummary] = []
    for episode in episodes:
        if episode.assistant_turns < CAPABILITY_UNIT_MIN_TURNS:
            continue
        if starting_after is not None and episode.first_ts <= starting_after:
            continue
        if episode.last_ts > closed_before or not start <= episode.last_ts < end:
            continue
        episode_marks = [
            row[1:]
            for row in marks
            if row[1] in episode.session_keys
            and episode.first_ts <= datetime.fromisoformat(row[2]) <= episode.last_ts
        ]
        retries = sum(int(mark[MARK_RETRIES]) for mark in episode_marks)
        failures = sum(int(mark[MARK_FAILURES]) for mark in episode_marks)
        key_material = "\0".join(
            (
                *sorted(episode.session_keys),
                episode.first_ts.isoformat(),
                episode.last_ts.isoformat(),
            )
        )
        units.append(
            CapabilityUnitSummary(
                unit_key=sha256(key_material.encode("utf-8")).hexdigest()[:24],
                first_ts=episode.first_ts,
                last_ts=episode.last_ts,
                assistant_turns=episode.assistant_turns,
                prompt_tokens=(
                    episode.input_tokens
                    + episode.cached_tokens
                    + episode.cache_creation_tokens
                ),
                cost_micro_usd=episode.cost_micro_usd,
                retries=retries,
                command_failures=failures,
                git_commit_attempts=episode.git_commit_attempts,
                test_run_attempts=episode.test_run_attempts,
                files_created=episode.files_created,
                doc_files_created=episode.doc_files_created,
                export_writes=episode.export_writes,
                files_edited=episode.files_edited,
                knowledge_evidence=(
                    episode.doc_files_created > 0 or episode.export_writes > 0
                ),
                software_evidence=(
                    bool(episode.branch_hash)
                    or episode.test_run_attempts > 0
                    or episode.git_commit_attempts > 0
                ),
            )
        )
    return tuple(sorted(units, key=lambda unit: (unit.last_ts, unit.unit_key)))
