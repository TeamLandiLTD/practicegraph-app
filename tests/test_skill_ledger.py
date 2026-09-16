"""The closed half of the skill loop: advice that audits itself.

The shelf could always say a skill was *taken*. Nothing measured what changed,
which meant the recommendation was a claim the product never checked. These
tests pin the loop, and — the load-bearing part — pin that it reports movement
the WRONG way on the same schedule as movement the right way. A loop that only
reports its wins is not a measurement.
"""

from __future__ import annotations

from datetime import date, timedelta

from practicegraph.analysis.advisor_receipts import (
    FamilyEconomics,
    ReceiptsWindow,
)
from practicegraph.analysis.skill_ledger import (
    AUDIT_COPY,
    METRIC_FOR_FINDING,
    NOTE_COPY,
    SKILL_AUDIT_HOLD_DAYS,
    SKILL_AUDIT_MIN_DAYS,
    SKILL_AUDIT_RETIRE_DAYS,
    SKILL_LEDGER_KEY,
    measure,
    metric_for_skill,
    record_skill_target,
    skill_audit,
)
from practicegraph.events import TokenCounts
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store

TODAY = date(2026, 7, 25)


def _family(
    *, turns: int, input_tokens: int, cached: int, cost: int, premium: bool
) -> FamilyEconomics:
    return FamilyEconomics(
        tool="claude_code",
        family="claude_opus" if premium else "claude_haiku",
        premium=premium,
        confident=True,
        active_days=20,
        assistant_turns=turns,
        priced_turns=turns,
        unpriced_turns=0,
        cost_micro_usd=cost,
        tokens=TokenCounts(
            input=input_tokens, output=0, cached=cached, cache_creation=0
        ),
        tool_calls=0,
        retries=0,
        interruptions=0,
        rework_edits=0,
        commands_run=0,
        commands_failed=0,
        commands_slow=0,
        compactions=0,
        prompt_tokens_per_turn=0,
        output_tokens_per_turn=0,
        cost_per_priced_turn_micro_usd=0,
        cache_hit_pct=0,
        cost_share_pct=0,
        retries_per_100_turns_tenths=0,
        interruptions_per_100_turns_tenths=0,
        rework_per_100_tool_calls_tenths=0,
        command_fail_pct=0,
    )


def _receipts(*, prompt_per_turn: int, cached_share: int = 0, premium_share: int = 0):
    """A window whose derived measures land on the requested values."""
    turns = 100
    prompt = prompt_per_turn * turns
    cached = prompt * cached_share // 100
    return ReceiptsWindow(
        end_day=TODAY,
        window_days=28,
        total_cost_micro_usd=1_000_000,
        total_assistant_turns=turns,
        total_unpriced_turns=0,
        families=(
            _family(
                turns=turns,
                input_tokens=prompt - cached,
                cached=cached,
                cost=premium_share * 10_000,
                premium=True,
            ),
            _family(
                turns=0,
                input_tokens=0,
                cached=0,
                cost=(100 - premium_share) * 10_000,
                premium=False,
            ),
        ),
    )


def _store(tmp_path) -> Store:
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    return store


def test_the_measures_read_off_the_window_exactly(tmp_path) -> None:
    window = _receipts(prompt_per_turn=40_000, cached_share=75, premium_share=60)
    assert measure("prompt_weight", window) == 40_000
    assert measure("cache_share", window) == 75
    assert measure("premium_share", window) == 60
    # An unknown metric reads 0 rather than raising — the ledger is best-effort
    # on the write path and must never take the copy action down with it.
    assert measure("not_a_metric", window) == 0


def test_a_skill_answering_no_measurable_finding_records_nothing(tmp_path) -> None:
    """Withheld, not invented. Pairing a skill with a number that does not test
    it would make the audit line meaningless in exactly the way the audit exists
    to prevent."""
    store = _store(tmp_path)
    assert metric_for_skill(("command_friction",)) == ""
    assert record_skill_target(
        store, "s1", "A skill", ("command_friction",),
        _receipts(prompt_per_turn=40_000), TODAY,
    ) is False
    assert store.meta_get(SKILL_LEDGER_KEY) in ("", None)


def test_the_metric_choice_never_depends_on_registry_order(tmp_path) -> None:
    assert metric_for_skill(("premium_heavy", "context_bloat")) == metric_for_skill(
        ("context_bloat", "premium_heavy")
    )
    assert set(METRIC_FOR_FINDING.values()) == {
        "prompt_weight", "cache_share", "premium_share"
    }


def test_the_loop_reports_movement_toward_the_target(tmp_path) -> None:
    store = _store(tmp_path)
    assert record_skill_target(
        store, "read-once", "Read what you need once", ("context_bloat",),
        _receipts(prompt_per_turn=40_000), TODAY,
    ) is True
    later = TODAY + timedelta(days=SKILL_AUDIT_MIN_DAYS)
    outcome = skill_audit(store, _receipts(prompt_per_turn=24_000), later)
    assert outcome is not None
    assert outcome.skill_id == "read-once"
    assert outcome.then == 40_000 and outcome.now == 24_000
    assert "40K" in outcome.line and "24K" in outcome.line
    assert "40.0K" not in outcome.line
    assert "Read what you need once" in outcome.line
    # Association, never causation — the product may not claim the skill did it.
    assert "not proof the skill caused it" in outcome.note


