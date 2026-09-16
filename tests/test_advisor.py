"""Advisor fusion engine: receipts-first takes, decisive-gap gates, honest
degradation, the calm default, and the closed copy catalog scans
(docs/ADVISOR_PLAN.md Phase 3)."""

from __future__ import annotations

import copy
from datetime import date

from practicegraph.analysis.advisor import (
    ADVISOR_COPY,
    ADVISOR_TAKE_IDS,
    CONFIDENCE_LEVELS,
    MAX_TAKES,
    build_advisor_board,
)
from practicegraph.analysis.advisor_market import (
    ATTRIBUTION,
    parse_advisor_artifact,
)
from practicegraph.analysis.advisor_market import (
    LEGACY_SCHEMA as SCHEMA,
)
from practicegraph.analysis.advisor_receipts import build_receipts
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import ContributionRow

TODAY = date(2026, 7, 3)


def _values(**overrides: int) -> list[int]:
    base = dict.fromkeys(ContributionRow._fields, 0)
    base.update(overrides)
    return [base[field] for field in ContributionRow._fields]


def _market_row(family: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "family": family,
        "aa_slug": family.replace("_", "-"),
        "released_on": "2026-06-01",
        "coding_index_tenths": 600,
        "agentic_index_tenths": 500,
        "price_in_micro": 1_000_000,
        "price_out_micro": 5_000_000,
        "price_cache_read_micro": 100_000,
        "median_tps_tenths": 1000,
        "ttft_ms": 800,
        "context_window": 200_000,
    }
    row.update(overrides)
    return row


def _artifact(
    models: list[dict[str, object]],
    verdicts: list[dict[str, object]] | None = None,
    expires: str = "2026-08-02",
) -> object:
    parsed = parse_advisor_artifact(
        {
            "schema": SCHEMA,
            "artifact_version": "advisor-2026-07-01-abcdef1234",
            "as_of": "2026-07-01",
            "expires": expires,
            "source": {
                "name": "Artificial Analysis",
                "url": "https://artificialanalysis.ai/models",
                "attribution": "Model benchmark and price data: Artificial Analysis",
            },
            "models": models,
            "verdicts": verdicts or [],
        }
    )
    assert parsed is not None
    return parsed


def _verdict_card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "id": "budget-first-ladder",
        "tier": "use",
        "families": ["claude_haiku"],
        "tools": ["claude_code"],
        "verdict": "Route mechanical work to the budget family.",
        "boundary": "Escalate when a diff would not land as-is.",
        "steelman": "The premium family keeps the best first-pass rate.",
        "action": "Set the budget family as the default for routine edits.",
        "experiment": "",
        "as_of": "2026-07-01",
        "expires": "2026-08-16",
    }
    card.update(overrides)
    return card


def _routing_receipts() -> object:
    """A confident premium family carrying 100% of window spend."""
    rows = [
        (
            "2026-07-01",
            "claude_code",
            "claude-fable-5",
            _values(
                assistant_turns=40,
                input_tokens=1_000_000,
                output_tokens=2_000_000,
                cost_micro_usd=34_000_000,
            ),
        ),
        ("2026-07-02", "claude_code", "claude-fable-5",
         _values(assistant_turns=10, cost_micro_usd=33_000_000)),
        ("2026-07-03", "claude_code", "claude-fable-5",
         _values(assistant_turns=10, cost_micro_usd=33_000_000)),
        ("2026-07-03", "claude_code", "claude-haiku-4-5", _values(assistant_turns=5)),
    ]
    return build_receipts(rows, TODAY)


def _quiet_receipts() -> object:
    """A window with nothing decisive in it: one small budget-tier family, so
    no routing rule clears its threshold and the calm default carries."""
    rows = [
        ("2026-07-03", "claude_code", "claude-haiku-4-5", _values(assistant_turns=5))
    ]
    return build_receipts(rows, TODAY)


_FABLE_ROW = _market_row(
    "claude_fable",
    price_in_micro=10_000_000,
    price_out_micro=50_000_000,
    price_cache_read_micro=1_000_000,
    coding_index_tenths=900,
)


