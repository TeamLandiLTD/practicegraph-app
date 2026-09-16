"""The conditioning readout: composite fitness-level readings computed
indirectly from the behavioral record. These tests pin the safety contract
(closed copy stays observational and lexicon-clean), determinism (INV-6),
and the suppression rule — a reading whose gate is not met is dropped from
the list, never rendered as zero."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from practicegraph.analysis.conditioning import (
    CAPACITY_TOP_DAYS,
    COMPOSURE_MIN_MOMENTS,
    CONDITIONING_COPY,
    INDICATOR_LABEL,
    INDICATOR_MEASURES,
    INDICATOR_WHY,
    INDICATORS,
    LOAD_MIN_ACTIVE_DAYS,
    LOAD_STEADY_MAX_PCT,
    LOAD_STEADY_MIN_PCT,
    compose_conditioning,
)
from practicegraph.privacy import leak_findings, lexicon_violations

END = date(2026, 7, 16)
BASE = datetime(2026, 6, 19, 9, 0, tzinfo=UTC)  # first day of the 28-d window


def _mark(session: str, ts: datetime, kind: str = "assistant_turn"):
    return (session, ts.isoformat(), kind, 0, 0, 0, 0, 0)


def _block_day(day_index: int, block_min: int) -> tuple[str, list]:
    """One day whose longest single-session block is `block_min` minutes:
    marks every 10 minutes so no >30-min internal gap splits the block."""
    start = BASE + timedelta(days=day_index)
    marks = [
        _mark("s1", start + timedelta(minutes=m))
        for m in range(0, block_min + 1, 10)
    ]
    return (start.date().isoformat(), marks)


def _reflex_day(day_index: int, pairs: int, gap_s: int) -> tuple[str, list]:
    """One day of answer->follow-up pairs, each `gap_s` seconds apart. An
    untooled answer plus a fast reply is a reflex follow-up; a slow reply is
    a deliberate one."""
    start = BASE + timedelta(days=day_index)
    marks = []
    for index in range(pairs):
        answer = start + timedelta(minutes=index * 20)
        marks.append(_mark("s1", answer))
        marks.append(_mark("s1", answer + timedelta(seconds=gap_s), "user_turn"))
    return (start.date().isoformat(), marks)


def test_all_conditioning_copy_is_observational_and_clean() -> None:
    for text in (
        list(CONDITIONING_COPY.values())
        + list(INDICATOR_MEASURES.values())
        + list(INDICATOR_WHY.values())
        + list(INDICATOR_LABEL.values())
    ):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_empty_windows_compose_to_nothing() -> None:
    """Unavailable is never zero: with no marks at all, the readout is an
    empty list — no indicator renders a fabricated 0."""
    assert compose_conditioning({}, {}, END) == []


def test_capacity_needs_enough_block_days_then_reads_the_median() -> None:
    thin = dict(_block_day(i, 60 + i) for i in range(CAPACITY_TOP_DAYS - 1))
    assert all(
        ind.indicator_id != "capacity"
        for ind in compose_conditioning(thin, {}, END)
    )
    # Seven days, longest blocks 30..150 by 20: top-5 = 150,130,110,90,70.
    days = dict(_block_day(i, 30 + i * 20) for i in range(7))
    capacity = next(
        ind
        for ind in compose_conditioning(days, {}, END)
        if ind.indicator_id == "capacity"
    )
    assert capacity.value == 110  # median of the top five, not the best day
    assert capacity.unit == "min"
    assert capacity.delta is None  # no prior window: the first reading
    assert "First reading" in capacity.reading
    assert capacity.band_lo <= capacity.band_hi <= capacity.axis_max


def test_capacity_delta_reads_against_the_prior_window() -> None:
    current = dict(_block_day(i, 120) for i in range(6))
    prior_start = BASE - timedelta(days=28)
    prior = {}
    for i in range(6):
        start = prior_start + timedelta(days=i)
        prior[start.date().isoformat()] = [
            _mark("s1", start + timedelta(minutes=m)) for m in range(0, 61, 10)
        ]
    capacity = next(
        ind
        for ind in compose_conditioning(current, prior, END)
        if ind.indicator_id == "capacity"
    )
    assert capacity.prior == 60
    assert capacity.delta == 60
    assert capacity.tone == "steady"
    assert "up 60" in capacity.reading


def test_load_balance_bands_and_sparse_window_suppression() -> None:
    # 27 quiet days + a heavy final week -> acute far above chronic.
    days = {}
    for i in range(28):
        block = 200 if i >= 21 else 15
        day, marks = _block_day(i, block)
        days[day] = marks
    load = next(
        ind
        for ind in compose_conditioning(days, {}, END)
        if ind.indicator_id == "load_balance"
    )
    assert load.value > LOAD_STEADY_MAX_PCT
    assert load.tone == "watch"
    flat = dict(_block_day(i, 60) for i in range(28))
    steady = next(
        ind
        for ind in compose_conditioning(flat, {}, END)
        if ind.indicator_id == "load_balance"
    )
    assert LOAD_STEADY_MIN_PCT <= steady.value <= LOAD_STEADY_MAX_PCT
    assert steady.tone == "steady"
    sparse = dict(_block_day(i * 5, 60) for i in range(LOAD_MIN_ACTIVE_DAYS - 2))
    assert all(
        ind.indicator_id != "load_balance"
        for ind in compose_conditioning(sparse, {}, END)
    )


def test_composure_gates_on_moments_and_scores_deliberate_share() -> None:
    thin = dict(
        [_reflex_day(0, COMPOSURE_MIN_MOMENTS - 1, gap_s=300)]
    )
    assert all(
        ind.indicator_id != "composure"
        for ind in compose_conditioning(thin, {}, END)
    )
    # 40 deliberate (5-minute reads) + 20 reflex (5-second replies) = 66%.
    days = dict(
        [_reflex_day(0, 40, gap_s=300), _reflex_day(1, 20, gap_s=5)]
    )
    composure = next(
        ind
        for ind in compose_conditioning(days, {}, END)
        if ind.indicator_id == "composure"
    )
    assert composure.value == 100 - (20 * 100 // 60)
    assert composure.tone == "watch"  # at/below the watch line
    assert "60" in composure.reading  # the moments denominator is shown


def test_readout_is_deterministic_and_ordered() -> None:
    days = dict(_block_day(i, 60 + i * 10) for i in range(10))
    first = compose_conditioning(days, {}, END)
    second = compose_conditioning(days, {}, END)
    assert first == second
    order = [ind.indicator_id for ind in first]
    assert order == [i for i in INDICATORS if i in order]
    for ind in first:
        assert lexicon_violations(ind.reading) == []
        assert leak_findings(ind.reading) == []