def test_the_loop_reports_movement_the_other_way_just_as_readily(tmp_path) -> None:
    """The test that makes this a measurement rather than marketing. Ferraro &
    Price found generic technical advice not statistically significant on its
    own; the only thing separating this from that arm is that it checks — and
    checking means being willing to publish the miss."""
    store = _store(tmp_path)
    record_skill_target(
        store, "read-once", "Read what you need once", ("context_bloat",),
        _receipts(prompt_per_turn=40_000), TODAY,
    )
    later = TODAY + timedelta(days=SKILL_AUDIT_MIN_DAYS)
    outcome = skill_audit(store, _receipts(prompt_per_turn=60_000), later)
    assert outcome is not None
    assert "the other way" in outcome.line
    # The caveat travels beside the line, for the ⓘ, not inside it.
    assert "not a verdict" in outcome.note
    assert "verdict" not in outcome.line and "→" not in outcome.line
    # Same schedule as the win: no extra patience is demanded of bad news.
    assert SKILL_AUDIT_MIN_DAYS < SKILL_AUDIT_HOLD_DAYS


def test_a_measure_where_higher_is_the_target_reads_the_other_direction(
    tmp_path,
) -> None:
    """Cache share is the inverted one — a rise is movement toward the target."""
    store = _store(tmp_path)
    record_skill_target(
        store, "cache-warm", "Warm the cache", ("low_cache",),
        _receipts(prompt_per_turn=40_000, cached_share=40), TODAY,
    )
    later = TODAY + timedelta(days=SKILL_AUDIT_MIN_DAYS)
    up = skill_audit(store, _receipts(prompt_per_turn=40_000, cached_share=80), later)
    assert up is not None and "the other way" not in up.line
    down = skill_audit(store, _receipts(prompt_per_turn=40_000, cached_share=20), later)
    assert down is not None and "the other way" in down.line


def test_patience_first_then_honesty(tmp_path) -> None:
    """Too young to mean anything, unmoved during the hold, reported after it,
    and retired once the window has fully passed."""
    store = _store(tmp_path)
    record_skill_target(
        store, "read-once", "Read what you need once", ("context_bloat",),
        _receipts(prompt_per_turn=40_000), TODAY,
    )
    flat = _receipts(prompt_per_turn=40_000)

    assert skill_audit(store, flat, TODAY + timedelta(days=1)) is None
    assert skill_audit(
        store, flat, TODAY + timedelta(days=SKILL_AUDIT_MIN_DAYS)
    ) is None
    held = skill_audit(store, flat, TODAY + timedelta(days=SKILL_AUDIT_HOLD_DAYS))
    assert held is not None and "about where" in held.line
    assert skill_audit(
        store, flat, TODAY + timedelta(days=SKILL_AUDIT_RETIRE_DAYS + 1)
    ) is None


def test_one_claim_at_a_time(tmp_path) -> None:
    """A second copy inside the audit window does not displace the first. Two
    half-measured claims are worth less than one carried to a conclusion."""
    store = _store(tmp_path)
    record_skill_target(
        store, "read-once", "Read what you need once", ("context_bloat",),
        _receipts(prompt_per_turn=40_000), TODAY,
    )
    assert record_skill_target(
        store, "cache-warm", "Warm the cache", ("low_cache",),
        _receipts(prompt_per_turn=40_000, cached_share=40),
        TODAY + timedelta(days=3),
    ) is False
    outcome = skill_audit(
        store, _receipts(prompt_per_turn=24_000),
        TODAY + timedelta(days=SKILL_AUDIT_MIN_DAYS),
    )
    assert outcome is not None and outcome.skill_id == "read-once"
    # Once the first entry has fully retired, the next copy takes the slot.
    assert record_skill_target(
        store, "cache-warm", "Warm the cache", ("low_cache",),
        _receipts(prompt_per_turn=40_000, cached_share=40),
        TODAY + timedelta(days=SKILL_AUDIT_RETIRE_DAYS + 1),
    ) is True


def test_a_corrupt_or_foreign_ledger_value_is_ignored(tmp_path) -> None:
    """Fail-open (NFR-REL-1): a meta blob written by an older or broken build
    yields no audit line rather than an exception on the render path."""
    store = _store(tmp_path)
    for raw in ('{', '[]', '{"skill_id": 1}', '{"skill_id": "a", "title": "b",'
                ' "day": "not-a-day", "metric_id": "prompt_weight", "value": 1}',
                '{"skill_id": "a", "title": "b", "day": "2026-07-01",'
                ' "metric_id": "invented", "value": 1}'):
        store.meta_set(SKILL_LEDGER_KEY, raw)
        assert skill_audit(store, _receipts(prompt_per_turn=1), TODAY) is None


def test_copy_stays_observational(tmp_path) -> None:
    for text in (*AUDIT_COPY.values(), *NOTE_COPY.values()):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text


def test_the_line_rounds_its_numbers(tmp_path) -> None:
    """COPY_RULES rule 5: "about 230K", never "232.1K"."""
    store = _store(tmp_path)
    record_skill_target(
        store, "read-once", "Read what you need once", ("context_bloat",),
        _receipts(prompt_per_turn=232_100), TODAY,
    )
    outcome = skill_audit(
        store, _receipts(prompt_per_turn=194_500),
        TODAY + timedelta(days=SKILL_AUDIT_MIN_DAYS),
    )
    assert outcome is not None
    assert "went from 230K to 190K" in outcome.line
    assert "232.1K" not in outcome.line