def test_premium_routing_fires_with_ceiling_math_and_attribution() -> None:
    artifact = _artifact([_FABLE_ROW, _market_row("claude_haiku")])
    board = build_advisor_board(_routing_receipts(), artifact, TODAY)

    assert board.market_state == "fresh"
    take = board.takes[0]
    assert take.take_id == "premium-routing"
    assert take.tier == "try"
    assert take.tool == "claude_code"
    # Ceiling: 1M input @ $1/M + 2M output @ $5/M = $11; spend was $100.
    assert take.impact_micro_usd == 89_000_000
    assert "$100" in take.receipts[0] and "100%" in take.receipts[0]
    assert "$11.00" in take.receipts[1] and "$89.00" in take.receipts[1]
    assert "ceiling" in take.receipts[1]
    assert "10.0x" in take.market
    assert take.attribution == ATTRIBUTION
    assert take.evidence and all(item.observed_on == "" for item in take.evidence)
    assert all(item.source_url == "https://artificialanalysis.ai/models" for item in take.evidence)
    assert take.expires == "2026-08-02"
    assert take.steelman  # advising away from premium requires the steelman


def test_routing_judges_premium_spend_combined_across_families() -> None:
    """The real-world case that motivated the combined gate: spend split
    47/48 between two premium tiers must not slip under a per-family
    threshold — premium share is one number (the premium_heavy definition)."""
    rows = []
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        rows.append(
            (day, "claude_code", "claude-fable-5",
             _values(assistant_turns=20, cost_micro_usd=47_000_000,
                     input_tokens=200_000, output_tokens=400_000))
        )
        rows.append(
            (day, "claude_code", "claude-opus-4-8",
             _values(assistant_turns=20, cost_micro_usd=48_000_000,
                     input_tokens=200_000, output_tokens=400_000))
        )
        rows.append(
            (day, "claude_code", "claude-haiku-4-5",
             _values(assistant_turns=15, cost_micro_usd=5_000_000))
        )
    receipts = build_receipts(rows, TODAY)
    artifact = _artifact(
        [
            _FABLE_ROW,
            _market_row(
                "claude_opus", price_in_micro=5_000_000, price_out_micro=25_000_000
            ),
            _market_row("claude_haiku"),
        ]
    )
    board = build_advisor_board(receipts, artifact, TODAY)
    take = board.takes[0]
    assert take.take_id == "premium-routing"
    # 95% combined share; the receipt names the combined tier set.
    assert "95%" in take.receipts[0]
    assert "The premium tiers carried" in take.receipts[0]
    # Ceiling reprices BOTH premium families at the candidate's rates:
    # 2x (600k in @ $1/M + 1.2M out @ $5/M) = $13.20 vs $285 spend.
    assert take.impact_micro_usd == 285_000_000 - 13_200_000
    # The market ratio line names the top-spend family, not the blend.
    assert "the Opus tier" in take.market


def test_routing_needs_a_decisive_gap() -> None:
    narrow = _artifact(
        [
            _FABLE_ROW,
            _market_row(
                "claude_haiku", price_in_micro=6_000_000, price_out_micro=30_000_000
            ),
        ]
    )
    board = build_advisor_board(_routing_receipts(), narrow, TODAY)
    assert [take.take_id for take in board.takes] == ["calm-default"]


def test_stale_market_degrades_honestly() -> None:
    stale = _artifact([_FABLE_ROW, _market_row("claude_haiku")], expires="2026-07-01")
    board = build_advisor_board(_routing_receipts(), stale, TODAY)
    assert board.market_state == "stale"
    assert board.market_as_of == "2026-07-01"
    assert [take.take_id for take in board.takes] == ["calm-default"]


def test_absent_market_keeps_receipts_only_takes() -> None:
    board = build_advisor_board(_routing_receipts(), None, TODAY)
    assert board.market_state == "absent"
    assert [take.take_id for take in board.takes] == ["calm-default"]
    assert board.takes[0].attribution == ""


def test_sticker_price_lie_reads_per_turn_economics() -> None:
    rows = []
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        rows.append(
            (day, "codex", "gpt-5.4-mini",
             _values(assistant_turns=14, cost_micro_usd=14_000_000))
        )
        rows.append(
            (day, "codex", "claude-sonnet-5",
             _values(assistant_turns=14, cost_micro_usd=7_000_000))
        )
    receipts = build_receipts(rows, TODAY)
    artifact = _artifact(
        [
            _market_row("gpt_5_mini", price_out_micro=4_500_000),
            _market_row("claude_sonnet", price_out_micro=10_000_000),
        ]
    )
    board = build_advisor_board(receipts, artifact, TODAY)
    take = board.takes[0]
    assert take.take_id == "sticker-price-lie"
    assert take.tier == "watch"
    # Observed: $1.00/turn on the sticker-cheap family vs $0.50 on the richer.
    assert "$1.00" in take.receipts[0]
    assert "$0.50" in take.receipts[1]
    assert take.attribution == ATTRIBUTION


