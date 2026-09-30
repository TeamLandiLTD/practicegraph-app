"""The Advisor closed loop: tick-only ledger
writes, pure audit reads, honest lines in both directions, retirement."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from practicegraph.analysis.advisor import (
    ADVISOR_LEDGER_KEY,
    AUDIT_COPY,
    AUDIT_HOLD_DAYS,
    AUDIT_MIN_DAYS,
    AUDIT_RETIRE_DAYS,
    AdvisorBoard,
    AdvisorTake,
    advisor_audit,
    build_advisor_board,
    premium_share_pct,
    record_advisor_ledger,
)
from practicegraph.analysis.advisor_receipts import build_receipts
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import ContributionRow, Store

DAY0 = date(2026, 7, 3)


def _values(**overrides: int) -> list[int]:
    base = dict.fromkeys(ContributionRow._fields, 0)
    base.update(overrides)
    return [base[field] for field in ContributionRow._fields]


def _receipts(premium_cost: int, budget_cost: int, end: date = DAY0) -> object:
    rows = [
        (end.isoformat(), "claude_code", "claude-fable-5",
         _values(assistant_turns=50, cost_micro_usd=premium_cost)),
        (end.isoformat(), "claude_code", "claude-haiku-4-5",
         _values(assistant_turns=50, cost_micro_usd=budget_cost)),
    ]
    return build_receipts(rows, end)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _routing_board(receipts: object) -> AdvisorBoard:
    """A board carrying the routing call, built directly — the ledger only
    inspects take ids, so market plumbing stays out of these tests."""
    del receipts
    take = AdvisorTake(
        take_id="premium-routing", tier="use", tool="claude_code",
        verdict="v", boundary="b", receipts=(), market="", steelman="",
        action="a", experiment="", attribution="", expires="",
        impact_micro_usd=1, confidence="medium", confidence_note="note",
    )
    return AdvisorBoard(
        as_of=DAY0.isoformat(), market_state="absent", market_as_of="",
        takes=(take,),
    )


def test_record_writes_once_and_audit_stays_quiet_early(tmp_path: Path) -> None:
    store = _store(tmp_path)
    receipts = _receipts(premium_cost=90_000_000, budget_cost=10_000_000)
    record_advisor_ledger(store, _routing_board(receipts), receipts, DAY0)
    raw = store.meta_get(ADVISOR_LEDGER_KEY)
    assert raw and '"share_pct": 90' in raw

    # A second surfacing does not overwrite the tracked baseline.
    later = _receipts(premium_cost=80_000_000, budget_cost=20_000_000)
    record_advisor_ledger(store, _routing_board(later), later, DAY0 + timedelta(days=3))
    assert store.meta_get(ADVISOR_LEDGER_KEY) == raw

    # Too young to audit.
    assert advisor_audit(store, later, DAY0 + timedelta(days=AUDIT_MIN_DAYS - 1)) == ""


def test_audit_reports_improvement_and_unmoved_honestly(tmp_path: Path) -> None:
    store = _store(tmp_path)
    baseline = _receipts(premium_cost=90_000_000, budget_cost=10_000_000)
    record_advisor_ledger(store, _routing_board(baseline), baseline, DAY0)

    improved = _receipts(premium_cost=60_000_000, budget_cost=40_000_000)
    line = advisor_audit(store, improved, DAY0 + timedelta(days=AUDIT_MIN_DAYS))
    # Words, not glyphs (COPY_RULES rule 2); the caveat no longer rides the line.
    assert "from 90% to 60%" in line
    assert "→" not in line and "not promised" not in line
    # AM-4: the moved slice in the person's own money — 30 dropped share
    # points over this window's $100.00 total = $30.00, stated as a
    # reallocation fact, never a counterfactual saving.
    assert "about $30.00 of this window's spend" in line

    flat = _receipts(premium_cost=89_000_000, budget_cost=11_000_000)
    assert advisor_audit(store, flat, DAY0 + timedelta(days=AUDIT_MIN_DAYS)) == ""
    unmoved = advisor_audit(store, flat, DAY0 + timedelta(days=AUDIT_HOLD_DAYS))
    assert "from 90% to 89%" in unmoved
    assert "has not come down" in unmoved


def test_ledger_retires_after_the_audit_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    baseline = _receipts(premium_cost=90_000_000, budget_cost=10_000_000)
    record_advisor_ledger(store, _routing_board(baseline), baseline, DAY0)

    stale_day = DAY0 + timedelta(days=AUDIT_RETIRE_DAYS + 1)
    assert advisor_audit(store, baseline, stale_day) == ""
    # The next tick clears the expired entry and immediately tracks anew when
    # the routing call is still on the board.
    record_advisor_ledger(store, _routing_board(baseline), baseline, stale_day)
    raw = store.meta_get(ADVISOR_LEDGER_KEY)
    assert raw and stale_day.isoformat() in raw


def test_no_entry_without_a_routing_take(tmp_path: Path) -> None:
    store = _store(tmp_path)
    receipts = _receipts(premium_cost=10_000_000, budget_cost=90_000_000)
    board = build_advisor_board(receipts, None, DAY0)  # calm default only
    record_advisor_ledger(store, board, receipts, DAY0)
    assert not store.meta_get(ADVISOR_LEDGER_KEY)


def test_premium_share_and_audit_copy_scans() -> None:
    receipts = _receipts(premium_cost=75_000_000, budget_cost=25_000_000)
    assert premium_share_pct(receipts) == 75
    for key, template in sorted(AUDIT_COPY.items()):
        text = template.format(date="2026-07-03", then=90, now=60, moved="$30.00")
        assert lexicon_violations(text) == [], key
        assert leak_findings(text) == [], key
