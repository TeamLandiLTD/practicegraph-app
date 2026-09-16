"""The Advisor fusion engine: receipts x market x curated verdicts -> takes.

Phase 3 of docs/ADVISOR_PLAN.md. Deterministic rules join the user's own
observed economics (advisor_receipts — the primary evidence, always cited
first) with the curated market artifact (advisor_market — corroboration,
attribution mandatory) and surface AT MOST three verdict-shaped takes.

The credibility mechanics are structural, not stylistic (plan §3):
receipts precede market lines; every take carries its sample basis; market
claims degrade honestly past artifact expiry (local numbers never rot);
sub-floor evidence renders the calm default, never a dressed-up guess; and
the anti-FOMO lane is a first-class outcome — "nothing worth switching for"
is a success state, not empty space.

Copy lives in the closed ADVISOR_COPY catalog (NFR-QLT-3, lexicon-scanned);
numbers enter through format slots only. Savings figures are labeled
ceilings — priced at listed rates over the user's own token volumes — never
promises. All arithmetic is integer (INV-6). Local-only forever (NFR-PRV-6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date

from practicegraph.analysis.advisor_market import (
    METRICS,
    AdvisorArtifact,
    CitedMeasurement,
    MarketModel,
    cited_measurements,
    evidence_attribution,
    evidence_expiry,
    market_stale,
    metric_basis,
    metric_current,
)
from practicegraph.analysis.advisor_receipts import FamilyEconomics, ReceiptsWindow
from practicegraph.store import Store
from practicegraph.wire import PREMIUM_FAMILIES

MAX_TAKES = 3

# Named thresholds (spec §0). Gaps must be decisive before a switch take
# fires — advice churn is worse than no advice (plan §4.2 decisive-gap rule).
ROUTING_MIN_SHARE_PCT = 50  # premium share of window spend (premium_heavy kin)
ROUTING_MIN_PRICE_RATIO = 2  # candidate must be >= 2x cheaper on both rates
ROUTING_MIN_IMPACT_MICRO_USD = 5_000_000  # < $5/window is noise, not a take
STICKER_LIE_MIN_RATIO_TENTHS = 15  # observed $/turn >= 1.5x despite cheaper rate
ESCALATION_MIN_TOOL_CALLS = 100  # volume floor before rework rates mean anything
ESCALATION_MIN_REWORK_TENTHS = 400  # >= 40 rework edits per 100 tool calls

# Closed take catalog — ids are the ranking tiebreak order.
ADVISOR_TAKE_IDS: tuple[str, ...] = (
    "premium-routing",
    "sticker-price-lie",
    "escalation-firing",
    "curated-verdict",
    "calm-default",
)

# Closed market states for the board header.
MARKET_STATES: tuple[str, ...] = ("fresh", "partial", "stale", "absent")

# How far a take can be trusted, strongest first. This grades the INFERENCE,
# not the arithmetic: every take's receipts are counted exactly, so the grade
# answers a different question — how much has to be assumed between the count
# and the advice. "high" is a measured fact plus a short step; "low" is a
# reading of numbers that could have several causes, or a claim not measured
# on this machine at all. Each take pairs its grade with the specific limit
# that earned it, because a bare label teaches nothing.
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")

# Display labels for closed wire families (plain nouns, no hype).
FAMILY_LABELS: dict[str, str] = {
    "claude_fable": "the Fable tier",
    "claude_mythos": "the Mythos tier",
    "claude_opus": "the Opus tier",
    "claude_sonnet": "the Sonnet tier",
    "claude_haiku": "the Haiku tier",
    "gpt_5_codex": "the Codex models",
    "gpt_5_mini": "the GPT mini tier",
    "gpt_5": "the GPT-5 tier",
    "gpt_4": "the GPT-4 tier",
    "o_series": "the o-series",
    "other": "unpriced models",
}

# Closed copy catalog (NFR-QLT-3): verdict + boundary welded per take, exact
# numbers through slots only, superlatives bounded in-sentence, one imperative
# action, one falsifiable experiment. Scanned by the lexicon + leak suites.
ADVISOR_COPY: dict[str, str] = {
    "premium-routing-verdict": (
        "Premium models carry most of your spend — test a cheaper model on routine work."
    ),
    "premium-routing-boundary": (
        "Use the cheaper model only where you can quickly check the result "
        "and revert a poor change."
    ),
    "premium-routing-receipt-spend": (
        "{premium} carried {share_pct}% of this window's estimated spend: "
        "{cost} over {turns_text} on {days_text}."
    ),
    "premium-routing-receipt-ceiling": (
        "The same token volume at {candidate} listed rates prices at "
        "{candidate_cost} — a {saving} ceiling, not a promise; your "
        "escalation trigger sets the real share."
    ),
    # API prices establish neither subscription billing nor window headroom.
    "premium-routing-receipt-ceiling-subscription": (
        "The same token volume at {candidate} listed rates prices at "
        "{candidate_cost} against {cost} — API-equivalent figures, not a "
        "bill you receive. These rates do not establish how a model change "
        "would affect your plan's window headroom."
    ),
    "premium-routing-market": (
        "{candidate} lists {ratio}x cheaper per output token than "
        "{premium_top} (corroboration, not the evidence)."
    ),
    "premium-routing-market-free": (
        "{candidate} lists no output-token charge under the cited API pricing conditions."
    ),
    "premium-routing-steelman": (
        "A lower token price can still cost more per task if it needs extra turns or more rework."
    ),
    "premium-routing-action": (
        "Try {candidate} on a small routine edit; compare the result before changing your default."
    ),
    "premium-routing-experiment": (
        "Route one week of mechanical work down and compare its rework rate "
        "to this window's {rework_tenths} per 100 tool calls."
    ),
    "premium-routing-experiment-zero": (
        "Route one week of mechanical work down and watch whether rework "
        "appears; none was measured on the premium tiers this window."
    ),
    "sticker-price-lie-verdict": (
        "{cheap} looks cheaper on the rate card but costs you more per turn "
        "— the listed price is not your price."
    ),
    "sticker-price-lie-boundary": (
        "This reads from your own turn economics; it flips back the moment "
        "the appetite changes."
    ),
    "sticker-price-lie-receipt-cheap": (
        "{cheap}: {cheap_cost} per priced turn observed, {cheap_output} "
        "output tokens per turn ({cheap_turns} turns)."
    ),
    "sticker-price-lie-receipt-rich": (
        "{rich}: {rich_cost} per priced turn observed, {rich_output} output "
        "tokens per turn ({rich_turns} turns)."
    ),
    "sticker-price-lie-market": (
        "On listed output rates {cheap} undercuts {rich} — your sessions say "
        "the token appetite eats the discount."
    ),
    "sticker-price-lie-action": (
        "Price work in cost per finished turn, not per token, when choosing "
        "between these two."
    ),
    "calm-default-verdict": (
        "No switch is worth making this week — the current routing holds."
    ),
    "calm-default-boundary": (
        "Re-checked as new market data and your own numbers land; a decisive "
        "gap is the bar."
    ),
    "calm-default-receipt": (
        "{turns_text} on {days_text} this window; estimated spend {cost}."
    ),
    "calm-default-action": "Keep the setup. Nothing to change today.",
    "escalation-firing-verdict": (
        "{family} is showing heavy rework on this window's volume — step that "
        "work up a tier."
    ),
    "escalation-firing-boundary": (
        "Applies to the work {family} is doing now; routine edits that land "
        "clean can stay down."
    ),
    "escalation-firing-receipt": (
        "{family}: {rework_tenths} rework edits per 100 tool calls across "
        "{tool_calls} tool calls; {interruption_tenths} interruptions per "
        "100 turns."
    ),
    "escalation-firing-action": (
        "Route this work type to {premium} for a week; keep {family} for the "
        "edits that land clean."
    ),
    "escalation-firing-experiment": (
        "Compare next window's rework rate on the stepped-up work against "
        "{rework_tenths} per 100 tool calls."
    ),
    # What each grade is answering: not "are the numbers right" — they are
    # counted — but "what had to be assumed to get from the count to the
    # advice". Each names the assumption plainly enough to be argued with.
    "premium-routing-confidence": (
        "Spend by tier and the turn counts are read straight from your logs. "
        "The ceiling reprices that same token volume at the lighter tier's "
        "listed rates, and a lighter tier may need more turns to reach the "
        "same place — nothing here replays the work to compare the results."
    ),
    "sticker-price-lie-confidence": (
        "Both per-turn figures are measured, over priced turns only. What "
        "they cannot separate is the work itself: the tier that lists cheaper "
        "may simply have been handed the longer jobs."
    ),
    "escalation-firing-confidence": (
        "The rework and interruption counts are exact; what caused them is "
        "not in the logs. A thin opening message leaves the same trace as a "
        "model working above its depth, and this cannot tell them apart."
    ),
    "curated-verdict-confidence": (
        "This one is not measured on your machine at all. It is a curated "
        "market note, shown because it touches a tool you use and has not "
        "expired — your own numbers played no part in it."
    ),
    "calm-default-confidence": (
        "Every routing rule ran against this window and none cleared its "
        "threshold, so this is a measured absence rather than an unexamined "
        "one. A window this short can still hide a pattern a longer one shows."
    ),
}


@dataclass(frozen=True, slots=True)
class AdvisorTake:
    """One rendered take: verdict-shaped, receipts-first, ranked by impact."""

    take_id: str
    tier: str  # advisor_market.VERDICT_TIERS value
    tool: str  # harness scope; "" = all
    verdict: str
    boundary: str
    receipts: tuple[str, ...]  # 1-3 lines, exact local numbers, sample basis
    market: str  # "" when the market layer is absent, stale, or unused
    steelman: str  # "" allowed
    action: str
    experiment: str  # "" allowed
    attribution: str  # Sources actually used by the take, else ""
    expires: str  # ISO date a market-backed claim expires, else ""
    impact_micro_usd: int  # estimated window ceiling driving rank; 0 = n/a
    # Both required, deliberately un-defaulted: a take that cannot say how far
    # it can be trusted has no business on the page, and a default would let
    # the next one ship silently without saying.
    confidence: str  # CONFIDENCE_LEVELS value
    confidence_note: str  # the specific limit that earned the grade
    evidence: tuple[CitedMeasurement, ...] = ()


@dataclass(frozen=True, slots=True)
class AdvisorBoard:
    """The Advisor surface for one window: header state + at most 3 takes.

    ``audit`` is the closed loop (plan §6): when a routing call surfaced
    weeks ago, this line reports what the person's own numbers did since —
    honestly in both directions. "" when there is nothing to audit yet."""

    as_of: str
    market_state: str  # MARKET_STATES value
    market_as_of: str  # "" when absent
    takes: tuple[AdvisorTake, ...]
    audit: str = ""


def _usd(micro: int) -> str:
    dollars, cents = divmod(micro, 1_000_000)
    if dollars >= 100:
        return f"${dollars:,}"
    return f"${dollars}.{cents // 10_000:02d}"


def _tenths(value: int) -> str:
    return f"{value // 10}.{value % 10}"


def _cap(text: str) -> str:
    """Sentence-case a formatted line whose leading slot may be lowercase."""
    return text[:1].upper() + text[1:] if text else text


def _label(family: str) -> str:
    return FAMILY_LABELS.get(family, FAMILY_LABELS["other"])


def _plural(n: int, unit: str) -> str:
    return f"{n:,} {unit}" if n == 1 else f"{n:,} {unit}s"


def _reprice_micro(economics: FamilyEconomics, market: MarketModel) -> int | None:
    """The user's observed window volume at a market row's listed rates.
    Unknown rates suppress the estimate only when that token category is used.
    Cache-write pricing is not in this contract; never substitute input pricing."""
    tokens = economics.tokens
    if tokens.cache_creation or tokens.cache_creation_1h:
        return None
    total = 0
    for count, metric, price in (
        (tokens.input, "price_in_micro", market.price_in_micro),
        (tokens.output, "price_out_micro", market.price_out_micro),
        (tokens.cached, "price_cache_read_micro", market.price_cache_read_micro),
    ):
        if not count:
            continue
        if price is None or metric_basis(market, metric) != metric_basis(market, "price_in_micro"):
            return None
        total += count * price
    return total // 1_000_000


def _market_rows(artifact: AdvisorArtifact | None, today: date) -> dict[str, MarketModel]:
    if artifact is None or market_stale(artifact, today):
        return {}
    return {
        model.family: replace(
            model,
            price_in_micro=model.price_in_micro
            if metric_current(model, "price_in_micro", today) else None,
            price_out_micro=model.price_out_micro
            if metric_current(model, "price_out_micro", today) else None,
            price_cache_read_micro=model.price_cache_read_micro
            if metric_current(model, "price_cache_read_micro", today) else None,
        )
        for model in artifact.models
    }


def _price_pair(model: MarketModel) -> tuple[int, int] | None:
    if model.price_in_micro is None or model.price_out_micro is None:
        return None
    if metric_basis(model, "price_in_micro") != metric_basis(model, "price_out_micro"):
        return None
    return model.price_in_micro, model.price_out_micro


def _premium_routing(
    receipts: ReceiptsWindow,
    rows: dict[str, MarketModel],
    expires: str,
    billing_mode: str = "unknown",
) -> AdvisorTake | None:
    # Premium spend is judged COMBINED across premium families (the
    # premium_heavy definition) — split spend between two premium tiers must
    # not slip under a per-family gate. Only market-priced, confident
    # families join the reprice set.
    price_pairs = {family: prices for family, row in rows.items()
                   if (prices := _price_pair(row)) is not None}
    premiums = [
        f
        for f in receipts.families
        if f.premium and f.confident and f.family in price_pairs
    ]
    if not premiums or receipts.total_cost_micro_usd <= 0:
        return None
    combined_cost = sum(f.cost_micro_usd for f in premiums)
    share_pct = combined_cost * 100 // receipts.total_cost_micro_usd
    if share_pct < ROUTING_MIN_SHARE_PCT:
        return None
    top = max(premiums, key=lambda f: (f.cost_micro_usd, f.family))
    # The candidate must be decisively cheaper than EVERY premium row in the
    # set — measured against the cheapest premium rates, not the priciest.
    bases = {metric_basis(rows[f.family], "price_in_micro") for f in premiums}
    if len(bases) != 1:
        return None
    min_out = min(price_pairs[f.family][1] for f in premiums)
    min_in = min(price_pairs[f.family][0] for f in premiums)
    candidates: list[tuple[int, str, MarketModel]] = []
    for family, (price_in, price_out) in price_pairs.items():
        row = rows[family]
        if (family in PREMIUM_FAMILIES
                or metric_basis(row, "price_in_micro") not in bases
                or price_out * ROUTING_MIN_PRICE_RATIO > min_out
                or price_in * ROUTING_MIN_PRICE_RATIO > min_in):
            continue
        costs = [_reprice_micro(f, row) for f in premiums]
        if any(cost is None for cost in costs):
            continue
        candidates.append((sum(cost for cost in costs if cost is not None), family, row))
    if not candidates:
        return None
    # Compare supported prices, never rank missing or incompatible benchmark scores.
    candidate_cost, _, candidate = min(candidates, key=lambda item: (item[0], item[1]))
    saving = combined_cost - candidate_cost
    if saving < ROUTING_MIN_IMPACT_MICRO_USD:
        return None
    candidate_out = price_pairs[candidate.family][1]
    ratio_tenths = (
        price_pairs[top.family][1] * 10 // candidate_out
        if candidate_out > 0
        else 0
    )
    combined_tool_calls = sum(f.tool_calls for f in premiums)
    combined_rework = sum(f.rework_edits for f in premiums)
    slots = {
        "premium": (
            _label(top.family) if len(premiums) == 1 else "the premium tiers"
        ),
        "premium_top": _label(top.family),
        "candidate": _label(candidate.family),
        "share_pct": share_pct,
        "cost": _usd(combined_cost),
        "turns_text": _plural(
            sum(f.assistant_turns for f in premiums), "assistant turn"
        ),
        "days_text": _plural(
            max(f.active_days for f in premiums), "active day"
        ),
        "candidate_cost": _usd(candidate_cost),
        "saving": _usd(saving),
        "ratio": _tenths(ratio_tenths),
        "rework_tenths": _tenths(
            combined_rework * 1000 // combined_tool_calls
            if combined_tool_calls > 0
            else 0
        ),
    }
    tools = {f.tool for f in premiums}
    evidence = tuple(
        citation for f in premiums
        for citation in cited_measurements(
            rows[f.family], ("price_in_micro", "price_out_micro"),
        )
    ) + cited_measurements(
        candidate,
        ("price_in_micro", "price_out_micro", "price_cache_read_micro")
        if any(f.tokens.cached for f in premiums) else ("price_in_micro", "price_out_micro"),
    )
    return AdvisorTake(
        take_id="premium-routing",
        tier="try",
        tool=top.tool if len(tools) == 1 else "",
        verdict=ADVISOR_COPY["premium-routing-verdict"].format(**slots),
        boundary=ADVISOR_COPY["premium-routing-boundary"].format(**slots),
        receipts=(
            _cap(ADVISOR_COPY["premium-routing-receipt-spend"].format(**slots)),
            _cap(
                ADVISOR_COPY[
                    "premium-routing-receipt-ceiling-subscription"
                    if billing_mode == "subscription"
                    else "premium-routing-receipt-ceiling"
                ].format(**slots)
            ),
        ),
        market=_cap(ADVISOR_COPY[
            "premium-routing-market" if candidate_out else "premium-routing-market-free"
        ].format(**slots)),
        steelman=ADVISOR_COPY["premium-routing-steelman"].format(**slots),
        action=_cap(ADVISOR_COPY["premium-routing-action"].format(**slots)),
        experiment=(
            ADVISOR_COPY["premium-routing-experiment"].format(**slots)
            if combined_rework > 0
            else ADVISOR_COPY["premium-routing-experiment-zero"]
        ),
        attribution=evidence_attribution(evidence),
        evidence=evidence,
        expires=evidence_expiry(expires, evidence),
        impact_micro_usd=saving,
        # Measured spend, one modelled step: the ceiling assumes the token
        # volume survives the move to a lighter tier.
        confidence="medium",
        confidence_note=ADVISOR_COPY["premium-routing-confidence"],
    )


def _sticker_price_lie(
    receipts: ReceiptsWindow, rows: dict[str, MarketModel], expires: str,
) -> AdvisorTake | None:
    worst: tuple[int, FamilyEconomics, FamilyEconomics] | None = None
    confident = [
        f for f in receipts.families if f.confident and f.priced_turns > 0
    ]
    for cheap in confident:
        cheap_row = rows.get(cheap.family)
        if cheap_row is None or cheap_row.price_out_micro is None:
            continue
        for rich in confident:
            if rich.tool != cheap.tool or rich.family == cheap.family:
                continue
            rich_row = rows.get(rich.family)
            if rich_row is None or rich_row.price_out_micro is None:
                continue
            if (metric_basis(cheap_row, "price_out_micro")
                    != metric_basis(rich_row, "price_out_micro")):
                continue
            if cheap_row.price_out_micro >= rich_row.price_out_micro:
                continue  # "cheap" must actually list cheaper
            if rich.cost_per_priced_turn_micro_usd <= 0:
                continue
            ratio_tenths = (
                cheap.cost_per_priced_turn_micro_usd
                * 10
                // rich.cost_per_priced_turn_micro_usd
            )
            if ratio_tenths < STICKER_LIE_MIN_RATIO_TENTHS:
                continue
            if worst is None or ratio_tenths > worst[0]:
                worst = (ratio_tenths, cheap, rich)
    if worst is None:
        return None
    _, cheap, rich = worst
    evidence = tuple(
        citation for family in (cheap.family, rich.family)
        for citation in cited_measurements(rows[family], ("price_out_micro",))
    )
    slots = {
        "cheap": _label(cheap.family),
        "rich": _label(rich.family),
        "cheap_cost": _usd(cheap.cost_per_priced_turn_micro_usd),
        "rich_cost": _usd(rich.cost_per_priced_turn_micro_usd),
        "cheap_output": cheap.output_tokens_per_turn,
        "rich_output": rich.output_tokens_per_turn,
        "cheap_turns": cheap.priced_turns,
        "rich_turns": rich.priced_turns,
    }
    return AdvisorTake(
        take_id="sticker-price-lie",
        tier="watch",
        tool=cheap.tool,
        verdict=_cap(ADVISOR_COPY["sticker-price-lie-verdict"].format(**slots)),
        boundary=ADVISOR_COPY["sticker-price-lie-boundary"].format(**slots),
        receipts=(
            _cap(ADVISOR_COPY["sticker-price-lie-receipt-cheap"].format(**slots)),
            _cap(ADVISOR_COPY["sticker-price-lie-receipt-rich"].format(**slots)),
        ),
        market=ADVISOR_COPY["sticker-price-lie-market"].format(**slots),
        steelman="",
        action=ADVISOR_COPY["sticker-price-lie-action"].format(**slots),
        experiment="",
        attribution=evidence_attribution(evidence),
        evidence=evidence,
        expires=evidence_expiry(expires, evidence),
        impact_micro_usd=0,
        # Two exact per-turn figures, but a per-turn average cannot see the
        # difficulty of the turns it averages.
        confidence="medium",
        confidence_note=ADVISOR_COPY["sticker-price-lie-confidence"],
    )


def _escalation_firing(receipts: ReceiptsWindow) -> AdvisorTake | None:
    has_premium = any(f.premium for f in receipts.families)
    if not has_premium:
        return None
    candidates = [
        f
        for f in receipts.families
        if f.confident
        and not f.premium
        and f.tool_calls >= ESCALATION_MIN_TOOL_CALLS
        and f.rework_per_100_tool_calls_tenths >= ESCALATION_MIN_REWORK_TENTHS
    ]
    if not candidates:
        return None
    family = max(
        candidates,
        key=lambda f: (f.rework_per_100_tool_calls_tenths, f.family),
    )
    premium = max(
        (f for f in receipts.families if f.premium),
        key=lambda f: (f.cost_micro_usd, f.family),
    )
    slots = {
        "family": _label(family.family),
        "premium": _label(premium.family),
        "rework_tenths": _tenths(family.rework_per_100_tool_calls_tenths),
        "interruption_tenths": _tenths(family.interruptions_per_100_turns_tenths),
        "tool_calls": family.tool_calls,
    }
    return AdvisorTake(
        take_id="escalation-firing",
        tier="try",
        tool=family.tool,
        verdict=_cap(ADVISOR_COPY["escalation-firing-verdict"].format(**slots)),
        boundary=ADVISOR_COPY["escalation-firing-boundary"].format(**slots),
        receipts=(_cap(ADVISOR_COPY["escalation-firing-receipt"].format(**slots)),),
        market="",
        steelman="",
        action=ADVISOR_COPY["escalation-firing-action"].format(**slots),
        experiment=ADVISOR_COPY["escalation-firing-experiment"].format(**slots),
        attribution="",
        expires="",
        impact_micro_usd=0,
        # The longest inference chain on the board: rework is counted, but
        # its cause is not observable and has at least two candidates.
        confidence="low",
        confidence_note=ADVISOR_COPY["escalation-firing-confidence"],
    )


def _curated_verdict(
    receipts: ReceiptsWindow, artifact: AdvisorArtifact | None, today: date
) -> AdvisorTake | None:
    if artifact is None or market_stale(artifact, today):
        return None
    user_tools = {f.tool for f in receipts.families if f.assistant_turns > 0}
    user_families = {f.family for f in receipts.families if f.assistant_turns > 0}
    for verdict in artifact.verdicts:
        if today < verdict.as_of or today > verdict.expires:
            continue
        rows = {row.family: row for row in artifact.models}
        if any(not metric_current(rows[family], metric, today)
               for family, metric in verdict.evidence):
            continue
        if not user_tools.intersection(verdict.tools):
            continue
        if verdict.tier != "watch" and not user_families.intersection(
            verdict.families
        ):
            continue
        evidence = tuple(
            citation for family, metric in verdict.evidence
            for citation in cited_measurements(rows[family], (metric,))
        )
        return AdvisorTake(
            take_id="curated-verdict",
            tier=verdict.tier,
            tool="" if len(verdict.tools) > 1 else verdict.tools[0],
            verdict=verdict.verdict,
            boundary=verdict.boundary,
            receipts=(),
            market="",
            steelman=verdict.steelman,
            action=verdict.action,
            experiment=verdict.experiment,
            attribution="Guidance: TeamLandi" + (
                " · " + evidence_attribution(evidence) if evidence else ""
            ),
            evidence=evidence,
            expires=evidence_expiry(min(verdict.expires, artifact.expires).isoformat(), evidence),
            impact_micro_usd=0,
            # The only take with no local receipts at all — relevance-gated,
            # but nothing here was measured on this machine.
            confidence="low",
            confidence_note=ADVISOR_COPY["curated-verdict-confidence"],
        )
    return None


def _calm_default(receipts: ReceiptsWindow) -> AdvisorTake:
    slots = {
        "turns_text": _plural(receipts.total_assistant_turns, "assistant turn"),
        "days_text": _plural(
            max((f.active_days for f in receipts.families), default=0),
            "active day",
        ),
        "cost": _usd(receipts.total_cost_micro_usd),
    }
    return AdvisorTake(
        take_id="calm-default",
        tier="wait",
        tool="",
        verdict=ADVISOR_COPY["calm-default-verdict"],
        boundary=ADVISOR_COPY["calm-default-boundary"],
        receipts=(ADVISOR_COPY["calm-default-receipt"].format(**slots),),
        market="",
        steelman="",
        action=ADVISOR_COPY["calm-default-action"],
        experiment="",
        attribution="",
        expires="",
        impact_micro_usd=0,
        # "Nothing to change" is itself a result: every rule ran and none
        # cleared its frozen threshold.
        confidence="high",
        confidence_note=ADVISOR_COPY["calm-default-confidence"],
    )


def build_advisor_board(
    receipts: ReceiptsWindow,
    artifact: AdvisorArtifact | None,
    today: date,
    audit: str = "",
    billing_mode: str = "unknown",
) -> AdvisorBoard:
    """Pure fusion: same inputs, same board (INV-6). At most MAX_TAKES takes,
    ranked by estimated impact then catalog order; the calm default renders
    exactly when nothing decisive fired.

    `billing_mode` decides the denomination of the routing receipt: on a flat
    plan the dollar figures are API-equivalent, never a bill, and the honest
    gain is window headroom (see the ceiling copy)."""
    if artifact is None:
        market_state, market_as_of = "absent", ""
    elif market_stale(artifact, today):
        market_state, market_as_of = "stale", artifact.as_of.isoformat()
    else:
        partial = any(not metric_current(model, metric, today)
                      for model in artifact.models for metric in METRICS)
        market_state = "partial" if partial else "fresh"
        market_as_of = artifact.as_of.isoformat()

    rows = _market_rows(artifact, today)
    expires = artifact.expires.isoformat() if artifact is not None and rows else ""

    candidates = [
        take
        for take in (
            _premium_routing(receipts, rows, expires, billing_mode),
            _sticker_price_lie(receipts, rows, expires),
            _escalation_firing(receipts),
            _curated_verdict(receipts, artifact, today),
        )
        if take is not None
    ]
    candidates.sort(
        key=lambda take: (
            -take.impact_micro_usd,
            ADVISOR_TAKE_IDS.index(take.take_id),
        )
    )
    takes = tuple(candidates[:MAX_TAKES]) or (_calm_default(receipts),)
    return AdvisorBoard(
        as_of=receipts.end_day.isoformat(),
        market_state=market_state,
        market_as_of=market_as_of,
        takes=takes,
        audit=audit,
    )


# Findings a surfaced take already speaks for. Other surfaces (the skills
# shelf) subtract these so the same fact never reads twice in two voices on
# one page — the same precedence discipline the takes hold over findings.
ADVISOR_TAKE_FINDINGS: dict[str, tuple[str, ...]] = {
    "premium-routing": ("premium_heavy",),
    "sticker-price-lie": (),
    "escalation-firing": ("rework_heavy",),
    "curated-verdict": (),
    "calm-default": (),
}


def covered_findings(board: AdvisorBoard | None) -> set[str]:
    """Finding ids the board's surfaced takes already carry."""
    if board is None:
        return set()
    covered: set[str] = set()
    for take in board.takes:
        covered.update(ADVISOR_TAKE_FINDINGS.get(take.take_id, ()))
    return covered