def test_escalation_firing_needs_volume_rework_and_an_escape_hatch() -> None:
    rows = [
        ("2026-07-01", "claude_code", "claude-fable-5",
         _values(assistant_turns=5, cost_micro_usd=1_000_000)),
    ]
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        rows.append(
            (day, "claude_code", "claude-haiku-4-5",
             _values(assistant_turns=20, tool_calls=70, rework_edits=35)),
        )
    receipts = build_receipts(rows, TODAY)
    board = build_advisor_board(receipts, None, TODAY)
    take = board.takes[0]
    assert take.take_id == "escalation-firing"
    assert take.tier == "try"
    assert "50.0" in take.receipts[0]  # 105 rework / 210 tool calls
    assert take.market == "" and take.attribution == ""


def test_curated_verdict_gates_on_relevance_and_expiry() -> None:
    models = [_market_row("claude_haiku"), _market_row("claude_sonnet")]
    rows = [
        ("2026-07-03", "claude_code", "claude-haiku-4-5", _values(assistant_turns=5))
    ]
    receipts = build_receipts(rows, TODAY)

    surfaced = build_advisor_board(
        receipts, _artifact(models, [_verdict_card()]), TODAY
    )
    assert surfaced.takes[0].take_id == "curated-verdict"
    assert surfaced.takes[0].verdict == "Route mechanical work to the budget family."
    assert surfaced.takes[0].expires == "2026-08-02"

    expired = build_advisor_board(
        receipts,
        _artifact(models, [_verdict_card(expires="2026-07-02")]),
        TODAY,
    )
    assert expired.takes[0].take_id == "calm-default"

    edition_expired = build_advisor_board(
        receipts, _artifact(models, [_verdict_card()], expires="2026-07-02"), TODAY,
    )
    assert edition_expired.takes[0].take_id == "calm-default"

    unrelated = build_advisor_board(
        receipts,
        _artifact(models, [_verdict_card(families=["claude_sonnet"])]),
        TODAY,
    )
    assert unrelated.takes[0].take_id == "calm-default"

    watch = build_advisor_board(
        receipts,
        _artifact(
            models, [_verdict_card(families=["claude_sonnet"], tier="watch")]
        ),
        TODAY,
    )
    assert watch.takes[0].take_id == "curated-verdict"
    assert watch.takes[0].tier == "watch"


def test_board_caps_at_three_ranked_by_impact_then_catalog_order() -> None:
    rows = []
    for day in ("2026-07-01", "2026-07-02", "2026-07-03"):
        # Fable: high volume, $1.00/turn, 65% of window spend.
        rows.append(
            (day, "claude_code", "claude-fable-5",
             _values(assistant_turns=40, cost_micro_usd=40_000_000,
                     input_tokens=400_000, output_tokens=700_000))
        )
        # Haiku: sticker-cheap but $1.50/turn observed, and rework-heavy.
        rows.append(
            (day, "claude_code", "claude-haiku-4-5",
             _values(assistant_turns=14, cost_micro_usd=21_000_000,
                     tool_calls=70, rework_edits=35))
        )
    receipts = build_receipts(rows, TODAY)
    artifact = _artifact(
        [_FABLE_ROW, _market_row("claude_haiku")], [_verdict_card()]
    )
    board = build_advisor_board(receipts, artifact, TODAY)
    assert len(board.takes) == MAX_TAKES
    assert [take.take_id for take in board.takes] == [
        "premium-routing",
        "sticker-price-lie",
        "escalation-firing",
    ]
    assert board.takes[0].impact_micro_usd > 0


def test_determinism_same_inputs_same_board() -> None:
    artifact = _artifact([_FABLE_ROW, _market_row("claude_haiku")], [_verdict_card()])
    receipts = _routing_receipts()
    assert build_advisor_board(receipts, artifact, TODAY) == build_advisor_board(
        copy.deepcopy(receipts), artifact, TODAY
    )


def test_take_ids_are_pinned() -> None:
    assert ADVISOR_TAKE_IDS == (
        "premium-routing",
        "sticker-price-lie",
        "escalation-firing",
        "curated-verdict",
        "calm-default",
    )


