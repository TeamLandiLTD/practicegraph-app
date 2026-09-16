"""Deterministic private capability rule, acceptance, and audit contracts."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

from practicegraph.analysis.capability import (
    CapabilityOpportunity,
    accept_practice,
    audit_practice,
    choose_opportunity,
    compose_capability_reading,
    measure_for,
)
from practicegraph.analysis.capability_ledger import (
    CAPABILITY_PRACTICE_KEY,
    OutcomeEntry,
    PracticeEntry,
    read_outcomes,
    read_practice,
    record_outcome,
    write_practice,
)
from practicegraph.analysis.capability_units import (
    CapabilityUnitSummary,
    closed_capability_units,
)
from practicegraph.analysis.playbook import CODEX_NEW_PREFIX
from practicegraph.config import Prefs, read_prefs
from practicegraph.events import TokenCounts, Tool, TurnEvent, TurnKind
from practicegraph.history import _marks_from_events, _work_spans_from_events
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store

TODAY = date(2026, 8, 14)
NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    result = Store(tmp_path / "state.db")
    result.migrate()
    return result


@pytest.fixture
def prefs(tmp_path: Path) -> Prefs:
    return read_prefs(tmp_path)


def unit(
    *,
    unit_key: str = "0123456789abcdef01234567",
    first_ts: datetime = NOW - timedelta(hours=2),
    last_ts: datetime = NOW - timedelta(hours=1),
    assistant_turns: int = 6,
    prompt_tokens: int = 1_200,
    cost_micro_usd: int = 250,
    retries: int = 0,
    command_failures: int = 0,
    git_commit_attempts: int = 0,
    test_run_attempts: int = 0,
    files_created: int = 0,
    doc_files_created: int = 0,
    export_writes: int = 0,
    files_edited: int = 0,
    knowledge_evidence: bool = False,
    software_evidence: bool = False,
) -> CapabilityUnitSummary:
    """Build a complete local-only unit with every field explicitly assigned."""
    return CapabilityUnitSummary(
        unit_key=unit_key,
        first_ts=first_ts,
        last_ts=last_ts,
        assistant_turns=assistant_turns,
        prompt_tokens=prompt_tokens,
        cost_micro_usd=cost_micro_usd,
        retries=retries,
        command_failures=command_failures,
        git_commit_attempts=git_commit_attempts,
        test_run_attempts=test_run_attempts,
        files_created=files_created,
        doc_files_created=doc_files_created,
        export_writes=export_writes,
        files_edited=files_edited,
        knowledge_evidence=knowledge_evidence,
        software_evidence=software_evidence,
    )


def outcome(summary: CapabilityUnitSummary, result: str, *, day: date = TODAY) -> OutcomeEntry:
    """Mirror a persisted outcome's complete evidence snapshot."""
    return OutcomeEntry(
        unit_key=summary.unit_key,
        day=day.isoformat(),
        outcome=result,
        last_ts=summary.last_ts.isoformat(),
        assistant_turns=summary.assistant_turns,
        prompt_tokens=summary.prompt_tokens,
        cost_micro_usd=summary.cost_micro_usd,
        retries=summary.retries,
        command_failures=summary.command_failures,
        git_commit_attempts=summary.git_commit_attempts,
        test_run_attempts=summary.test_run_attempts,
        files_created=summary.files_created,
        doc_files_created=summary.doc_files_created,
        export_writes=summary.export_writes,
        files_edited=summary.files_edited,
        knowledge_evidence=summary.knowledge_evidence,
        software_evidence=summary.software_evidence,
    )


_PRACTICE_META = {
    "diagnose_before_retry": ("Pause before retrying", "shared"),
    "run_verification": ("Make verification part of the handoff", "software"),
    "review_deliverable": ("Review the deliverable once more", "knowledge"),
}