# ---- the closed loop (plan §6): the Advisor audits its own past calls -------
# One live ledger entry at a time, written ONLY in the agent tick (the
# coach_ledger discipline); the audit line is composed purely on read. The
# tracked quantity is the combined premium share of window spend — the same
# number both when recorded and when audited, receipts-only, so the loop
# never depends on the market artifact being present later.

ADVISOR_LEDGER_KEY = "advisor_ledger:v1"

AUDIT_MIN_DAYS = 14  # youngest a call may be before its numbers mean anything
AUDIT_HOLD_DAYS = 28  # after this, an unmoved mix is reported, not waited on
AUDIT_RETIRE_DAYS = 56  # entries older than this retire unconditionally
AUDIT_MIN_DROP_PCT = 5  # points of premium-share drop that count as movement

AUDIT_COPY: dict[str, str] = {
    # AM-4: the moved slice is stated in the person's own money — the share
    # points that left the premium tiers, priced over THIS window's spend. A
    # factual reallocation, never a counterfactual "you saved" claim.
    "improved": (
        "Since {date}, premium models' share of your spend fell from {then}% "
        "to {now}% — about {moved} of this window's spend now runs on the "
        "cheaper tiers."
    ),
    "unmoved": (
        "Since {date}, premium models' share of your spend went from {then}% "
        "to {now}% — the mix has not come down, so the call stands until the "
        "routing moves."
    ),
}


