"""The strain reading: verification-load components and the felt-drain probe.

STRAIN_READING_PLAN S1/S2. Components, never a score; the probe asked before
the components show; every sentence on the page through the lexicon gate.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path

from conftest import (
    FIXTURE_DAY,
    FIXTURE_GENERATED_AT,
    build_fixture_extras,
    build_fixture_history,
    build_fixture_snapshot,
)
from practicegraph import __version__
from practicegraph.analysis.drain import (
    DRAIN_BRACKETS,
    DRAIN_COPY,
    compose_drain,
    probe_pending,
    record_drain,
)
from practicegraph.analysis.ratecard import RATE_CARD_VERSION
from practicegraph.analysis.verification import (
    VERIFICATION_COMPONENTS,
    VERIFICATION_COPY,
    VERIFICATION_MIN_SESSIONS,
    components_from_marks,
    compose_verification,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.viewmodel import view_model
from practicegraph.store import Store

NOW = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)


def _mark(session: str, ts: str, kind: str = "assistant_turn", *,
          tool_calls: int = 1, retries: int = 0, interruptions: int = 0,
          failures: int = 0, cwd: str = "c1") -> tuple:
    # DayMarkRow: day, session_key, ts_utc, kind, tool_calls, retries,
    # interruptions, command_failures, compactions, interactive, cwd_hash,
    # branch_hash, short_reply, human_initiated
    return ("2026-08-20", session, ts, kind, tool_calls, retries,
            interruptions, failures, 0, 1, cwd, "b1", 0, 1)


def test_components_read_failures_iterations_pauses_and_switches() -> None:
    marks = [
        # Session A: a failure, two more turns, then a clean tool run — 2
        # iterations to pass; one 6-minute pause; one folder switch.
        _mark("A", "2026-08-20T09:00:00+00:00", failures=1),
        _mark("A", "2026-08-20T09:01:00+00:00", tool_calls=0),
        _mark("A", "2026-08-20T09:02:00+00:00", tool_calls=0, retries=1),
        _mark("A", "2026-08-20T09:03:00+00:00", tool_calls=1, failures=0),
        _mark("A", "2026-08-20T09:09:30+00:00", cwd="c2"),
        # Session B: one interruption, never fails.
        _mark("B", "2026-08-20T10:00:00+00:00", interruptions=1),
        _mark("B", "2026-08-20T10:01:00+00:00"),
    ]
    got = components_from_marks(marks)
    assert got["sessions"] == 2
    assert got["failures"] == 1
    assert got["retries"] == 1
    assert got["iterations_to_pass"] == 3  # the three assistant turns after
    assert got["pauses"] == 1
    assert got["switches"] == 2  # one interruption + one folder change


def test_the_reading_is_withheld_under_the_session_floor(tmp_path: Path) -> None:
    store = Store(tmp_path / "state.db")
    store.migrate()
    reading = compose_verification(store, date(2026, 8, 23))
    assert reading.available is False
    assert reading.components == ()
    assert str(VERIFICATION_MIN_SESSIONS) in reading.note


def test_every_component_and_sentence_passes_the_gates() -> None:
    for _, label, unit, doc in VERIFICATION_COMPONENTS:
        assert lexicon_violations(f"{label} {unit} {doc}") == []
        assert leak_findings(doc) == []
    for text in VERIFICATION_COPY.values():
        assert lexicon_violations(text) == [], text
        assert leak_findings(text) == [], text
    for text in DRAIN_COPY.values():
        assert lexicon_violations(text) == [], text
    for _, label in DRAIN_BRACKETS:
        assert lexicon_violations(label) == []
    # The reading never names a state about the person.
    joined = " ".join(VERIFICATION_COPY.values()).lower()
    for banned in ("you are fatigued", "you are stressed", "burned out"):
        assert banned not in joined


def test_the_drain_probe_asks_once_a_day_only_on_active_days(tmp_path: Path) -> None:
    empty = Store(tmp_path / "empty.db")
    empty.migrate()
    assert probe_pending(empty, FIXTURE_DAY, coaching_enabled=True) is False

    store, _ = build_fixture_history()
    assert probe_pending(store, FIXTURE_DAY, coaching_enabled=False) is False
    assert probe_pending(store, FIXTURE_DAY, coaching_enabled=True) is True
    # Unknown brackets are refused; a real answer lands once; a second
    # answer for the same day is refused, never overwritten; a skip also
    # closes the day.
    assert record_drain(store, FIXTURE_DAY, "exhausted", NOW) is False
    assert record_drain(store, FIXTURE_DAY, "worn", NOW) is True
    assert record_drain(store, FIXTURE_DAY, "fresh", NOW) is False
    assert probe_pending(store, FIXTURE_DAY, coaching_enabled=True) is False
    surface = compose_drain(store, FIXTURE_DAY, coaching_enabled=True)
    assert surface.pending is False
    assert surface.answered_today == "worn"
    assert surface.answered_count == 1


def test_activity_details_do_not_require_answering_the_optional_reflection() -> None:
    """A pending personal reflection does not gate ordinary activity diagnostics."""
    from practicegraph.analysis.verification import VerificationComponent

    extras = build_fixture_extras()
    assert extras.drain is not None and extras.verification is not None
    extras = dataclasses.replace(extras, verification=dataclasses.replace(
        extras.verification, available=True,
        components=(VerificationComponent("failures", "Failures", "runs", "Recorded", 12, 2),),
    ))
    pending = dataclasses.replace(
        extras, drain=dataclasses.replace(extras.drain, pending=True)
    )
    model = view_model(
        build_fixture_snapshot(), pending, FIXTURE_GENERATED_AT,
        __version__, RATE_CARD_VERSION,
    )
    assert model["drain"]["pending"] is True
    assert model["verification"]["withheld_for_probe"] is False
    before = model["verification"]["components"]
    assert len(before) == 1 and before[0]["value"] == 12
    answered = dataclasses.replace(
        extras, drain=dataclasses.replace(extras.drain, pending=False)
    )
    model = view_model(
        build_fixture_snapshot(), answered, FIXTURE_GENERATED_AT,
        __version__, RATE_CARD_VERSION,
    )
    assert model["verification"]["withheld_for_probe"] is False
    assert model["verification"]["components"] == before
    assert model["verification"]["available"] is True
