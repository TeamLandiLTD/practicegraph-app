"""Session grain (W2.0): the receipt and the context tax.

These tests pin that session totals are exact integer arithmetic that AGREES
with the day x tool x model contributions to the micro-USD (the two paths price
the same events), that a session merges correctly across the files and days it
spans, that the receipt and the cohort reading are withheld until their gates
pass, that the two-sided register reports an absorbed context honestly instead
of manufacturing an alarm, that composition is deterministic (INV-6), and —
load-bearing — that session IDENTITY never reaches a rendered surface.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from practicegraph.analysis.sessions import (
    COHORT_MIN_SESSIONS,
    CONTEXT_MULTIPLE_MIN_TENTHS,
    LONG_SESSION_MIN_TURNS,
    RECEIPT_MIN_TURNS,
    SESSION_COPY,
    compose_sessions,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
TODAY = date(2026, 7, 3)
WINDOW = 28


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _session(
    day: str,
    key: str,
    *,
    tool: str = "claude_code",
    first: str = "09:00:00",
    last: str = "09:30:00",
    turns: int = 10,
    input_tokens: int = 0,
    cached: int = 0,
    creation: int = 0,
    output: int = 0,
    cost: int = 0,
    unpriced: int = 0,
    compactions: int = 0,
    tool_calls: int = 0,
) -> tuple[str, str, str, str, str, int, int, int, int, int, int, int, int, int]:
    """One SessionTotalRow in the storage contract's column order."""
    return (
        day,
        key,
        tool,
        f"{day}T{first}+00:00",
        f"{day}T{last}+00:00",
        turns,
        input_tokens,
        cached,
        creation,
        output,
        cost,
        unpriced,
        compactions,
        tool_calls,
    )


def _seed(store: Store, sessions: list[object], path_hash: str = "h-1") -> None:
    store.replace_file_data(
        source_id="claude_code",
        path_hash=path_hash,
        cursor=(1, 1, 8),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        session_totals=sessions,  # type: ignore[arg-type]
    )