def premium_share_pct(receipts: ReceiptsWindow) -> int:
    """Combined premium share of window spend — the audited quantity."""
    if receipts.total_cost_micro_usd <= 0:
        return 0
    premium_cost = sum(
        f.cost_micro_usd for f in receipts.families if f.premium
    )
    return premium_cost * 100 // receipts.total_cost_micro_usd


def _read_ledger(store: Store) -> dict[str, object] | None:
    raw = store.meta_get(ADVISOR_LEDGER_KEY)
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("day"), str)
        or not isinstance(entry.get("share_pct"), int)
    ):
        return None
    try:
        date.fromisoformat(entry["day"])
    except ValueError:
        return None
    return entry


def advisor_audit(store: Store, receipts: ReceiptsWindow, today: date) -> str:
    """Pure read: the audit line for a recorded routing call, or "".

    Improvement reports as soon as the drop is decisive and the entry is old
    enough to mean something; an unmoved mix reports only after the hold
    window — patience first, honesty after."""
    entry = _read_ledger(store)
    if entry is None:
        return ""
    recorded = date.fromisoformat(str(entry["day"]))
    age = (today - recorded).days
    if age < AUDIT_MIN_DAYS or age > AUDIT_RETIRE_DAYS:
        return ""
    share = entry["share_pct"]
    if not isinstance(share, int):
        return ""
    then = share
    now = premium_share_pct(receipts)
    if then - now >= AUDIT_MIN_DROP_PCT:
        # The moved slice in the person's own money (AM-4): the dropped share
        # points priced over this window's total spend — reallocation stated
        # as a fact, never a counterfactual saving.
        moved = (then - now) * receipts.total_cost_micro_usd // 100
        return AUDIT_COPY["improved"].format(
            date=recorded.isoformat(), then=then, now=now, moved=_usd(moved)
        )
    if age >= AUDIT_HOLD_DAYS:
        return AUDIT_COPY["unmoved"].format(
            date=recorded.isoformat(), then=then, now=now
        )
    return ""


def record_advisor_ledger(
    store: Store, board: AdvisorBoard, receipts: ReceiptsWindow, today: date
) -> None:
    """Tick-only write path (the coach-ledger discipline). Retires an entry
    once its audit window has fully passed; records a new one when the
    routing call is on the board and nothing is being tracked."""
    entry = _read_ledger(store)
    if entry is not None:
        recorded = date.fromisoformat(str(entry["day"]))
        if (today - recorded).days > AUDIT_RETIRE_DAYS:
            store.meta_set(ADVISOR_LEDGER_KEY, "")
            entry = None
    if entry is None and any(
        take.take_id == "premium-routing" for take in board.takes
    ):
        store.meta_set(
            ADVISOR_LEDGER_KEY,
            json.dumps(
                {
                    "take_id": "premium-routing",
                    "day": today.isoformat(),
                    "share_pct": premium_share_pct(receipts),
                },
                sort_keys=True,
            ),
        )