def test_every_take_declares_how_far_it_can_be_trusted() -> None:
    """Ported from a Codex usage-coach report that grades every finding
    (2026-08-06 review). The receipts were always counted exactly; what was
    missing is the step between the count and the advice. A take that cannot
    state its own limit is asserting more than it measured, so the fields are
    required on the dataclass and the note must be real prose, not a label."""
    artifact = _artifact([_FABLE_ROW, _market_row("claude_haiku")], [_verdict_card()])
    boards = [
        build_advisor_board(_routing_receipts(), artifact, TODAY),
        build_advisor_board(_routing_receipts(), None, TODAY),
        build_advisor_board(_quiet_receipts(), artifact, TODAY),
        build_advisor_board(_quiet_receipts(), None, TODAY),
    ]
    seen: set[str] = set()
    for board in boards:
        for take in board.takes:
            seen.add(take.take_id)
            assert take.confidence in CONFIDENCE_LEVELS, take.take_id
            # A grade with no reasoning is the thing this replaces.
            assert len(take.confidence_note) > 60, take.take_id
            assert take.confidence_note.endswith("."), take.take_id
            assert take.confidence_note in ADVISOR_COPY.values(), take.take_id
    assert seen, "no takes exercised"


def test_the_take_with_no_local_receipts_is_graded_lowest() -> None:
    """The curated verdict is the only take built from someone else's claim
    rather than this machine's numbers. Its receipts tuple is empty, and the
    grade has to say so — otherwise a market note reads with the same weight
    as a measurement, which is exactly the confusion the grade exists to stop."""
    models = [_market_row("claude_haiku"), _market_row("claude_sonnet")]
    board = build_advisor_board(
        _quiet_receipts(), _artifact(models, [_verdict_card()]), TODAY
    )
    curated = [t for t in board.takes if t.take_id == "curated-verdict"]
    assert curated, "curated verdict did not fire"
    assert curated[0].receipts == ()
    assert curated[0].confidence == "low"
    assert "not measured on your machine" in curated[0].confidence_note
    # And the calm default, which IS a measured absence, outranks it.
    calm = [t for t in board.takes if t.take_id == "calm-default"]
    if calm:
        assert calm[0].confidence == "high"


def test_advisor_copy_passes_lexicon_and_leak_scans() -> None:
    slots = {
        "premium": "the Fable tier",
        "premium_top": "the Fable tier",
        "candidate": "the Haiku tier",
        "share_pct": 72,
        "cost": "$140",
        "turns_text": "312 assistant turns",
        "days_text": "18 active days",
        "candidate_cost": "$29.10",
        "saving": "$110",
        "ratio": "4.8",
        "rework_tenths": "12.4",
        "cheap": "the GPT mini tier",
        "rich": "the Sonnet tier",
        "cheap_cost": "$1.00",
        "rich_cost": "$0.50",
        "cheap_output": 5200,
        "rich_output": 1400,
        "cheap_turns": 80,
        "rich_turns": 64,
        "family": "the Haiku tier",
        "interruption_tenths": "3.2",
        "tool_calls": 210,
    }
    for key, template in sorted(ADVISOR_COPY.items()):
        text = template.format(**slots)
        assert lexicon_violations(text) == [], key
        assert leak_findings(text) == [], key


def test_routing_receipt_is_denominated_by_billing_mode() -> None:
    """The moat program forbids presenting list-price equivalence as actual
    spend. On a flat plan the marginal token is free, so the dollar ceiling is
    arithmetic about a bill that never arrives — the receipt says so and points
    at window headroom instead. It must never claim a model switch RESTORES
    access: limits are shared across models."""
    receipts = _routing_receipts()
    artifact = _artifact([_FABLE_ROW, _market_row("claude_haiku")])

    api = build_advisor_board(receipts, artifact, TODAY, billing_mode="api")
    sub = build_advisor_board(receipts, artifact, TODAY, billing_mode="subscription")

    api_take = next(t for t in api.takes if t.take_id == "premium-routing")
    sub_take = next(t for t in sub.takes if t.take_id == "premium-routing")

    api_ceiling = api_take.receipts[1]
    sub_ceiling = sub_take.receipts[1]
    assert "ceiling, not a promise" in api_ceiling
    assert "API-equivalent figures, not a bill you receive" in sub_ceiling
    assert "window headroom" in sub_ceiling
    # The mechanism claim stays honest: reach, never rescue.
    for banned in ("restores", "resets your limit", "unlocks"):
        assert banned not in sub_ceiling.lower()
    # Everything else about the take is unchanged by denomination.
    assert api_take.verdict == sub_take.verdict
    assert api_take.action == sub_take.action
    assert api_take.receipts[0] == sub_take.receipts[0]
    # An unknown mode keeps today's estimate framing.
    unknown = build_advisor_board(receipts, artifact, TODAY)
    unknown_take = next(t for t in unknown.takes if t.take_id == "premium-routing")
    assert unknown_take.receipts[1] == api_ceiling