def test_ingest_prices_sessions_exactly_like_contributions() -> None:
    """The two aggregation paths price the same events, so their cost totals
    must agree to the micro-USD — the guard against session grain drifting
    into a second, subtly different money path."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import build_fixture_history

    store, _health = build_fixture_history()
    sessions = store.sessions_between("0001-01-01", "9999-12-31")
    assert sessions, "the fixture pack has sessions"
    session_cost = sum(row.cost_micro_usd for row in sessions)
    contribution_cost = sum(
        values[10]
        for _tool, _model, values in store.usage_rows_between(
            "0001-01-01", "9999-12-31"
        )
    )
    assert session_cost == contribution_cost


def test_session_merges_across_files_and_days(tmp_path: Path) -> None:
    """A session crossing midnight or spanning files writes several rows; the
    reader merges them into one span with summed counters."""
    store = _store(tmp_path)
    _seed(
        store,
        [
            _session(
                "2026-07-01", "s:a", first="22:00:00", last="23:59:00",
                turns=10, cost=1_000_000, cached=100, compactions=1,
            )
        ],
        path_hash="h-day1",
    )
    _seed(
        store,
        [
            _session(
                "2026-07-02", "s:a", first="00:01:00", last="01:00:00",
                turns=6, cost=500_000, cached=50, compactions=2,
            )
        ],
        path_hash="h-day2",
    )
    rows = store.sessions_between("2026-07-01", "2026-07-02")
    assert len(rows) == 1
    merged = rows[0]
    assert merged.assistant_turns == 16
    assert merged.cost_micro_usd == 1_500_000
    assert merged.compactions == 3
    assert merged.first_ts == "2026-07-01T22:00:00+00:00"
    assert merged.last_ts == "2026-07-02T01:00:00+00:00"


def test_receipt_is_the_latest_substantial_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            # The most expensive, but older.
            _session(
                "2026-07-01", "s:big", turns=80, cost=90_000_000,
                input_tokens=1_000, cached=9_000,
            ),
            # The latest substantial one — this is the receipt.
            _session(
                "2026-07-03", "s:latest", first="14:00:00", last="15:30:00",
                turns=20, cost=6_000_000, input_tokens=2_000, cached=8_000,
                compactions=2,
            ),
            # Later still, but too thin to earn a receipt.
            _session(
                "2026-07-03", "s:thin", first="20:00:00", last="20:02:00",
                turns=RECEIPT_MIN_TURNS - 1, cost=100,
            ),
        ],
    )
    reading = compose_sessions(store, TODAY, WINDOW)
    assert reading.available is True
    receipt = reading.receipt
    assert receipt is not None
    assert receipt.assistant_turns == 20
    assert receipt.cost_micro_usd == 6_000_000
    assert receipt.minutes == 90
    assert receipt.cache_hit_pct == 80  # 8,000 of 10,000 prompt tokens
    assert receipt.prompt_tokens_per_turn == 500
    assert receipt.line == SESSION_COPY["receipt-line"].format(
        minutes="90", turns="20", money="$6.00", tool="Claude Code"
    )
    # The compaction detail rides along only because this session compacted.
    assert any("compacted" in line for line in receipt.detail)


def test_no_substantial_session_is_withheld(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, [_session("2026-07-03", "s:thin", turns=RECEIPT_MIN_TURNS - 1)])
    reading = compose_sessions(store, TODAY, WINDOW)
    assert reading.available is False
    assert reading.receipt is None and reading.tax is None
    # An empty window is unavailable too, never a hollow card.
    assert compose_sessions(_store(tmp_path / "empty"), TODAY, WINDOW).available is False


def _seed_cohorts(
    store: Store, long_prompt_per_turn: int, long_cost_per_turn: int
) -> None:
    """Five long and five short sessions with exact per-turn arithmetic:
    short sessions carry 10k prompt tokens and cost $0.10 a turn."""
    sessions = []
    for i in range(COHORT_MIN_SESSIONS):
        sessions.append(
            _session(
                "2026-07-02", f"s:short{i}", turns=10,
                cached=10 * 10_000, cost=10 * 100_000,
            )
        )
        sessions.append(
            _session(
                "2026-07-02", f"s:long{i}", turns=LONG_SESSION_MIN_TURNS,
                cached=LONG_SESSION_MIN_TURNS * long_prompt_per_turn,
                cost=LONG_SESSION_MIN_TURNS * long_cost_per_turn,
            )
        )
    _seed(store, sessions)


def test_context_tax_carried_when_the_cost_follows(tmp_path: Path) -> None:
    """Long sessions carry 3x the context and cost 2x per turn — the weight is
    landing on the bill, and the reading says so with the mechanism."""
    store = _store(tmp_path)
    _seed_cohorts(store, long_prompt_per_turn=30_000, long_cost_per_turn=200_000)
    tax = compose_sessions(store, TODAY, WINDOW).tax
    assert tax is not None
    assert tax.register == "carried"
    assert tax.context_multiple_tenths == 30
    assert tax.cost_multiple_tenths == 20
    assert tax.long_sessions == COHORT_MIN_SESSIONS
    assert tax.short_sessions == COHORT_MIN_SESSIONS
    assert "3.0x" in tax.line and "2.0x" in tax.line
    assert "re-sends the whole conversation" in tax.line


def test_context_tax_absorbed_reports_the_cache_honestly(tmp_path: Path) -> None:
    """Long sessions carry 3x the context but cost only 1.1x per turn — the
    cache is absorbing it. That is a steady state, not an alarm."""
    store = _store(tmp_path)
    _seed_cohorts(store, long_prompt_per_turn=30_000, long_cost_per_turn=110_000)
    tax = compose_sessions(store, TODAY, WINDOW).tax
    assert tax is not None
    assert tax.register == "absorbed"
    assert tax.context_multiple_tenths == 30
    assert tax.cost_multiple_tenths == 11
    assert "cache is absorbing" in tax.line


def test_context_tax_gates(tmp_path: Path) -> None:
    """Withheld when either cohort is under-sampled, and when the carried
    context does not genuinely differ."""
    thin = _store(tmp_path / "thin")
    sessions = [
        _session("2026-07-02", f"s:short{i}", turns=10, cached=100_000, cost=1_000_000)
        for i in range(COHORT_MIN_SESSIONS)
    ]
    sessions += [
        _session(
            "2026-07-02", f"s:long{i}", turns=LONG_SESSION_MIN_TURNS,
            cached=LONG_SESSION_MIN_TURNS * 30_000,
            cost=LONG_SESSION_MIN_TURNS * 200_000,
        )
        for i in range(COHORT_MIN_SESSIONS - 1)  # one short of the gate
    ]
    _seed(thin, sessions)
    assert compose_sessions(thin, TODAY, WINDOW).tax is None

    # Same shape in both cohorts -> nothing to report.
    flat = _store(tmp_path / "flat")
    _seed_cohorts(flat, long_prompt_per_turn=10_000, long_cost_per_turn=100_000)
    flat_tax = compose_sessions(flat, TODAY, WINDOW).tax
    assert flat_tax is None
    # ...and the boundary itself is the gate, not an off-by-one.
    edge = _store(tmp_path / "edge")
    _seed_cohorts(
        edge,
        long_prompt_per_turn=10_000 * CONTEXT_MULTIPLE_MIN_TENTHS // 10,
        long_cost_per_turn=100_000,
    )
    assert compose_sessions(edge, TODAY, WINDOW).tax is not None


def test_double_compose_is_identical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_cohorts(store, long_prompt_per_turn=30_000, long_cost_per_turn=200_000)
    assert compose_sessions(store, TODAY, WINDOW) == compose_sessions(
        store, TODAY, WINDOW
    )


def test_window_bounds_exclude_older_sessions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, [_session("2026-05-01", "s:ancient", turns=50, cost=9_000_000)])
    assert compose_sessions(store, TODAY, WINDOW).available is False


def test_session_identity_never_reaches_a_surface(tmp_path: Path) -> None:
    """The load-bearing privacy pin: session_key is a local identity. It may
    live in the store and in SessionRow, but no composed reading may carry it
    — not in a sentence, not in a field."""
    import dataclasses
    import json

    store = _store(tmp_path)
    secret = "claude_code:11111111-2222-3333-4444-555555555555"
    _seed(
        store,
        [
            _session(
                "2026-07-03", secret, turns=20, cost=6_000_000,
                cached=8_000, input_tokens=2_000,
            )
        ],
    )
    reading = compose_sessions(store, TODAY, WINDOW)
    encoded = json.dumps(dataclasses.asdict(reading))
    assert secret not in encoded
    assert "11111111" not in encoded
    assert "session_key" not in encoded


def test_copy_catalog_and_minted_lines_are_clean(tmp_path: Path) -> None:
    for text in SESSION_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
    store = _store(tmp_path)
    _seed_cohorts(store, long_prompt_per_turn=30_000, long_cost_per_turn=200_000)
    reading = compose_sessions(store, TODAY, WINDOW)
    assert reading.receipt is not None and reading.tax is not None
    minted = [reading.receipt.line, reading.tax.line, reading.tax.basis]
    minted.extend(reading.receipt.detail)
    for line in minted:
        assert lexicon_violations(line) == []
        assert leak_findings(line) == []


# --- the context tax, wired to the skills that answer it ---------------------


def test_carried_context_aliases_only_to_context_bloat() -> None:
    """The alias bridge: a finding newer than the registry's closed tag
    vocabulary still reaches the skills that answer it. Crucially it does NOT
    reach cache skills — carried weight and cache misses are different
    failures, and someone whose cache is healthy must not be told to warm it."""
    from practicegraph.analysis.skills import FINDING_ALIASES, matchable_findings

    assert FINDING_ALIASES["context_carried"] == ("context_bloat",)
    assert "low_cache_reuse" not in FINDING_ALIASES["context_carried"]
    assert matchable_findings({"context_carried"}) == {
        "context_carried", "context_bloat",
    }
    # Unrelated findings pass through untouched.
    assert matchable_findings({"retry_storm"}) == {"retry_storm"}
    assert matchable_findings(set()) == set()


def test_carried_context_surfaces_context_skills_not_cache_skills() -> None:
    """End to end through the real matcher: the finding surfaces the working-set
    skills and leaves the cache-only skill alone."""
    from practicegraph.analysis.skills import (
        SCORE_FINDING,
        Skill,
        relevant_skills,
    )

    registry = (
        Skill(
            "context-keep-working-set-lean", "Keep the working set lean",
            "Prune what the task no longer needs.", "prompt",
            ("any",), ("any",), findings=("context_bloat", "low_cache_reuse"),
        ),
        Skill(
            "cost-preserve-prompt-cache", "Keep the prompt cache warm",
            "Reuse cached prompt tokens.", "prompt",
            ("any",), ("any",), findings=("low_cache_reuse",),
        ),
    )
    matched = relevant_skills(
        registry,
        tools_observed={"claude_code"},
        work_mix={},
        active_findings={"context_carried"},
    )
    by_id = {m.skill.skill_id: m for m in matched}
    lean = by_id["context-keep-working-set-lean"]
    cache = by_id["cost-preserve-prompt-cache"]
    # The working-set skill is PROMOTED by the alias: it earns the finding
    # band and says why it surfaced.
    assert lean.score >= SCORE_FINDING
    assert any("context bloat" in reason for reason in lean.reasons)
    # The cache-only skill is not. It remains an ordinary "any work" candidate
    # (which is why it is still in the list at all), but the alias awards it
    # nothing and it never claims to answer anything — no "warm your cache"
    # advice to someone whose cache is already serving most of their tokens.
    assert cache.score < SCORE_FINDING
    assert not any(reason.startswith("answers") for reason in cache.reasons)
    # ...so the ranking puts the right one first.
    assert matched[0].skill.skill_id == "context-keep-working-set-lean"


def test_carried_context_copy_is_registered_and_clean() -> None:
    from practicegraph.analysis.insights import FINDING_COPY, FINDING_IDS

    assert "context_carried" in FINDING_IDS
    title, body = FINDING_COPY["context_carried"]
    for text in (title, body):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
    # The body carries the mechanism, which is what makes it actionable.
    assert "re-sends the conversation" in body


# --- tokenizer generations (Claude 4.7 changed encoding) ---------------------


def test_tokenizer_eras_are_classified_and_straddling_detected() -> None:
    from practicegraph.analysis.tokenizer import (
        straddles_tokenizer_change,
        uses_wide_tokenizer,
    )

    for wide in (
        "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
        "claude-fable-5", "claude-fable-5-20260630", "claude-mythos-5",
        "claude-sonnet-5",
    ):
        assert uses_wide_tokenizer(wide) is True, wide
    for narrow in (
        "claude-sonnet-4-6", "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001", "claude-opus-4-1-20250805",
    ):
        assert uses_wide_tokenizer(narrow) is False, narrow

    # Straddling needs BOTH eras present.
    assert straddles_tokenizer_change({"claude-fable-5", "claude-sonnet-4-6"}) is True
    assert straddles_tokenizer_change({"claude-fable-5", "claude-opus-5"}) is False
    assert straddles_tokenizer_change({"claude-sonnet-4-6"}) is False
    assert straddles_tokenizer_change(set()) is False
    # OpenAI models are unaffected by the change and never straddle alone.
    assert straddles_tokenizer_change({"gpt-5.6-sol", "gpt-5-codex"}) is False
    # ...and they do not suppress a genuine Anthropic straddle either.
    assert straddles_tokenizer_change(
        {"gpt-5.6-sol", "claude-fable-5", "claude-sonnet-4-6"}
    ) is True


def test_context_tax_caveats_a_mixed_tokenizer_window(tmp_path: Path) -> None:
    """A token-count comparison across encoding generations carries an
    artifact. The reading says so rather than reporting encoding as behaviour
    — and deliberately does NOT caveat the cost multiple, which is money the
    person actually spent."""
    store = _store(tmp_path)
    _seed_cohorts(store, long_prompt_per_turn=30_000, long_cost_per_turn=200_000)
    # Same window, two model mixes.
    single = compose_sessions(store, TODAY, WINDOW)
    assert single.tax is not None
    assert single.tax.caveat == ""  # no contributions seeded -> no straddle

    from practicegraph.analysis.sessions import SESSION_COPY, _context_tax

    rows = store.sessions_between("2026-06-06", TODAY.isoformat())
    mixed = _context_tax(rows, WINDOW, mixed_tokenizers=True)
    assert mixed is not None
    assert mixed.caveat == SESSION_COPY["tax-tokenizer-caveat"]
    # The numbers themselves are untouched by the caveat.
    assert mixed.context_multiple_tenths == single.tax.context_multiple_tenths
    assert mixed.cost_multiple_tenths == single.tax.cost_multiple_tenths
    # The caveat names the context multiple as directional, not the money.
    assert "cost multiple" in mixed.caveat and "real money" in mixed.caveat
