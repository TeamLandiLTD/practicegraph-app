"""Local failure and retry diagnostics from recorded activity marks.

Raw components are retained for analysis, without a personal score. The current
UI exposes only recorded failures and retries in optional details. The sequence
ending at a nonfailing tool event does not establish that the original operation
succeeded; pauses and folder changes do not measure wellbeing or verification.
These operational proxies are not a replication of a controlled research index.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median

from practicegraph.report.format import about
from practicegraph.store import (
    MARK_CWD_HASH,
    MARK_FAILURES,
    MARK_INTERRUPTIONS,
    MARK_RETRIES,
    MARK_TOOL_CALLS,
    DayMarkRow,
    Store,
)

VERIFICATION_MEASURE_ID = "verification_load_components/v1"
# MarkRow has no named constant for the kind column; it sits after the
# session key and timestamp.
_MARK_KIND = 2
VERIFICATION_WINDOW_DAYS = 28
# A gap this long between two marks inside one session is a pause.
VERIFICATION_PAUSE_MIN = 5
# Nothing is shown under this many sessions in the window (withheld, not zero).
VERIFICATION_MIN_SESSIONS = 5

VERIFICATION_COPY: dict[str, str] = {
    "title": "Recorded failures and retries",
    "eyebrow": "",
    "hint": "Recorded failures and retry events in supported tool logs. Totals can change with "
    "activity volume and task mix; they do not measure review quality.",
    "source": "Inspired by verification-effort research, including Fan et al., CHI 2026. These "
    "operational counters are not a replication of the study instrument.",
    "churn": "These logs do not establish whether a failed task was ultimately corrected or how "
    "thoroughly the work was reviewed.",
    "withheld": "Fewer than {floor} sessions this month - nothing to compare yet.",
    "line-more": "The logs recorded about {failures} command or test failures, {delta} more than "
    "the preceding window.",
    "line-fewer": "The logs recorded about {failures} command or test failures, {delta} fewer than "
    "the preceding window.",
    "line-flat": "The logs recorded about {failures} command or test failures, close to the "
    "preceding window.",
    "line-alone": "The logs recorded about {failures} command or test failures this window.",
    "passing-quick": " A nonfailing tool event usually followed on the next turn.",
    "passing-slow": " A nonfailing tool event followed after about {iterations} turns.",
}

# id -> (label, unit, documentation). The doc is the rule, stated plainly.
VERIFICATION_COMPONENTS: tuple[tuple[str, str, str, str], ...] = (
    ("failures", "Runs that failed", "runs", "Commands and tests that came back failing."),
    ("retries", "Quick retries", "retries", "Sent again within minutes of a failed run."),
    (
        "iterations_to_pass",
        "Turns until a nonfailing event",
        "turns",
        "Turns after a failure until a tool event without a recorded failure; "
        "it may concern different work.",
    ),
    (
        "pauses",
        "Pauses inside a session",
        "gaps",
        f"Gaps of {VERIFICATION_PAUSE_MIN} minutes or more between turns.",
    ),
    (
        "switches",
        "Jumps between tasks",
        "jumps",
        "Interruptions plus changes of working folder inside one session.",
    ),
)


@dataclass(frozen=True)
class VerificationComponent:
    id: str
    label: str
    unit: str
    doc: str
    value: int | float | None  # None = not observable this window
    delta: int | float | None  # vs the prior window; None when either is thin


@dataclass(frozen=True)
class VerificationReading:
    available: bool
    measure_id: str
    window_days: int
    sessions: int
    components: tuple[VerificationComponent, ...]
    line: str
    note: str  # the withheld explanation, or the churn line
    source: str


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _by_session(marks: list[DayMarkRow]) -> dict[str, list[DayMarkRow]]:
    grouped: dict[str, list[DayMarkRow]] = {}
    for row in marks:
        grouped.setdefault(row[1], []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda r: r[2])
    return grouped


def components_from_marks(marks: list[DayMarkRow]) -> dict[str, int | float | None]:
    """The raw components over a set of day-prefixed marks (MARK_* indexes
    shift +1 because the day leads the tuple)."""
    sessions = _by_session(marks)
    failures = retries = pauses = switches = 0
    to_pass: list[int] = []
    for rows in sessions.values():
        last_ts: datetime | None = None
        last_cwd: str | None = None
        first_failure_at: int | None = None
        turns_since_failure = 0
        for index, row in enumerate(rows):
            shifted = row[1:]
            ts = _parse_ts(row[2])
            f = int(shifted[MARK_FAILURES] or 0)
            failures += f
            retries += int(shifted[MARK_RETRIES] or 0)
            switches += int(shifted[MARK_INTERRUPTIONS] or 0)
            cwd = shifted[MARK_CWD_HASH]
            if last_cwd is not None and cwd and cwd != last_cwd:
                switches += 1
            if cwd:
                last_cwd = cwd
            if last_ts is not None and (ts - last_ts) >= timedelta(minutes=VERIFICATION_PAUSE_MIN):
                pauses += 1
            last_ts = ts
            # Iterations from the first failure to the first clean run.
            if first_failure_at is None:
                if f > 0:
                    first_failure_at = index
                    turns_since_failure = 0
            else:
                if shifted[_MARK_KIND] == "assistant_turn":
                    turns_since_failure += 1
                if int(shifted[MARK_TOOL_CALLS] or 0) > 0 and f == 0:
                    to_pass.append(turns_since_failure)
                    first_failure_at = None
    return {
        "sessions": len(sessions),
        "failures": failures,
        "retries": retries,
        "iterations_to_pass": median(to_pass) if to_pass else None,
        "pauses": pauses,
        "switches": switches,
    }


def _delta(current: int | float | None, prior: int | float | None) -> int | float | None:
    if current is None or prior is None:
        return None
    return round(current - prior, 1) if isinstance(current, float) else current - prior


def compose_verification(store: Store, today: date) -> VerificationReading:
    """The reading for the 28 days ending today, against the 28 before."""
    end = today
    start = end - timedelta(days=VERIFICATION_WINDOW_DAYS - 1)
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=VERIFICATION_WINDOW_DAYS - 1)
    current = components_from_marks(store.marks_between(start.isoformat(), end.isoformat()))
    prior = components_from_marks(
        store.marks_between(prior_start.isoformat(), prior_end.isoformat())
    )
    sessions = int(current["sessions"] or 0)
    source = VERIFICATION_COPY["source"]
    if sessions < VERIFICATION_MIN_SESSIONS:
        return VerificationReading(
            available=False,
            measure_id=VERIFICATION_MEASURE_ID,
            window_days=VERIFICATION_WINDOW_DAYS,
            sessions=sessions,
            components=(),
            line="",
            note=VERIFICATION_COPY["withheld"].format(floor=VERIFICATION_MIN_SESSIONS),
            source=source,
        )
    prior_thin = int(prior["sessions"] or 0) < VERIFICATION_MIN_SESSIONS
    components = tuple(
        VerificationComponent(
            id=component_id,
            label=label,
            unit=unit,
            doc=doc,
            value=current[component_id],
            delta=None if prior_thin else _delta(current[component_id], prior[component_id]),
        )
        for component_id, label, unit, doc in VERIFICATION_COMPONENTS
    )
    iterations = current["iterations_to_pass"]
    passing = ""
    if iterations is not None:
        if float(iterations) <= 1.0:
            passing = VERIFICATION_COPY["passing-quick"]
        else:
            shown: int | float = int(iterations) if float(iterations).is_integer() else iterations
            passing = VERIFICATION_COPY["passing-slow"].format(iterations=shown)
    failures = int(current["failures"] or 0)
    failure_delta = None if prior_thin else _delta(failures, prior["failures"])
    if failure_delta is None:
        line = VERIFICATION_COPY["line-alone"].format(failures=about(failures))
    elif failure_delta > 0:
        line = VERIFICATION_COPY["line-more"].format(
            failures=about(failures), delta=about(int(failure_delta))
        )
    elif failure_delta < 0:
        line = VERIFICATION_COPY["line-fewer"].format(
            failures=about(failures), delta=about(int(-failure_delta))
        )
    else:
        line = VERIFICATION_COPY["line-flat"].format(failures=about(failures))
    line += passing
    return VerificationReading(
        available=True,
        measure_id=VERIFICATION_MEASURE_ID,
        window_days=VERIFICATION_WINDOW_DAYS,
        sessions=sessions,
        components=components,
        line=line,
        note=VERIFICATION_COPY["churn"],
        source=source,
    )
