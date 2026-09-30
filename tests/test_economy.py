"""The economy reading (W1.1): capability per dollar/window. These tests pin
that the reading is exact integer arithmetic over the receipts window
(INV-6, double-compose identical), honestly withheld below the evidence
floor, advisor-first in the lever slot (a decisive board owns it, and premium
share is never re-voiced here), dual-denominated by billing mode (AM-1), that
the first-open retrospective (AM-3) renders until dismissed exactly once, and
that every minted sentence stays inside the copy lexicon."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from practicegraph.analysis.advisor import (
    ADVISOR_TAKE_IDS,
    AdvisorBoard,
    AdvisorTake,
)
from practicegraph.analysis.advisor_receipts import gather_receipts
from practicegraph.analysis.economy import (
    ADVISOR_CALM_TAKE_ID,
    ECONOMY_BILLING_MODES,
    ECONOMY_COPY,
    ECONOMY_INTRO_META_KEY,
    ECONOMY_MIN_PRICED_TURNS,
    compose_economy,
    record_economy_intro_seen,
)
from practicegraph.config import BILLING_MODES
from practicegraph.history import week_over_week
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import ContributionRow, Store

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
TODAY = date(2026, 7, 3)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _values(**overrides: int) -> list[int]:
    base = dict.fromkeys(ContributionRow._fields, 0)
    base.update(overrides)
    return [base[field] for field in ContributionRow._fields]


def _seed(
    store: Store,
    contributions: list[tuple[str, str, str, list[int]]],
    rate_limit_marks: list[tuple[str, str, int, int]] | None = None,
    path_hash: str = "h-econ",
) -> None:
    store.replace_file_data(
        source_id="claude_code",
        path_hash=path_hash,
        cursor=(1, 1, 8),
        contributions=contributions,
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        rate_limit_marks=rate_limit_marks or [],
    )


def _seed_window(store: Store) -> None:
    """Three active days inside the 28-day window: 60 priced turns,
    $16.00, 300k prompt tokens of which 85k came from cache (28%)."""
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=30,
                    input_tokens=100_000,
                    cached_tokens=50_000,
                    cache_creation_tokens=10_000,
                    output_tokens=20_000,
                    cost_micro_usd=9_000_000,
                    tool_calls=10,
                ),
            ),
            (
                "2026-07-02",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=20,
                    input_tokens=60_000,
                    cached_tokens=20_000,
                    cache_creation_tokens=5_000,
                    output_tokens=10_000,
                    cost_micro_usd=6_000_000,
                ),
            ),
            (
                "2026-07-03",
                "claude_code",
                "claude-haiku-4-5",
                _values(
                    assistant_turns=10,
                    input_tokens=40_000,
                    cached_tokens=15_000,
                    output_tokens=5_000,
                    cost_micro_usd=1_000_000,
                ),
            ),
        ],
    )


def _compose(store: Store, mode: str = "api", board: AdvisorBoard | None = None):
    return compose_economy(
        store, TODAY, gather_receipts(store, TODAY), board, None, mode
    )


def _take(take_id: str) -> AdvisorTake:
    return AdvisorTake(
        take_id=take_id,
        tier="use",
        tool="",
        verdict="verdict",
        boundary="boundary",
        receipts=("receipt",),
        market="",
        steelman="",
        action="action",
        experiment="",
        attribution="",
        expires="",
        impact_micro_usd=0,
        confidence="medium",
        confidence_note="note",
    )


def _board(*take_ids: str) -> AdvisorBoard:
    return AdvisorBoard(
        as_of="2026-07-03",
        market_state="absent",
        market_as_of="",
        takes=tuple(_take(take_id) for take_id in take_ids),
    )


def test_reading_is_exact_integer_arithmetic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    reading = _compose(store)
    assert reading.available is True
    assert reading.billing_mode == "api"
    assert reading.window_days == 28
    assert reading.active_days == 3
    assert reading.cost_micro_usd == 16_000_000
    assert reading.priced_turns == 60
    assert reading.cost_per_priced_turn_micro_usd == 266_666
    assert reading.cache_hit_pct == 28  # 85k of 300k prompt tokens
    assert reading.wow_cost_delta_pct is None  # no wow passed in
    assert reading.week_window_pct is None  # api mode never reads the gauge


def test_below_the_evidence_floor_is_withheld(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=ECONOMY_MIN_PRICED_TURNS - 1,
                    cost_micro_usd=1_000_000,
                ),
            )
        ],
    )
    reading = _compose(store)
    assert reading.available is False
    assert reading.lever is None
    assert reading.retro is None


def test_zero_cost_is_withheld_even_with_volume(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "some-unpriced-model",
                _values(assistant_turns=100, unpriced_turns=100),
            )
        ],
    )
    assert _compose(store).available is False


def test_double_compose_is_identical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    assert _compose(store) == _compose(store)


def test_cache_lever_fires_with_exact_copy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    lever = _compose(store).lever
    assert lever is not None and lever.lever_id == "cache"
    assert lever.metric == 28
    assert lever.title == ECONOMY_COPY["lever-cache-title"]
    assert lever.body == ECONOMY_COPY["lever-cache-body"].format(pct=28)


def test_context_lever_fires_on_heavy_prompt_per_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=50,
                    input_tokens=3_500_000,
                    cached_tokens=3_000_000,
                    cost_micro_usd=10_000_000,
                ),
            )
        ],
    )
    lever = _compose(store).lever
    # Cache reuse is healthy (46%), so the context lever takes the slot:
    # 6.5M prompt tokens over 50 turns = 130k per turn.
    assert lever is not None and lever.lever_id == "context"
    assert lever.metric == 130_000
    assert "130.0K" in lever.body


def test_unpriced_lever_and_calm_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=60,
                    input_tokens=10_000,
                    cached_tokens=40_000,
                    cost_micro_usd=5_000_000,
                    unpriced_turns=20,
                ),
            )
        ],
    )
    lever = _compose(store).lever
    assert lever is not None and lever.lever_id == "unpriced"
    assert lever.metric == 20

    calm_store = _store(tmp_path / "calm")
    _seed(
        calm_store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=60,
                    input_tokens=10_000,
                    cached_tokens=40_000,
                    cost_micro_usd=5_000_000,
                ),
            )
        ],
    )
    calm_lever = _compose(calm_store).lever
    assert calm_lever is not None and calm_lever.lever_id == "calm"
    assert calm_lever.body == ECONOMY_COPY["lever-calm-body"]


def test_decisive_advisor_board_owns_the_lever_slot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    lever = _compose(store, board=_board("premium-routing")).lever
    assert lever is not None and lever.lever_id == "advisor"
    # A pointer only — no number from the take is restated here.
    assert lever.metric == 0
    # A calm-only board does NOT own the slot; the hygiene levers speak.
    calm = _compose(store, board=_board(ADVISOR_CALM_TAKE_ID)).lever
    assert calm is not None and calm.lever_id == "cache"


def test_wow_gate_needs_three_active_days_both_weeks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(
        store,
        [
            (
                day,
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=20,
                    input_tokens=50_000,
                    cached_tokens=50_000,
                    cost_micro_usd=2_000_000,
                ),
            )
            for day in ("2026-06-20", "2026-06-21", "2026-06-22")
        ]
        + [
            (
                day,
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=20,
                    input_tokens=50_000,
                    cached_tokens=50_000,
                    cost_micro_usd=4_000_000,
                ),
            )
            for day in ("2026-07-01", "2026-07-02", "2026-07-03")
        ],
    )
    wow = week_over_week(store, TODAY)
    assert wow is not None
    reading = compose_economy(
        store, TODAY, gather_receipts(store, TODAY), None, wow, "api"
    )
    assert reading.wow_cost_delta_pct == wow.cost_delta_pct

    thin = _store(tmp_path / "thin")
    _seed(
        thin,
        [
            (
                day,
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=30,
                    input_tokens=50_000,
                    cached_tokens=50_000,
                    cost_micro_usd=2_000_000,
                ),
            )
            for day in ("2026-06-20", "2026-06-21", "2026-07-01", "2026-07-02")
        ],
    )
    thin_wow = week_over_week(thin, TODAY)
    assert thin_wow is not None  # history's own gate passes...
    thin_reading = compose_economy(
        thin, TODAY, gather_receipts(thin, TODAY), None, thin_wow, "api"
    )
    assert thin_reading.wow_cost_delta_pct is None  # ...but ours withholds


def test_subscription_mode_reads_the_weekly_gauge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    _seed_gauge = [
        # A 5h reading the same day must not satisfy the weekly lens.
        ("2026-07-02", "2026-07-02T09:00:00+00:00", 900, 300),
        ("2026-07-02", "2026-07-02T10:00:00+00:00", 435, 10080),
    ]
    store.replace_file_data(
        source_id="codex_cli",
        path_hash="h-gauge",
        cursor=(1, 1, 7),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=NOW,
        rate_limit_marks=_seed_gauge,
    )
    reading = _compose(store, mode="subscription")
    assert reading.billing_mode == "subscription"
    assert reading.week_window_pct == 44  # 435 tenths, half-up
    # Without any weekly reading in the lookback: withheld, not zero.
    bare = _store(tmp_path / "bare")
    _seed_window(bare)
    assert _compose(bare, mode="subscription").week_window_pct is None


def test_unrecognised_billing_mode_degrades_to_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    reading = _compose(store, mode="weird")
    assert reading.billing_mode == "unknown"
    assert reading.week_window_pct is None


def test_retro_renders_until_dismissed_exactly_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)
    retro = _compose(store).retro
    assert retro is not None
    assert retro.days == 90
    assert retro.active_days == 3
    assert retro.assistant_turns == 60
    assert retro.cost_micro_usd == 16_000_000
    assert retro.line == ECONOMY_COPY["retro-line"].format(
        days=90, turns="60", active=3, usd="$16.00"
    )
    record_economy_intro_seen(store)
    assert store.meta_get(ECONOMY_INTRO_META_KEY) == "1"
    assert _compose(store).retro is None
    # Idempotent — dismissing again changes nothing.
    record_economy_intro_seen(store)
    assert _compose(store).retro is None


def test_copy_catalog_and_minted_lines_are_clean(tmp_path: Path) -> None:
    for text in ECONOMY_COPY.values():
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []
        assert "!" not in text
    store = _store(tmp_path)
    _seed_window(store)
    reading = _compose(store)
    assert reading.lever is not None and reading.retro is not None
    for minted in (reading.lever.body, reading.retro.line):
        assert lexicon_violations(minted) == []
        assert leak_findings(minted) == []


def _seed_reasoning(store: Store, reasoning: int, output: int = 100_000) -> None:
    """One cache-healthy, context-light window row on the Fable tier
    (output listed at $50/M): reasoning attribution numbers stay exact."""
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=50,
                    input_tokens=10_000,
                    cached_tokens=190_000,
                    output_tokens=output,
                    reasoning_tokens=reasoning,
                    cost_micro_usd=7_000_000,
                ),
            )
        ],
    )


def test_reasoning_attribution_is_never_additive(tmp_path: Path) -> None:
    """The W1.2 property test: attribution explains a slice of the stored
    total and changes NOTHING — the window cost is byte-identical, and the
    slice can never exceed the output spend it is a share of."""
    store = _store(tmp_path)
    _seed_reasoning(store, reasoning=40_000)
    reading = _compose(store)
    # The stored total is untouched by the attribution.
    assert reading.cost_micro_usd == 7_000_000
    assert reading.reasoning is not None
    # 40k reasoning tokens at the $50/M output rate = $2.00 of the $5.00
    # output spend — a 40% share, all exact integers.
    assert reading.reasoning.cost_micro_usd == 2_000_000
    assert reading.reasoning.output_cost_micro_usd == 5_000_000
    assert reading.reasoning.share_pct == 40
    assert (
        reading.reasoning.cost_micro_usd
        <= reading.reasoning.output_cost_micro_usd
    )


def test_reasoning_clamps_malformed_rows(tmp_path: Path) -> None:
    """Reasoning tokens are a subset of billed output per turn; a malformed
    row claiming more is clamped to the output volume, never re-priced."""
    store = _store(tmp_path)
    _seed_reasoning(store, reasoning=150_000, output=100_000)
    reading = _compose(store)
    assert reading.reasoning is not None
    assert reading.reasoning.cost_micro_usd == 5_000_000  # clamped to output
    assert reading.reasoning.share_pct == 100


def test_reasoning_absent_and_unpriced_rows_skipped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_window(store)  # no reasoning tokens anywhere
    assert _compose(store).reasoning is None
    # An unpriced model's output/reasoning volume never enters the attribution.
    with_mystery = _store(tmp_path / "mystery")
    _seed_reasoning(with_mystery, reasoning=40_000)
    _seed(
        with_mystery,
        [
            (
                "2026-07-02",
                "claude_code",
                "mystery-local-model",
                _values(
                    assistant_turns=10,
                    output_tokens=500_000,
                    reasoning_tokens=400_000,
                    unpriced_turns=10,
                ),
            )
        ],
        path_hash="h-mystery",
    )
    reading = _compose(with_mystery)
    assert reading.reasoning is not None
    assert reading.reasoning.cost_micro_usd == 2_000_000  # unchanged
    assert reading.reasoning.output_cost_micro_usd == 5_000_000


def test_reasoning_lever_fires_past_the_share_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_reasoning(store, reasoning=40_000)  # 40% share, cache healthy
    lever = _compose(store).lever
    assert lever is not None and lever.lever_id == "reasoning"
    assert lever.metric == 40
    assert "40%" in lever.body
    # Below the floor the attribution still shows, the lever stays calm.
    quiet = _store(tmp_path / "quiet")
    _seed_reasoning(quiet, reasoning=10_000)  # 10% share
    quiet_reading = _compose(quiet)
    assert quiet_reading.reasoning is not None
    assert quiet_reading.reasoning.share_pct == 10
    assert quiet_reading.lever is not None
    assert quiet_reading.lever.lever_id == "calm"


def test_billing_modes_and_calm_id_are_pinned() -> None:
    """The closed enums this module leans on are pinned against their owners:
    config's modes and the advisor's calm take id cannot drift silently."""
    assert set(ECONOMY_BILLING_MODES) == set(BILLING_MODES)
    assert ADVISOR_CALM_TAKE_ID in ADVISOR_TAKE_IDS


def _seed_churn(
    store: Store,
    cached: int,
    creation: int,
    creation_1h: int = 0,
    fresh: int = 10_000,
) -> None:
    """One window row on the Fable tier, cache rates all distinct so the
    decomposition arithmetic cannot pass by coincidence: read $1/M, 5m write
    $12.50/M, 1h write $20/M."""
    _seed(
        store,
        [
            (
                "2026-07-01",
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=50,
                    input_tokens=fresh,
                    cached_tokens=cached,
                    cache_creation_tokens=creation,
                    cache_creation_1h_tokens=creation_1h,
                    output_tokens=20_000,
                    cost_micro_usd=8_000_000,
                ),
            )
        ],
    )


def test_churn_decomposes_cache_spend_exactly(tmp_path: Path) -> None:
    """The W3.2 attribution: 3M cached reads at $1/M = $3.00 back; 100k 5m
    writes at $12.50/M + 100k 1h writes at $20/M = $3.25 re-written. Exact
    integers, both TTLs priced at their own listed rate — and the stored
    window total is untouched (an attribution, never an addition)."""
    store = _store(tmp_path)
    _seed_churn(store, cached=3_000_000, creation=200_000, creation_1h=100_000)
    reading = _compose(store)
    assert reading.cost_micro_usd == 8_000_000  # stored total unchanged
    assert reading.churn is not None
    assert reading.churn.read_cost_micro_usd == 3_000_000
    assert reading.churn.creation_cost_micro_usd == 3_250_000
    assert reading.churn.share_pct == 52  # 3_250_000 * 100 // 6_250_000
    assert "$3.00" in reading.churn.line and "$3.25" in reading.churn.line
    assert lexicon_violations(reading.churn.line) == []
    assert leak_findings(reading.churn.line) == []


def test_churn_is_withheld_below_the_spend_floor(tmp_path: Path) -> None:
    """A share of pennies is noise: under $1 of cache spend at listed rates
    the split is withheld while the reading itself stays available."""
    store = _store(tmp_path)
    _seed_churn(store, cached=500_000, creation=0)  # $0.50 of cache spend
    reading = _compose(store)
    assert reading.available
    assert reading.churn is None


def test_churn_clamps_malformed_1h_claims(tmp_path: Path) -> None:
    """cache_creation_1h is stored as a subset of cache_creation; a malformed
    row claiming more is clamped to the total (the estimator's own rule),
    never double-counted."""
    store = _store(tmp_path)
    _seed_churn(
        store, cached=2_000_000, creation=100_000, creation_1h=150_000
    )
    reading = _compose(store)
    assert reading.churn is not None
    # All 100k billed at the 1h rate ($20/M), none at the 5m rate.
    assert reading.churn.creation_cost_micro_usd == 2_000_000
    assert reading.churn.read_cost_micro_usd == 2_000_000


def test_churn_skips_unpriced_rows(tmp_path: Path) -> None:
    """A model the card cannot price contributes nothing to either side —
    consistent with the estimator, which prices those rows at zero."""
    store = _store(tmp_path)
    _seed_churn(store, cached=3_000_000, creation=200_000, creation_1h=100_000)
    _seed(
        store,
        [
            (
                "2026-07-02",
                "claude_code",
                "mystery-local-model",
                _values(
                    assistant_turns=10,
                    cached_tokens=50_000_000,
                    cache_creation_tokens=50_000_000,
                    unpriced_turns=10,
                ),
            )
        ],
        path_hash="h-mystery-churn",
    )
    reading = _compose(store)
    assert reading.churn is not None
    assert reading.churn.read_cost_micro_usd == 3_000_000
    assert reading.churn.creation_cost_micro_usd == 3_250_000


def test_churn_lever_fires_past_the_share_floor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_churn(store, cached=3_000_000, creation=200_000, creation_1h=100_000)
    lever = _compose(store).lever
    assert lever is not None and lever.lever_id == "churn"
    assert lever.metric == 52
    assert "52%" in lever.body
    assert lexicon_violations(lever.body) == []
    # A healthy split keeps the lever calm while the fact line stays. Kept
    # under 60k prompt tokens per turn so the context lever cannot fire.
    healthy = _store(tmp_path / "healthy")
    _seed_churn(healthy, cached=2_000_000, creation=20_000)  # 11% share
    healthy_reading = _compose(healthy)
    assert healthy_reading.churn is not None
    assert healthy_reading.churn.share_pct == 11
    assert healthy_reading.lever is not None
    assert healthy_reading.lever.lever_id == "calm"


def test_low_cache_reuse_outranks_churn_in_the_ladder(tmp_path: Path) -> None:
    """When fresh-token leakage and churn both fire, the coarser problem owns
    the one lever slot: fixing reuse changes the churn arithmetic anyway."""
    store = _store(tmp_path)
    # 8% reuse over a 3.4M-token prompt window; churn share 80% ($1.25 of
    # writes against $0.30 of reads) — both past their floors.
    _seed_churn(
        store, cached=300_000, creation=100_000, fresh=3_000_000
    )
    reading = _compose(store)
    assert reading.churn is not None and reading.churn.share_pct == 80
    assert reading.lever is not None and reading.lever.lever_id == "cache"


def _seed_commits(
    store: Store,
    commits: int,
    day: str = "2026-07-01",
    cost: int = 6_000_000,
    path_hash: str = "h-denom",
) -> None:
    """One window row carrying commit attempts: 50 priced turns, healthy
    cache, no reasoning — only the denominator varies."""
    _seed(
        store,
        [
            (
                day,
                "claude_code",
                "claude-fable-5",
                _values(
                    assistant_turns=50,
                    input_tokens=10_000,
                    cached_tokens=190_000,
                    output_tokens=20_000,
                    cost_micro_usd=cost,
                    git_commit_attempts=commits,
                ),
            )
        ],
        path_hash=path_hash,
    )


def test_denominator_divides_the_headline_spend_exactly(tmp_path: Path) -> None:
    """W3.1: the same dollars the headline shows, divided by the window's
    commit attempts — never a second derivation. 12 commits against $6.00 is
    50 cents a commit, exact integers, and the minted line says attempts."""
    store = _store(tmp_path)
    _seed_commits(store, commits=12)
    reading = _compose(store)
    assert reading.denominator is not None
    assert reading.denominator.commit_attempts == 12
    assert reading.denominator.per_commit_micro_usd == 500_000
    assert reading.denominator.per_commit_micro_usd == (
        reading.cost_micro_usd // 12
    )
    assert "$0.50" in reading.denominator.line
    assert "12 commands" in reading.denominator.line
    assert "Commands do not establish completed work" in reading.denominator.line
    assert reading.denominator.prior_per_commit_micro_usd is None
    assert lexicon_violations(reading.denominator.line) == []
    assert leak_findings(reading.denominator.line) == []


def test_denominator_is_withheld_below_the_commit_floor(tmp_path: Path) -> None:
    """Nine commits is not a denominator (withheld, not zero) — the reading
    itself stays available."""
    store = _store(tmp_path)
    _seed_commits(store, commits=9)
    reading = _compose(store)
    assert reading.available
    assert reading.denominator is None


def test_denominator_trend_needs_the_prior_window_past_the_floor(
    tmp_path: Path,
) -> None:
    """The prior-window clause rides only when THAT window also clears the
    commit floor: a thin prior month is absent from the line, not zero."""
    store = _store(tmp_path)
    _seed_commits(store, commits=12)
    # Prior 28-day window (window is 2026-06-06..2026-07-03): 20 commits at
    # $8.00 -> $0.40 per commit.
    _seed_commits(
        store, commits=20, day="2026-06-01", cost=8_000_000,
        path_hash="h-denom-prior",
    )
    reading = _compose(store)
    assert reading.denominator is not None
    assert reading.denominator.prior_per_commit_micro_usd == 400_000
    assert "Last window it was about $0.40" in reading.denominator.line

    thin_prior = _store(tmp_path / "thin")
    _seed_commits(thin_prior, commits=12)
    _seed_commits(
        thin_prior, commits=5, day="2026-06-01", path_hash="h-thin-prior"
    )
    thin_reading = _compose(thin_prior)
    assert thin_reading.denominator is not None
    assert thin_reading.denominator.prior_per_commit_micro_usd is None
    assert "prior window" not in thin_reading.denominator.line
