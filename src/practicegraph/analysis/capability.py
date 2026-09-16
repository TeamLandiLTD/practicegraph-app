"""Pure, closed capability practice selection and local audit composition."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote

from practicegraph.analysis.capability_ledger import (
    CAPABILITY_SKIP_KEY,
    OutcomeEntry,
    PracticeEntry,
    PracticeReview,
    pending_outcome_unit,
    read_outcomes,
    read_practice,
    read_reviews,
    write_practice,
)
from practicegraph.analysis.capability_units import (
    CapabilityUnitSummary,
    closed_capability_units,
)
from practicegraph.analysis.playbook import CODEX_NEW_PREFIX
from practicegraph.analysis.schedule import ScheduleProfile
from practicegraph.config import Prefs
from practicegraph.store import Store

WIRE_FORBIDDEN_TERMS = (
    "capability_outcome",
    "capability_practice",
    "capability_path",
    "unit_key",
)


@dataclass(frozen=True, slots=True)
class CapabilityOpportunity:
    opportunity_id: str
    practice_id: str
    capability_id: str
    path: str
    title: str
    observation: str
    why: str
    practice: str
    prompt: str
    codex_url: str
    confidence: str
    baseline_value: int


@dataclass(frozen=True, slots=True)
class CapabilityPracticeResult:
    practice_id: str
    title: str
    status: str
    comparable_units: int
    baseline_value: int
    current_value: int
    line: str


@dataclass(frozen=True, slots=True)
class CapabilityPracticeProgress:
    title: str
    practice: str
    codex_url: str
    comparable_units: int
    context: str


@dataclass(frozen=True, slots=True)
class CapabilityReading:
    paths: tuple[str, ...]
    paths_confirmed: bool
    pending_outcome: bool
    opportunity: CapabilityOpportunity | None
    practice_result: CapabilityPracticeResult | None
    practice_progress: CapabilityPracticeProgress | None = None
    history: tuple[PracticeReview, ...] = ()
    pending_subject: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityRule:
    practice_id: str
    capability_id: str
    path: str
    title: str
    observation: str
    why: str
    practice: str
    prompt: str
    higher_is_better: bool


CAPABILITY_RULES = (
    CapabilityRule(
        practice_id="diagnose_before_retry",
        capability_id="choose-approach",
        path="shared",
        title="Pause before retrying",
        observation="This piece of work repeated command failures and retried quickly.",
        why="A retry without a changed approach can repeat the same blocker.",
        practice=(
            "Before another run, ask Codex to name the blocker and propose the "
            "smallest verifiable next step."
        ),
        prompt=(
            "State the current blocker from the available evidence. Do not retry "
            "yet. Propose the smallest verifiable next step and explain what would "
            "show that it worked."
        ),
        higher_is_better=False,
    ),
    CapabilityRule(
        practice_id="run_verification",
        capability_id="verify-result",
        path="software",
        title="Make verification part of the handoff",
        observation="An edit was recorded with no test run visible in the available logs.",
        why="A short verification step makes the result easier to trust.",
        practice="Ask for the smallest relevant check before accepting the result.",
        prompt=(
            "Run the smallest relevant check for this change. Report what you ran, "
            "what passed or did not pass, and any part that remains unverified."
        ),
        higher_is_better=True,
    ),
    CapabilityRule(
        practice_id="review_deliverable",
        capability_id="verify-result",
        path="knowledge",
        title="Review the deliverable once more",
        observation="A document or export was recorded with no later edit visible in the logs.",
        why=(
            "When a result is only partly useful, a deliberate review can turn the "
            "gap into a precise revision."
        ),
        practice=(
            "Ask Codex to check the deliverable against the requested outcome, then "
            "revise the specific gaps it finds."
        ),
        prompt=(
            "Check this deliverable against the requested outcome and constraints. "
            "List the specific gaps, revise only those gaps, and state what still "
            "needs human judgment."
        ),
        higher_is_better=True,
    ),
)

_RESULT_LINES = {
    "diagnose_before_retry": {
        "improved": (
            "The next three comparable pieces of work used fewer immediate retries."
        ),
        "unchanged": (
            "The next three comparable pieces of work used the same number of immediate retries."
        ),
        "other_way": (
            "The next three comparable pieces of work used more immediate retries."
        ),
    },
    "run_verification": {
        "improved": "The next three comparable changes included verification more often.",
        "unchanged": (
            "The next three comparable changes showed no movement in observed verification."
        ),
        "other_way": "The next three comparable changes included verification less often.",
    },
    "review_deliverable": {
        "improved": (
            "The next three comparable deliverables included a revision more often."
        ),
        "unchanged": (
            "The next three comparable deliverables showed no movement in observed revision."
        ),
        "other_way": (
            "The next three comparable deliverables included a revision less often."
        ),
    },
}

_PROGRESS_CONTEXT = (
    "The local check will use the next three comparable pieces of work.",
    "One of three comparable pieces of work is ready for the local check.",
    "Two of three comparable pieces of work are ready for the local check.",
)


def _rule_for(practice_id: str) -> CapabilityRule | None:
    return next(
        (rule for rule in CAPABILITY_RULES if rule.practice_id == practice_id),
        None,
    )


def _valid_practice_rule(practice: PracticeEntry) -> CapabilityRule | None:
    rule = _rule_for(practice.practice_id)
    if rule is None or practice.title != rule.title or practice.path != rule.path:
        return None
    return rule


def measure_for(practice_id: str, summary: CapabilityUnitSummary) -> int:
    """Return the closed audit measure for one practice and local work unit."""
    if practice_id == "diagnose_before_retry":
        return summary.retries
    if practice_id == "run_verification":
        return 100 if summary.test_run_attempts > 0 else 0
    if practice_id == "review_deliverable":
        return 100 if summary.files_edited > 0 else 0
    raise ValueError("unknown capability practice")


def _matches_rule(
    rule: CapabilityRule,
    summary: CapabilityUnitSummary,
    recorded_outcome: OutcomeEntry,
    paths: tuple[str, ...],
) -> bool:
    if rule.practice_id == "diagnose_before_retry":
        return summary.command_failures >= 2 and summary.retries >= 2
    if rule.practice_id == "run_verification":
        return (
            "software" in paths
            and summary.software_evidence
            and (summary.files_created > 0 or summary.files_edited > 0)
            and summary.test_run_attempts == 0
        )
    if rule.practice_id == "review_deliverable":
        return (
            "knowledge" in paths
            and summary.knowledge_evidence
            and (summary.doc_files_created > 0 or summary.export_writes > 0)
            and summary.files_edited == 0
            and recorded_outcome.outcome in ("partly", "no")
        )
    return False


def choose_opportunity(
    summary: CapabilityUnitSummary,
    recorded_outcome: OutcomeEntry,
    paths: tuple[str, ...],
) -> CapabilityOpportunity | None:
    """Choose the first matching closed rule without reading or writing state."""
    for rule in CAPABILITY_RULES:
        if not _matches_rule(rule, summary, recorded_outcome, paths):
            continue
        return CapabilityOpportunity(
            opportunity_id=f"{rule.practice_id}:{summary.unit_key[:12]}",
            practice_id=rule.practice_id,
            capability_id=rule.capability_id,
            path=rule.path,
            title=rule.title,
            observation=rule.observation,
            why=rule.why,
            practice=rule.practice,
            prompt=rule.prompt,
            codex_url=CODEX_NEW_PREFIX + quote(rule.prompt, safe=""),
            confidence="limited",
            baseline_value=measure_for(rule.practice_id, summary),
        )
    return None


def _matches_evidence_path(rule: CapabilityRule, summary: CapabilityUnitSummary) -> bool:
    if rule.path == "shared":
        return summary.knowledge_evidence or summary.software_evidence
    if rule.path == "knowledge":
        return summary.knowledge_evidence
    return summary.software_evidence


def _comparable_units(
    practice: PracticeEntry,
    later_units: tuple[CapabilityUnitSummary, ...],
) -> tuple[CapabilityRule, tuple[CapabilityUnitSummary, ...]] | None:
    rule = _valid_practice_rule(practice)
    if rule is None:
        return None
    try:
        baseline_last_ts = datetime.fromisoformat(practice.baseline_last_ts)
        if practice.accepted_at:
            baseline_last_ts = max(baseline_last_ts, datetime.fromisoformat(practice.accepted_at))
        comparable = tuple(
            sorted(
                (
                    unit
                    for unit in later_units
                    if unit.first_ts > baseline_last_ts
                    and _matches_evidence_path(rule, unit)
                ),
                key=lambda unit: (unit.last_ts, unit.unit_key),
            )[:3]
        )
    except (TypeError, ValueError):
        return None
    return rule, comparable


def _practice_progress(
    practice: PracticeEntry,
    later_units: tuple[CapabilityUnitSummary, ...],
) -> CapabilityPracticeProgress | None:
    selected = _comparable_units(practice, later_units)
    if selected is None:
        return None
    rule, comparable = selected
    count = len(comparable)
    if count >= 3:
        return None
    return CapabilityPracticeProgress(
        title=rule.title,
        practice=rule.practice,
        codex_url=CODEX_NEW_PREFIX + quote(rule.prompt, safe=""),
        comparable_units=count,
        context=_PROGRESS_CONTEXT[count],
    )


def audit_practice(
    practice: PracticeEntry,
    later_units: tuple[CapabilityUnitSummary, ...],
) -> CapabilityPracticeResult | None:
    """Audit an exact active rule against its first three comparable later units."""
    selected = _comparable_units(practice, later_units)
    if selected is None:
        return None
    rule, comparable = selected
    if len(comparable) < 3:
        return None
    current_value = sum(measure_for(rule.practice_id, unit) for unit in comparable) // 3
    if current_value == practice.baseline_value:
        status = "unchanged"
    elif (current_value > practice.baseline_value) == rule.higher_is_better:
        status = "improved"
    else:
        status = "other_way"
    return CapabilityPracticeResult(
        practice_id=rule.practice_id,
        title=rule.title,
        status=status,
        comparable_units=3,
        baseline_value=practice.baseline_value,
        current_value=current_value,
        line=_RESULT_LINES[rule.practice_id][status],
    )


def _current_opportunity(
    units: tuple[CapabilityUnitSummary, ...],
    outcomes: tuple[OutcomeEntry, ...],
    paths: tuple[str, ...],
) -> tuple[CapabilityOpportunity, CapabilityUnitSummary] | None:
    if not outcomes:
        return None
    latest_outcome = max(
        outcomes,
        key=lambda entry: (entry.day, entry.last_ts, entry.unit_key),
    )
    summary = next(
        (unit for unit in units if unit.unit_key == latest_outcome.unit_key),
        None,
    )
    if summary is None:
        return None
    opportunity = choose_opportunity(summary, latest_outcome, paths)
    if opportunity is None:
        return None
    return opportunity, summary


def accept_practice(
    store: Store,
    practice_id: str,
    units: tuple[CapabilityUnitSummary, ...],
    outcomes: tuple[OutcomeEntry, ...],
    paths: tuple[str, ...],
    today: date,
    *,
    accepted_at: datetime | None = None,
) -> PracticeEntry:
    """Accept only the exact current server-resolved capability opportunity."""
    if read_practice(store) is not None:
        raise ValueError("not the current capability opportunity")
    current = _current_opportunity(units, outcomes, paths)
    if current is None or current[0].practice_id != practice_id:
        raise ValueError("not the current capability opportunity")
    opportunity, summary = current
    if any(review.baseline_unit_key == summary.unit_key for review in read_reviews(store)):
        raise ValueError("practice already reviewed for this piece of work")
    practice = PracticeEntry(
        practice_id=opportunity.practice_id,
        title=opportunity.title,
        path=opportunity.path,
        accepted_day=today.isoformat(),
        baseline_unit_key=summary.unit_key,
        baseline_last_ts=summary.last_ts.isoformat(),
        baseline_value=opportunity.baseline_value,
        accepted_at=accepted_at.isoformat() if accepted_at else "",
    )
    write_practice(store, practice)
    return practice


def compose_capability_reading(
    store: Store,
    prefs: Prefs,
    today: date,
    as_of: datetime,
    *,
    schedule: ScheduleProfile | None = None,
) -> CapabilityReading:
    """Compose one deterministic local reading without mutating persisted state."""
    units = closed_capability_units(store, today, as_of, schedule=schedule)
    outcomes = read_outcomes(store)
    active_practice = read_practice(store)
    try:
        history = read_reviews(store)
    except ValueError:
        history = ()
    pending = pending_outcome_unit(units, outcomes, today)
    opportunity: CapabilityOpportunity | None = None
    practice_result: CapabilityPracticeResult | None = None
    practice_progress: CapabilityPracticeProgress | None = None
    if active_practice is not None:
        if _valid_practice_rule(active_practice) is not None:
            try:
                baseline_last_ts = datetime.fromisoformat(
                    active_practice.baseline_last_ts
                )
                if baseline_last_ts <= as_of:
                    audit_units = closed_capability_units(
                        store,
                        today,
                        as_of,
                        starting_after=baseline_last_ts,
                        schedule=schedule,
                    )
                    practice_result = audit_practice(active_practice, audit_units)
                    if practice_result is None:
                        practice_progress = _practice_progress(
                            active_practice,
                            audit_units,
                        )
            except (TypeError, ValueError):
                pass
    else:
        current = _current_opportunity(units, outcomes, prefs.capability_paths)
        if current is not None and not any(
            review.baseline_unit_key == current[1].unit_key for review in history
        ):
            opportunity = current[0]
    return CapabilityReading(
        paths=prefs.capability_paths,
        paths_confirmed=prefs.capability_paths_confirmed,
        pending_outcome=(
            pending is not None and active_practice is None
            and store.meta_get(CAPABILITY_SKIP_KEY) != today.isoformat()
        ),
        opportunity=opportunity,
        practice_result=practice_result,
        practice_progress=practice_progress,
        history=history,
        pending_subject=(
            f"Recorded work from {pending.first_ts.strftime('%Y-%m-%d %H:%M UTC')} "
            f"to {pending.last_ts.strftime('%Y-%m-%d %H:%M UTC')} "
            f"· {pending.assistant_turns} assistant turns"
            if pending is not None else None
        ),
    )