def accepted(
    practice_id: str,
    *,
    baseline_value: int,
    baseline_last_ts: datetime = NOW,
) -> PracticeEntry:
    title, path = _PRACTICE_META[practice_id]
    return PracticeEntry(
        practice_id=practice_id,
        title=title,
        path=path,
        accepted_day=TODAY.isoformat(),
        baseline_unit_key="baseline-unit-key",
        baseline_last_ts=baseline_last_ts.isoformat(),
        baseline_value=baseline_value,
    )


def comparable_unit(
    practice_id: str,
    value: int,
    index: int,
    *,
    last_ts: datetime | None = None,
    unit_key: str | None = None,
) -> CapabilityUnitSummary:
    timestamp = last_ts or NOW + timedelta(hours=index + 1)
    common = {
        "unit_key": unit_key or f"later-{index}",
        "first_ts": timestamp - timedelta(minutes=20),
        "last_ts": timestamp,
    }
    if practice_id == "diagnose_before_retry":
        return unit(**common, retries=value, command_failures=2, software_evidence=True)
    if practice_id == "run_verification":
        return unit(
            **common,
            test_run_attempts=1 if value == 100 else 0,
            files_created=1,
            software_evidence=True,
        )
    if practice_id == "review_deliverable":
        return unit(
            **common,
            doc_files_created=1,
            files_edited=1 if value == 100 else 0,
            knowledge_evidence=True,
        )
    raise AssertionError(f"unknown test practice: {practice_id}")


def capability_copy_surfaces_for_test() -> tuple[str, ...]:
    """Collect every closed, user-facing capability copy surface."""
    opportunity_fixtures = (
        (
            unit(command_failures=2, retries=2, software_evidence=True),
            "partly",
            ("knowledge", "software"),
            "diagnose_before_retry",
        ),
        (
            unit(software_evidence=True, files_edited=1),
            "yes",
            ("software",),
            "run_verification",
        ),
        (
            unit(knowledge_evidence=True, doc_files_created=1),
            "partly",
            ("knowledge",),
            "review_deliverable",
        ),
    )
    surfaces: list[str] = []
    for summary, result, paths, expected_practice_id in opportunity_fixtures:
        opportunity = choose_opportunity(summary, outcome(summary, result), paths)
        assert opportunity is not None
        assert opportunity.practice_id == expected_practice_id
        surfaces.extend(
            (
                opportunity.title,
                opportunity.observation,
                opportunity.why,
                opportunity.practice,
                opportunity.prompt,
            )
        )

    result_fixtures = (
        ("diagnose_before_retry", 2, (1, 1, 1), "improved"),
        ("diagnose_before_retry", 2, (2, 2, 2), "unchanged"),
        ("diagnose_before_retry", 2, (3, 3, 3), "other_way"),
        ("run_verification", 0, (100, 100, 100), "improved"),
        ("run_verification", 0, (0, 0, 0), "unchanged"),
        ("run_verification", 100, (0, 0, 0), "other_way"),
        ("review_deliverable", 0, (100, 100, 100), "improved"),
        ("review_deliverable", 0, (0, 0, 0), "unchanged"),
        ("review_deliverable", 100, (0, 0, 0), "other_way"),
    )
    for practice_id, baseline, values, expected_status in result_fixtures:
        practice = accepted(practice_id, baseline_value=baseline)
        later = tuple(
            comparable_unit(practice_id, value, index)
            for index, value in enumerate(values)
        )
        result = audit_practice(practice, later)
        assert result is not None
        assert result.status == expected_status
        surfaces.extend((result.title, result.line))
    return tuple(surfaces)


def test_capability_copy_is_closed_calm_and_non_evaluative() -> None:
    """Every rendered rule/result string stays private, calm, and non-ranking."""
    surfaces = capability_copy_surfaces_for_test()
    assert surfaces
    for text in surfaces:
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
    joined = " ".join(surfaces).lower()
    for forbidden in (
        "score",
        "rank",
        "percentile",
        "streak",
        "top performer",
        "company average",
        "credits used",
    ):
        assert forbidden not in joined


