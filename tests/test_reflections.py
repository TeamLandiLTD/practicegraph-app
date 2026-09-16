"""The reflection band: cohort math must clear its gates before a sentence
exists, every template stays inside the copy rules, and the composition is
deterministic (INV-6). No reflection ever reaches the wire — the band is a
view-model key only (covered by the wire suite's payload scans)."""

from __future__ import annotations

import json

from practicegraph.analysis.focus import RHYTHM_WINDOW_DAYS, RhythmStats, rhythm_stats
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.reflections import (
    EFFECT_MIN_TOOL_CALLS,
    MAX_REFLECTIONS,
    REFLECTION_COPY,
    WAVED_Q_MIN_MOMENTS,
    compose_reflections,
)
from practicegraph.store import MarkRow


def _mark(
    session: str,
    ts: str,
    kind: str = "assistant_turn",
    tool_calls: int = 0,
    failures: int = 0,
    short_reply: int = 0,
) -> MarkRow:
    return (session, ts, kind, tool_calls, 0, 0, failures, 0, 1, "", "", short_reply)


def _day(day: str, hour: int, marks_spec: list[tuple[int, int]]) -> list[MarkRow]:
    """A day's marks at `hour`: (tool_calls, failures) per assistant turn,
    one minute apart."""
    return [
        _mark("s1", f"{day}T{hour:02d}:{minute:02d}:00+00:00", "assistant_turn", tools, fails)
        for minute, (tools, fails) in enumerate(marks_spec)
    ]


def _rhythm(marks_by_day: dict[str, list[MarkRow]]) -> RhythmStats:
    return rhythm_stats(marks_by_day)


def test_reflection_copy_is_lexicon_and_leak_clean() -> None:
    """The whole closed catalog — raw templates, not just rendered picks —
    stays inside the observational-copy rules (FR-FOC-8)."""
    for template in REFLECTION_COPY.values():
        assert lexicon_violations(template) == []
        assert leak_findings(template) == []


def test_no_data_means_no_sentences() -> None:
    assert compose_reflections({}, None, 0, 0) == []
    marks = {"2026-07-01": [_mark("s1", "2026-07-01T10:00:00+00:00")]}
    lines = compose_reflections(marks, _rhythm(marks), 1, 1)
    assert lines == []  # one quiet day clears no gate


def test_composition_is_deterministic_and_closed() -> None:
    marks_by_day: dict[str, list[MarkRow]] = {}
    for index in range(14):
        day = f"2026-06-{index + 10:02d}"
        marks_by_day[day] = _day(day, 23, [(20, 4)] * 20) + _day(day, 10, [(20, 1)] * 20)
    rhythm = _rhythm(marks_by_day)
    first = compose_reflections(marks_by_day, rhythm, 4000, 2000)
    second = compose_reflections(marks_by_day, rhythm, 4000, 2000)
    assert json.dumps(first) == json.dumps(second)
    assert 0 < len(first) <= MAX_REFLECTIONS
    for line in first:
        assert set(line.keys()) == {"id", "tone", "text"}
        assert line["id"] in REFLECTION_COPY
        assert line["tone"] in {"watch", "question", "steady"}
        assert lexicon_violations(line["text"]) == []


def test_late_effect_needs_material_gap_and_sample() -> None:
    """The effect clause exists only when both cohorts clear the tool-call
    gate AND late work fails materially more; otherwise the pattern line
    (or nothing) renders — no gate, no claim."""
    worse: dict[str, list[MarkRow]] = {}
    balanced: dict[str, list[MarkRow]] = {}
    for index in range(14):
        day = f"2026-06-{index + 10:02d}"
        worse[day] = _day(day, 23, [(20, 4)] * 20) + _day(day, 10, [(20, 1)] * 20)
        balanced[day] = _day(day, 23, [(20, 1)] * 20) + _day(day, 10, [(20, 1)] * 20)
    assert sum(mark[3] for marks in worse.values() for mark in marks) >= 2 * EFFECT_MIN_TOOL_CALLS
    worse_ids = {line["id"] for line in compose_reflections(worse, _rhythm(worse), 100, 100)}
    balanced_ids = {
        line["id"] for line in compose_reflections(balanced, _rhythm(balanced), 100, 100)
    }
    assert "late-effect" in worse_ids
    assert "late-effect" not in balanced_ids
    assert "late-pattern" in balanced_ids  # half the work is at night


def test_waved_question_needs_moments_waves_and_share() -> None:
    """The review question renders only when waving is a pattern: enough
    moments, enough waves, and a quarter of the handoffs waved through."""
    stretch = [(2, 0)] * 9 + [(0, 0)]  # 9 tooled turns then the untooled ask

    def approval_day(day: str, waved: bool) -> list[MarkRow]:
        marks = _day(day, 10, stretch)
        gap = "10:10:30" if waved else "10:20:00"
        marks.append(_mark("s1", f"{day}T{gap}+00:00", "user_turn", 0, 0, 1))
        return marks

    waving: dict[str, list[MarkRow]] = {}
    reading: dict[str, list[MarkRow]] = {}
    for index in range(WAVED_Q_MIN_MOMENTS + 2):
        day = f"2026-06-{index + 10:02d}"
        waving[day] = approval_day(day, waved=index % 2 == 0)
        reading[day] = approval_day(day, waved=False)
    waving_ids = {line["id"] for line in compose_reflections(waving, _rhythm(waving), 10, 10)}
    reading_ids = {line["id"] for line in compose_reflections(reading, _rhythm(reading), 10, 10)}
    assert "waved-question" in waving_ids
    assert "waved-question" not in reading_ids
    assert "waved-steady" in reading_ids


def test_rhythm_window_is_the_one_the_band_names() -> None:
    """Copy says "the last 28 days" / "this month" — pin the window those
    words describe so a silent widening becomes a reviewed diff."""
    assert RHYTHM_WINDOW_DAYS == 28