def seed_closed_retry_unit(store: Store) -> CapabilityUnitSummary:
    """Seed real history whose derived unit satisfies the shared retry rule."""
    events = tuple(
        TurnEvent(
            timestamp=datetime(2026, 8, 14, 9, tzinfo=UTC) + timedelta(minutes=index * 5),
            session_id="session-a",
            source_id="codex",
            tool=Tool.CODEX,
            kind=TurnKind.ASSISTANT_TURN,
            model="gpt-test",
            tokens=TokenCounts(input=100),
            retries=2 if index == 1 else 0,
            commands_failed=2 if index == 2 else 0,
            git_commit_attempts=0,
            test_run_attempts=0,
            files_created=0,
            doc_files_created=0,
            export_writes=0,
            files_edited=0,
            cwd_hash="project-a",
            branch_hash="branch-a",
        )
        for index in range(5)
    )
    store.replace_file_data(
        source_id="codex",
        path_hash="capability-retry-history",
        cursor=(1, 1, 1),
        contributions=[],
        marks=_marks_from_events(events),
        health=None,
        turn_keys=(),
        now=NOW,
        work_spans=_work_spans_from_events(events),
    )
    units = closed_capability_units(store, TODAY, NOW)
    assert len(units) == 1
    return units[0]


def seed_closed_software_unit(
    store: Store,
    day: date,
    suffix: str,
    *,
    test_run_attempts: int,
) -> CapabilityUnitSummary:
    """Seed and return one real, closed software capability unit."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=9)
    events = tuple(
        TurnEvent(
            timestamp=start + timedelta(minutes=index * 5),
            session_id=f"software-{suffix}",
            source_id="codex",
            tool=Tool.CODEX,
            kind=TurnKind.ASSISTANT_TURN,
            model="gpt-test",
            tokens=TokenCounts(input=100),
            test_run_attempts=test_run_attempts if index == 0 else 0,
            files_edited=1 if index == 0 else 0,
            cwd_hash=f"project-{suffix}",
            branch_hash=f"branch-{suffix}",
        )
        for index in range(5)
    )
    store.replace_file_data(
        source_id="codex",
        path_hash=f"software-history-{suffix}",
        cursor=(1, 1, 1),
        contributions=[],
        marks=_marks_from_events(events),
        health=None,
        turn_keys=(),
        now=NOW,
        work_spans=_work_spans_from_events(events),
    )
    expected_last_ts = events[-1].timestamp
    units = closed_capability_units(
        store,
        day,
        datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12),
    )
    return next(unit for unit in units if unit.last_ts == expected_last_ts)


@pytest.mark.parametrize("paths", [("knowledge",), ("software",), ("knowledge", "software")])
def test_diagnose_before_retry_has_first_priority(paths: tuple[str, ...]) -> None:
    """Moving the shared rule below a path rule would select the wrong practice."""
    summary = unit(command_failures=2, retries=2, software_evidence=True, files_edited=3)

    opportunity = choose_opportunity(summary, outcome(summary, "partly"), paths)

    assert opportunity is not None
    assert opportunity.practice_id == "diagnose_before_retry"
    assert opportunity.baseline_value == 2


def test_software_verification_requires_observed_software_work_and_changed_files() -> None:
    """Dropping the software evidence or file gate would coach unrelated work."""
    summary = unit(software_evidence=True, files_edited=2, test_run_attempts=0)

    opportunity = choose_opportunity(summary, outcome(summary, "yes"), ("software",))

    assert opportunity is not None
    assert opportunity.practice_id == "run_verification"
    assert opportunity.baseline_value == 0
    assert choose_opportunity(
        unit(files_edited=2), outcome(unit(files_edited=2), "yes"), ("software",)
    ) is None
    assert choose_opportunity(
        unit(software_evidence=True),
        outcome(unit(software_evidence=True), "yes"),
        ("software",),
    ) is None


@pytest.mark.parametrize(
    ("paths", "test_run_attempts"),
    [
        (("knowledge",), 0),
        (("software",), 1),
    ],
)
def test_software_verification_requires_selected_path_and_zero_test_attempts(
    paths: tuple[str, ...], test_run_attempts: int
) -> None:
    """Either a missing software selection or an observed test must close the rule."""
    summary = unit(
        software_evidence=True,
        files_edited=2,
        test_run_attempts=test_run_attempts,
    )

    assert choose_opportunity(summary, outcome(summary, "yes"), paths) is None


def test_knowledge_review_requires_partly_or_no_outcome() -> None:
    """Coaching a useful result would violate the closed outcome gate."""
    summary = unit(knowledge_evidence=True, doc_files_created=1, files_edited=0)

    assert choose_opportunity(summary, outcome(summary, "yes"), ("knowledge",)) is None
    partly = choose_opportunity(summary, outcome(summary, "partly"), ("knowledge",))
    no = choose_opportunity(summary, outcome(summary, "no"), ("knowledge",))

    assert partly is not None
    assert partly.practice_id == "review_deliverable"
    assert no is not None
    assert no.practice_id == "review_deliverable"


@pytest.mark.parametrize(
    (
        "paths",
        "knowledge_evidence",
        "doc_files_created",
        "export_writes",
        "files_edited",
        "expected_practice_id",
    ),
    [
        (("software",), True, 1, 0, 0, None),
        (("knowledge",), False, 1, 0, 0, None),
        (("knowledge",), True, 0, 0, 0, None),
        (("knowledge",), True, 1, 0, 1, None),
        (("knowledge",), True, 0, 1, 0, "review_deliverable"),
    ],
)
def test_knowledge_review_requires_every_path_evidence_artifact_and_edit_gate(
    paths: tuple[str, ...],
    knowledge_evidence: bool,
    doc_files_created: int,
    export_writes: int,
    files_edited: int,
    expected_practice_id: str | None,
) -> None:
    """Each knowledge predicate and the export alternative must independently matter."""
    summary = unit(
        knowledge_evidence=knowledge_evidence,
        doc_files_created=doc_files_created,
        export_writes=export_writes,
        files_edited=files_edited,
    )

    opportunity = choose_opportunity(summary, outcome(summary, "partly"), paths)

    assert (
        opportunity.practice_id if opportunity is not None else None
    ) == expected_practice_id


def test_opportunity_uses_closed_copy_internal_id_and_existing_codex_prefix() -> None:
    """Dynamic copy, exposed identity, or a different URL scheme would break the contract."""
    summary = unit(command_failures=2, retries=3)
    prompt = (
        "State the current blocker from the available evidence. Do not retry yet. "
        "Propose the smallest verifiable next step and explain what would show that it worked."
    )

    opportunity = choose_opportunity(summary, outcome(summary, "partly"), ("knowledge",))

    assert opportunity == CapabilityOpportunity(
        opportunity_id="diagnose_before_retry:0123456789ab",
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
        prompt=prompt,
        codex_url=CODEX_NEW_PREFIX + quote(prompt, safe=""),
        confidence="limited",
        baseline_value=3,
    )


def test_measure_rejects_unknown_practice_ids() -> None:
    """Silently defaulting an unknown measure could manufacture an audit result."""
    with pytest.raises(ValueError, match="unknown capability practice"):
        measure_for("made-up", unit())


def test_acceptance_recomputes_and_rejects_a_stale_or_forged_id(store: Store) -> None:
    """Caller-supplied identity must never choose the persisted baseline."""
    summary = unit(command_failures=2, retries=2)

    with pytest.raises(ValueError, match="current capability opportunity"):
        accept_practice(
            store,
            "made-up",
            (summary,),
            (outcome(summary, "partly"),),
            ("knowledge",),
            TODAY,
        )

    assert read_practice(store) is None


def test_acceptance_writes_only_the_recomputed_current_practice(store: Store) -> None:
    """Accepting an exact ID must persist the server-resolved unit, copy, path, and measure."""
    older = unit(
        unit_key="older-abcdefghijklmnopqr",
        last_ts=NOW - timedelta(hours=2),
        command_failures=2,
        retries=4,
    )
    latest = unit(
        unit_key="latest-abcdefghijklmnopq",
        last_ts=NOW - timedelta(hours=1),
        software_evidence=True,
        files_edited=2,
    )

    practice = accept_practice(
        store,
        "run_verification",
        (older, latest),
        (outcome(older, "partly", day=TODAY - timedelta(days=1)), outcome(latest, "yes")),
        ("knowledge", "software"),
        TODAY,
    )

    assert practice == PracticeEntry(
        practice_id="run_verification",
        title="Make verification part of the handoff",
        path="software",
        accepted_day=TODAY.isoformat(),
        baseline_unit_key=latest.unit_key,
        baseline_last_ts=latest.last_ts.isoformat(),
        baseline_value=0,
    )
    assert read_practice(store) == practice


@pytest.mark.parametrize(
    ("practice_id", "values", "expected", "line"),
    [
        (
            "diagnose_before_retry",
            (1, 0, 1),
            "improved",
            "The next three comparable pieces of work used fewer immediate retries.",
        ),
        (
            "diagnose_before_retry",
            (2, 2, 2),
            "unchanged",
            "The next three comparable pieces of work used the same number of immediate retries.",
        ),
        (
            "diagnose_before_retry",
            (3, 2, 4),
            "other_way",
            "The next three comparable pieces of work used more immediate retries.",
        ),
        (
            "run_verification",
            (100, 100, 100),
            "improved",
            "The next three comparable changes included verification more often.",
        ),
        (
            "review_deliverable",
            (0, 0, 0),
            "unchanged",
            "The next three comparable deliverables showed no movement in observed revision.",
        ),
    ],
)
def test_audit_uses_three_comparable_later_units(
    practice_id: str,
    values: tuple[int, int, int],
    expected: str,
    line: str,
) -> None:
    """Wrong direction or comparison math would report a false practice result."""
    practice = accepted(
        practice_id,
        baseline_value=2 if practice_id == "diagnose_before_retry" else 0,
    )
    later = tuple(comparable_unit(practice_id, value, index) for index, value in enumerate(values))

    result = audit_practice(practice, later)

    assert result is not None
    assert result.status == expected
    assert result.comparable_units == 3
    assert result.line == line


def test_audit_waits_for_three_units_after_the_baseline() -> None:
    """A partial comparison window must never be rendered as an outcome."""
    practice = accepted("run_verification", baseline_value=0)

    assert audit_practice(practice, (comparable_unit("run_verification", 100, 0),)) is None


def test_audit_excludes_wrong_path_and_units_at_or_before_baseline() -> None:
    """Incomparable or pre-baseline work must never satisfy the three-unit floor."""
    practice = accepted("run_verification", baseline_value=0)
    at_baseline = comparable_unit("run_verification", 100, 0, last_ts=NOW)
    before = comparable_unit(
        "run_verification", 100, 1, last_ts=NOW - timedelta(microseconds=1)
    )
    overlapping = comparable_unit(
        "run_verification", 100, 5, last_ts=NOW + timedelta(minutes=1)
    )
    wrong_path = unit(
        unit_key="knowledge-only",
        first_ts=NOW + timedelta(minutes=1),
        last_ts=NOW + timedelta(minutes=2),
        doc_files_created=1,
        knowledge_evidence=True,
    )
    two_later = (
        comparable_unit("run_verification", 100, 2),
        comparable_unit("run_verification", 100, 3),
    )

    assert audit_practice(
        practice,
        (at_baseline, before, overlapping, wrong_path, *two_later),
    ) is None

    result = audit_practice(
        practice,
        (
            at_baseline,
            before,
            overlapping,
            wrong_path,
            *two_later,
            comparable_unit("run_verification", 100, 4),
        ),
    )
    assert result is not None
    assert result.current_value == 100
    assert result.comparable_units == 3


def test_audit_sorts_by_time_then_unit_key_and_uses_only_the_first_three() -> None:
    """Caller order or a fourth unit must not change the fixed audit window."""
    practice = accepted("run_verification", baseline_value=0)
    same_time = NOW + timedelta(hours=1)
    units = (
        comparable_unit("run_verification", 100, 3, last_ts=NOW + timedelta(hours=2)),
        comparable_unit("run_verification", 100, 2, last_ts=same_time, unit_key="b"),
        comparable_unit("run_verification", 0, 1, last_ts=same_time, unit_key="a"),
        comparable_unit("run_verification", 0, 0, last_ts=NOW + timedelta(minutes=30)),
    )

    result = audit_practice(practice, units)

    assert result is not None
    assert result.current_value == 33


@pytest.mark.parametrize(
    "practice",
    [
        PracticeEntry(
            practice_id="made-up",
            title="Pause before retrying",
            path="shared",
            accepted_day=TODAY.isoformat(),
            baseline_unit_key="baseline-unit-key",
            baseline_last_ts=NOW.isoformat(),
            baseline_value=2,
        ),
        PracticeEntry(
            practice_id="diagnose_before_retry",
            title="Wrong title",
            path="shared",
            accepted_day=TODAY.isoformat(),
            baseline_unit_key="baseline-unit-key",
            baseline_last_ts=NOW.isoformat(),
            baseline_value=2,
        ),
        PracticeEntry(
            practice_id="diagnose_before_retry",
            title="Pause before retrying",
            path="software",
            accepted_day=TODAY.isoformat(),
            baseline_unit_key="baseline-unit-key",
            baseline_last_ts=NOW.isoformat(),
            baseline_value=2,
        ),
    ],
)
def test_audit_fails_closed_for_unknown_or_mismatched_practice_metadata(
    practice: PracticeEntry,
) -> None:
    """Forged persisted rule metadata must not crash or produce an audit claim."""
    later = tuple(comparable_unit("diagnose_before_retry", 1, index) for index in range(3))

    assert audit_practice(practice, later) is None


def test_composer_is_deterministic_for_injected_time(store: Store, prefs: Prefs) -> None:
    """Reading identical local state at an injected time must be pure and stable."""
    first = compose_capability_reading(store, prefs, TODAY, NOW)
    second = compose_capability_reading(store, prefs, TODAY, NOW)

    assert first == second
    assert first.paths == ("knowledge", "software")
    assert first.paths_confirmed is False
    assert first.pending_outcome is False
    assert first.opportunity is None
    assert first.practice_result is None
    assert first.practice_progress is None


def test_composer_returns_a_valid_real_store_opportunity(
    store: Store,
    prefs: Prefs,
) -> None:
    """Dropping the outcome-to-unit join would hide an eligible current practice."""
    summary = seed_closed_retry_unit(store)
    record_outcome(store, summary, "partly", TODAY)

    reading = compose_capability_reading(store, prefs, TODAY, NOW)

    assert reading.pending_outcome is False
    assert reading.opportunity is not None
    assert reading.opportunity.practice_id == "diagnose_before_retry"
    assert reading.practice_result is None
    assert reading.practice_progress is None


def test_composer_keeps_an_accepted_practice_visible_while_audit_is_pending(
    store: Store,
    prefs: Prefs,
) -> None:
    """An accepted practice must not turn into the unrelated quiet state."""
    summary = seed_closed_retry_unit(store)
    record_outcome(store, summary, "partly", TODAY)
    accepted_practice = accept_practice(
        store,
        "diagnose_before_retry",
        (summary,),
        read_outcomes(store),
        prefs.capability_paths,
        TODAY,
    )

    reading = compose_capability_reading(store, prefs, TODAY, NOW)

    assert accepted_practice.practice_id == "diagnose_before_retry"
    assert reading.opportunity is None
    assert reading.practice_result is None
    assert reading.practice_progress is not None
    assert reading.practice_progress.title == "Pause before retrying"
    assert reading.practice_progress.practice == (
        "Before another run, ask Codex to name the blocker and propose the "
        "smallest verifiable next step."
    )
    assert reading.practice_progress.codex_url == CODEX_NEW_PREFIX + quote(
        (
            "State the current blocker from the available evidence. Do not retry "
            "yet. Propose the smallest verifiable next step and explain what would "
            "show that it worked."
        ),
        safe="",
    )
    assert reading.practice_progress.comparable_units == 0
    assert reading.practice_progress.context == (
        "The local check will use the next three comparable pieces of work."
    )
    for text in (
        reading.practice_progress.title,
        reading.practice_progress.practice,
        reading.practice_progress.context,
    ):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_composer_audits_sparse_later_units_beyond_candidate_recency(
    store: Store,
    prefs: Prefs,
) -> None:
    """Reusing the two-day candidate horizon would lose the first sparse unit."""
    baseline_day = TODAY - timedelta(days=6)
    baseline = seed_closed_software_unit(
        store,
        baseline_day,
        "baseline",
        test_run_attempts=0,
    )
    write_practice(
        store,
        PracticeEntry(
            practice_id="run_verification",
            title="Make verification part of the handoff",
            path="software",
            accepted_day=baseline_day.isoformat(),
            baseline_unit_key=baseline.unit_key,
            baseline_last_ts=baseline.last_ts.isoformat(),
            baseline_value=0,
        ),
    )

    day_10 = TODAY - timedelta(days=4)
    seed_closed_software_unit(store, day_10, "day-10", test_run_attempts=1)
    reading_day_10 = compose_capability_reading(
        store,
        prefs,
        day_10,
        datetime.combine(day_10, datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=12),
    )
    assert reading_day_10.practice_progress is not None
    assert reading_day_10.practice_progress.comparable_units == 1
    assert reading_day_10.practice_progress.context == (
        "One of three comparable pieces of work is ready for the local check."
    )

    day_12 = TODAY - timedelta(days=2)
    seed_closed_software_unit(store, day_12, "day-12", test_run_attempts=1)
    reading_day_12 = compose_capability_reading(
        store,
        prefs,
        day_12,
        datetime.combine(day_12, datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=12),
    )
    assert reading_day_12.practice_progress is not None
    assert reading_day_12.practice_progress.comparable_units == 2
    assert reading_day_12.practice_progress.context == (
        "Two of three comparable pieces of work are ready for the local check."
    )

    seed_closed_software_unit(store, TODAY, "day-14", test_run_attempts=1)
    candidate_units = closed_capability_units(store, TODAY, NOW)
    assert [unit.last_ts.day for unit in candidate_units] == [12, 14]

    reading = compose_capability_reading(store, prefs, TODAY, NOW)

    assert reading.practice_result is not None
    assert reading.practice_result.status == "improved"
    assert reading.practice_result.comparable_units == 3
    assert reading.practice_result.current_value == 100
    assert reading.practice_progress is None
    for text in (
        reading_day_10.practice_progress.context,
        reading_day_12.practice_progress.context,
    ):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_composer_with_sparse_ineligible_history_returns_quiet_defaults(
    store: Store, prefs: Prefs
) -> None:
    """A real work span below the turn floor must not become evidence."""
    events = tuple(
        TurnEvent(
            timestamp=datetime(2026, 8, 14, 9, tzinfo=UTC)
            + timedelta(minutes=index * 5),
            session_id="sparse-session",
            source_id="codex",
            tool=Tool.CODEX,
            kind=TurnKind.ASSISTANT_TURN,
            model="gpt-test",
            tokens=TokenCounts(input=100),
            retries=0,
            commands_failed=0,
            git_commit_attempts=0,
            test_run_attempts=0,
            files_created=0,
            doc_files_created=0,
            export_writes=0,
            files_edited=0,
            cwd_hash="sparse-project",
            branch_hash="sparse-branch",
        )
        for index in range(4)
    )
    store.replace_file_data(
        source_id="codex",
        path_hash="sparse-capability-history",
        cursor=(1, 1, 1),
        contributions=[],
        marks=_marks_from_events(events),
        health=None,
        turn_keys=(),
        now=NOW,
        work_spans=_work_spans_from_events(events),
    )
    assert store.work_spans_between(TODAY.isoformat(), TODAY.isoformat())
    assert closed_capability_units(store, TODAY, NOW) == ()

    reading = compose_capability_reading(store, prefs, TODAY, NOW)

    assert reading.paths == ("knowledge", "software")
    assert reading.paths_confirmed is False
    assert reading.pending_outcome is False
    assert reading.opportunity is None
    assert reading.practice_result is None
    assert reading.practice_progress is None


@pytest.mark.parametrize(
    ("practice_id", "title", "path"),
    [
        ("made-up", "Pause before retrying", "shared"),
        ("diagnose_before_retry", "Wrong title", "shared"),
        ("diagnose_before_retry", "Pause before retrying", "software"),
    ],
)
def test_composer_fails_closed_for_semantically_invalid_active_practice(
    store: Store,
    prefs: Prefs,
    practice_id: str,
    title: str,
    path: str,
) -> None:
    """An invalid active record must suppress both a forged result and a new opportunity."""
    summary = seed_closed_retry_unit(store)
    record_outcome(store, summary, "partly", TODAY)
    invalid = PracticeEntry(
        practice_id=practice_id,
        title=title,
        path=path,
        accepted_day=TODAY.isoformat(),
        baseline_unit_key=summary.unit_key,
        baseline_last_ts=summary.last_ts.isoformat(),
        baseline_value=summary.retries,
    )
    store.meta_set(CAPABILITY_PRACTICE_KEY, json.dumps(asdict(invalid), sort_keys=True))
    before = store.meta_get(CAPABILITY_PRACTICE_KEY)

    reading = compose_capability_reading(store, prefs, TODAY, NOW)

    assert reading.opportunity is None
    assert reading.practice_result is None
    assert reading.practice_progress is None
    assert store.meta_get(CAPABILITY_PRACTICE_KEY) == before


def test_active_practice_suppresses_new_acceptance(store: Store) -> None:
    """A caller must not replace the one active practice through a hidden opportunity."""
    active = accepted("run_verification", baseline_value=0)
    write_practice(store, active)
    summary = unit(command_failures=2, retries=2)

    with pytest.raises(ValueError, match="current capability opportunity"):
        accept_practice(
            store,
            "diagnose_before_retry",
            (summary,),
            (outcome(summary, "partly"),),
            ("knowledge",),
            TODAY,
        )

    assert read_practice(store) == active


def test_audit_does_not_count_work_done_before_accepting_a_practice() -> None:
    practice = PracticeEntry(
        "run_verification", "Make verification part of the handoff", "software",
        TODAY.isoformat(), "baseline", (NOW - timedelta(hours=4)).isoformat(), 0,
        accepted_at=NOW.isoformat(),
    )
    earlier = tuple(unit(
        unit_key=f"before-{i}", first_ts=NOW - timedelta(hours=3, minutes=i),
        last_ts=NOW - timedelta(hours=2, minutes=i), test_run_attempts=1,
        software_evidence=True,
    ) for i in range(3))
    assert audit_practice(practice, earlier) is None
    later = tuple(unit(
        unit_key=f"after-{i}", first_ts=NOW + timedelta(hours=i+1),
        last_ts=NOW + timedelta(hours=i+1, minutes=30), test_run_attempts=1,
        software_evidence=True,
    ) for i in range(3))
    result = audit_practice(practice, (*earlier, *later))
    assert result is not None and result.comparable_units == 3
