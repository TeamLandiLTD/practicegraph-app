"""The economy reading (W1.1): capability per dollar, one lever, calm.

The daily money glance the cost engine already earns but never composed: one
window headline (spend, cost per priced turn, cache reuse), the week-over-week
direction, and THE ONE LEVER worth a look — or the calm line, a first-class
success state (the Advisor's calm-default discipline, applied to spend).

Dual denomination. Heavy subscription users
pay a flat fee — their marginal token cost is zero and their scarce resource is
the provider window, not dollars. The reading therefore carries a billing mode:
``api`` leads with dollars; ``subscription`` leads with the weekly rate-limit
window share and labels every dollar figure API-equivalent (moat program F3.4:
list-price equivalence is never presented as actual spend); ``unknown`` behaves
like today's surfaces (estimate framing).

Advisor precedence, structurally. Premium-share-of-spend is the Advisor's fact
(its premium-routing take carries receipts, market corroboration, and decisive
gates). This module NEVER voices premium share — when the board carries any
decisive take the lever is a closed pointer to the Advisor card, and when the
board is calm, re-flagging premium share here would contradict the calm verdict
on the same page. The levers minted here are the usage-hygiene facts no take
covers: cache reuse, context weight, unpriced turns.

First-open verdict (AM-3): until the person dismisses it once, the reading
carries a retrospective line over the last RETRO_WINDOW_DAYS of already-ingested
history — the activation moment only a log-reading product can offer. The
dismissal flag lives in the meta table, written only via the UI action path.

Honesty rules: every figure is an estimate at listed rates over the person's own
volumes (FR-ANL-1 framing lives in the closed copy); money is integer micro-USD
(INV-6, no floats); thin evidence yields ``available=False`` and the surfaces
hide the card (withheld, not zero); copy slots carry integers or
integer-derived formatted numbers (the advisor precedent), never free text.
Local-only forever (NFR-PRV-6): nothing here may feed an emit — the wire scan
pins the vocabulary below.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from practicegraph.analysis.advisor import AdvisorBoard
from practicegraph.analysis.advisor_receipts import ReceiptsWindow
from practicegraph.analysis.insights import (
    CONTEXT_BLOAT_AVG_PROMPT_TOKENS,
    LOW_CACHE_MAX_PCT,
)
from practicegraph.analysis.ratecard import MICRO_PER_TOKEN_UNIT, rate_for_model
from practicegraph.analysis.runway import WINDOW_WEEK_MINUTES
from practicegraph.history import WeekOverWeek
from practicegraph.report.format import compact, count
from practicegraph.store import ContributionRow, Store

# The wire field-name scan pins these out of any payload (test_wire).
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "economy_reading",
    "billing_mode",
    "lever",
    "retro",
    "week_window",
    "churn",
    "denominator",
    "landed",
    "per_commit",
)

# Closed billing modes (AM-1). Must stay a subset of config.BILLING_MODES —
# pinned by test. Anything unrecognised degrades to "unknown", never an error.
ECONOMY_BILLING_MODES: tuple[str, ...] = ("unknown", "subscription", "api", "mixed")

# The advisor's calm take id — pinned by test against analysis/advisor.py so a
# rename there cannot silently break the precedence rule here.
ADVISOR_CALM_TAKE_ID = "calm-default"

# Evidence floors (named constants, spec §0 rule). The turn floor mirrors
# RECEIPTS_MIN_TURNS; below it the whole reading is withheld. The cache lever
# needs real window volume before a reuse percentage means anything (the
# daily detector's floor, scaled to a 28-day window). The unpriced lever fires
# only when enough turns are invisible to understate spend materially.
ECONOMY_MIN_PRICED_TURNS = 40
ECONOMY_CACHE_MIN_PROMPT_TOKENS = 200_000
ECONOMY_UNPRICED_MIN_TURNS = 20
# Week-over-week renders only when BOTH weeks have this many active days —
# a vacation week never reads as a spend drop (withheld, not zero).
ECONOMY_WOW_MIN_ACTIVE_DAYS = 3

# Reasoning-share attribution (W1.2). Reasoning tokens are ALREADY billed
# inside output (ratecard.estimate_cost_micro_usd) — this is an attribution of
# counted spend, NEVER an addition; a property test pins that the total is
# unchanged. The lever joins the ladder only past this share of output spend.
ECONOMY_REASONING_LEVER_MIN_PCT = 25

# Cache churn (W3.2). Cache spend splits into reading context back (cheap)
# and re-WRITING it after a restart or idle expiry (the write premium). On the
# reference 88-day store the write side was 26% of cache spend — and the
# enterprise-overspend synthesis (2026-07-27) flags exactly this mechanic as
# the one nobody models; our data independently replicated its headline
# (re-sent context 63.8% of the bill vs the report's 62%). The fact line
# renders once cache spend clears the floor (a share of pennies is noise);
# the lever joins the ladder only when re-writing rivals reading — churning.
ECONOMY_CHURN_MIN_CACHE_MICRO_USD = 1_000_000  # $1 at listed rates
ECONOMY_CHURN_LEVER_MIN_PCT = 40

# The denominator (W3.1): cost per commit the
# person's sessions ran. ATTEMPTS, counted at the moment the commit command
# runs — v1 pairs no outcomes, and the copy says so. Below the commit floor a
# percentage-of-nothing is withheld; the prior-window comparison additionally
# needs the prior window past the same floor (a vacation month is not a
# trend). This is the number the overspend synthesis calls the missing
# denominator, at the only grain we serve: one person, their own month.
DENOMINATOR_MIN_COMMITS = 10

# First-open retrospective (AM-3).
RETRO_WINDOW_DAYS = 90
ECONOMY_INTRO_META_KEY = "economy_intro_dismissed:v1"

# Subscription lens: how far back the weekly rate-limit gauge may be read.
WEEK_GAUGE_LOOKBACK_DAYS = 7

# Closed copy catalog (NFR-QLT-3, lexicon-scanned). Calm register, no
# exclamation marks, no guilt vocabulary — usage is accounting, never failure
# (PRINCIPLES §1). Slots hold integers / formatted integers only.
ECONOMY_COPY: dict[str, str] = {
    "eyebrow": "Spend",
    "title-api": "Estimated model usage",
    "title-subscription": "API-equivalent usage",
    "title-unknown": "Model usage at listed rates",
    "title-mixed": "Model usage at listed rates",
    "label-spend": "listed-rate estimate",
    "label-per-turn": "per turn",
    "label-cache": "served from cache",
    "label-week-window": "of the week's allowance",
    "label-wow": "against last week",
    "note-estimate": "Estimates at listed rates over your own volumes.",
    "note-api-equivalent": "On a subscription these compare usage at listed API rates; "
    "they are not subscription charges.",
    "lever-advisor-title": "Model guidance worth a look",
    "lever-advisor-body": "One model choice deserves a look this window. The Advice section "
    "explains the observation and the published guidance.",
    "lever-cache-title": "Review context reuse",
    "lever-cache-body": "{pct}% of recorded prompt tokens came from cache. Check the context being "
    "resent before changing how you use sessions.",
    "lever-reasoning-title": "Reasoning is a usage driver",
    "lever-reasoning-body": "{pct}% of estimated output cost was attributed to reasoning. "
    "You could try a lower effort on routine work and check the result.",
    "lever-context-title": "Heavy context per turn",
    "lever-context-body": "Each turn carried about {tokens} prompt tokens. Trimming stale files "
    "from the working set lowers every later turn.",
    "lever-unpriced-title": "Some spend is invisible",
    "lever-unpriced-body": "{turns} turns used models the rate card cannot price, so the figures "
    "here are understated.",
    "lever-calm-title": "No clear usage change to suggest",
    "lever-calm-body": "These usage records do not point to a specific change this window.",
    "reasoning-line": "About {pct}% of estimated output cost was attributed to reasoning, already "
    "included in the total.",
    "label-reasoning": "reasoning share",
    "churn-line": "Of the cache spend, {read_usd} read context back and {creation_usd} wrote it in "
    "- part of the total, not extra.",
    "label-churn": "re-written into cache",
    "denominator-line": "About {per} per commit command: {commits} commands against "
    "{spend} in listed-rate usage. Commands do not establish completed work.",
    "denominator-trend": " Last window it was about {prior}.",
    "label-denominator": "per commit",
    "denominator-withheld": "Per-commit spend shows from {floor} commits; this window had "
    "{commits}. Work that ships as documents has no commit to count.",
    "lever-churn-title": "Some context is written again",
    "lever-churn-body": "{pct}% of estimated cache cost went to writing context. Review repeated "
    "context before deciding whether to change the setup.",
    "retro-title": "Your first reading",
    "retro-line": "Over the last {days} days: {turns} recorded assistant turns on {active} days, "
    "equivalent to about {usd} at listed API rates.",
}


@dataclass(frozen=True, slots=True)
class EconomyLever:
    """The one lever worth a look this window — closed id, minted copy, and
    the integer that triggered it (0 for the pointer and calm states)."""

    lever_id: str  # advisor | cache | churn | reasoning | context | unpriced | calm
    title: str
    body: str
    metric: int


@dataclass(frozen=True, slots=True)
class EconomyReasoning:
    """The reasoning slice of output spend (W1.2): an attribution of money
    already counted in the window total — never an added charge. Priced at
    the active card's listed output rates over the window's per-model rows
    (the same at-listed-rates framing every estimate carries)."""

    share_pct: int  # of the window's priced output spend
    cost_micro_usd: int  # the attributed slice (inside the total, not extra)
    output_cost_micro_usd: int  # the priced output spend it is a share of
    line: str


@dataclass(frozen=True, slots=True)
class EconomyChurn:
    """The cache-spend split (W3.2): reading context back versus re-writing
    it after restarts and idle expiries. Like the reasoning slice, this is an
    attribution of money already inside the window total — never an added
    charge — priced per model row at the active card's listed cache rates
    with the same 5m/1h write split the estimator itself uses."""

    share_pct: int  # creation share of (read + creation) cache spend
    creation_cost_micro_usd: int
    read_cost_micro_usd: int
    line: str


@dataclass(frozen=True, slots=True)
class EconomyDenominator:
    """Cost per commit attempt (W3.1) — the person's own denominator. The
    prior-window figure rides along only when that window also clears the
    commit floor; the line is minted once, trend clause included, so the
    surface never assembles copy."""

    per_commit_micro_usd: int
    commit_attempts: int
    prior_per_commit_micro_usd: int | None
    line: str


@dataclass(frozen=True, slots=True)
class EconomyRetro:
    """The first-open retrospective (AM-3): the person's own last-N-days
    numbers, present only until dismissed once. Never a judgment — a fact."""

    days: int
    active_days: int
    assistant_turns: int
    cost_micro_usd: int
    line: str


@dataclass(frozen=True, slots=True)
class EconomyReading:
    """One window's economy read, render-ready. ``available=False`` means the
    evidence floor was not met and every surface hides the card entirely."""

    available: bool
    billing_mode: str  # ECONOMY_BILLING_MODES value
    window_days: int
    active_days: int
    cost_micro_usd: int
    priced_turns: int
    cost_per_priced_turn_micro_usd: int
    cache_hit_pct: int
    wow_cost_delta_pct: int | None  # None when either week is too thin
    week_window_pct: int | None  # subscription lens; latest weekly gauge
    reasoning: EconomyReasoning | None  # W1.2 attribution; None when absent
    churn: EconomyChurn | None  # W3.2 cache-spend split; None when thin
    denominator: EconomyDenominator | None  # W3.1; None below the floor
    lever: EconomyLever | None
    retro: EconomyRetro | None
    # The sentence that stands in the denominator's slot when it is withheld
    # ("" when the denominator is present). Explained absence (W0.4).
    denominator_note: str = ""


_UNAVAILABLE = EconomyReading(
    available=False,
    billing_mode="unknown",
    window_days=0,
    active_days=0,
    cost_micro_usd=0,
    priced_turns=0,
    cost_per_priced_turn_micro_usd=0,
    cache_hit_pct=0,
    wow_cost_delta_pct=None,
    week_window_pct=None,
    reasoning=None,
    churn=None,
    denominator=None,
    lever=None,
    retro=None,
)


def _usd(micro: int) -> str:
    """Money for copy slots: integer micro-USD to $D.CC, whole dollars with a
    thousands separator once cents stop mattering (the advisor's formatter)."""
    dollars, cents = divmod(micro, 1_000_000)
    if dollars >= 100:
        return f"${dollars:,}"
    return f"${dollars}.{cents // 10_000:02d}"


def _pct(numerator: int, denominator: int) -> int:
    return numerator * 100 // denominator if denominator > 0 else 0


def _reasoning(store: Store, today: date, window_days: int) -> EconomyReasoning | None:
    """The reasoning slice of the window's priced output spend, attributed
    per model row at the active card's listed output rate. Reasoning tokens
    are a subset of billed output per turn, so the row-level clamp below only
    guards malformed data — it never re-prices anything. None when the window
    priced no output or saw no reasoning tokens (withheld, not zero)."""
    start = (today - timedelta(days=window_days - 1)).isoformat()
    output_cost = 0
    reasoning_cost = 0
    for _day, _tool, model, values in store.usage_rows_by_day(start, today.isoformat()):
        rate = rate_for_model(model)
        if rate is None:
            continue
        row = ContributionRow.from_values(values)
        output_cost += row.output_tokens * rate.output // MICRO_PER_TOKEN_UNIT
        clamped = min(row.reasoning_tokens, row.output_tokens)
        reasoning_cost += clamped * rate.output // MICRO_PER_TOKEN_UNIT
    if output_cost <= 0 or reasoning_cost <= 0:
        return None
    share = _pct(reasoning_cost, output_cost)
    return EconomyReasoning(
        share_pct=share,
        cost_micro_usd=reasoning_cost,
        output_cost_micro_usd=output_cost,
        line=ECONOMY_COPY["reasoning-line"].format(pct=share),
    )


def _churn(store: Store, today: date, window_days: int) -> EconomyChurn | None:
    """The cache-spend split for the window, priced per model row: reads at
    the card's cache_read rate; writes split 5m/1h with the SAME clamp the
    cost estimator uses (creation_1h is stored as a subset of creation, and
    malformed rows clamp rather than double-count). Rows the card cannot
    price contribute nothing here and nothing to priced spend — consistent
    by construction. None below the spend floor (withheld, not zero)."""
    start = (today - timedelta(days=window_days - 1)).isoformat()
    read_cost = 0
    creation_cost = 0
    for _day, _tool, model, values in store.usage_rows_by_day(start, today.isoformat()):
        rate = rate_for_model(model)
        if rate is None:
            continue
        row = ContributionRow.from_values(values)
        creation_1h = min(row.cache_creation_1h_tokens, row.cache_creation_tokens)
        creation_5m = row.cache_creation_tokens - creation_1h
        read_cost += row.cached_tokens * rate.cache_read // MICRO_PER_TOKEN_UNIT
        creation_cost += (
            creation_5m * rate.cache_creation + creation_1h * rate.cache_creation_1h
        ) // MICRO_PER_TOKEN_UNIT
    cache_cost = read_cost + creation_cost
    if cache_cost < ECONOMY_CHURN_MIN_CACHE_MICRO_USD:
        return None
    share = _pct(creation_cost, cache_cost)
    return EconomyChurn(
        share_pct=share,
        creation_cost_micro_usd=creation_cost,
        read_cost_micro_usd=read_cost,
        line=ECONOMY_COPY["churn-line"].format(
            read_usd=_usd(read_cost), creation_usd=_usd(creation_cost)
        ),
    )


def _window_commits_and_cost(store: Store, start: date, end: date) -> tuple[int, int]:
    """(commit_attempts, priced cost) summed over an inclusive day range —
    the stored totals, no re-pricing."""
    commits = 0
    cost = 0
    for _day, _tool, _model, values in store.usage_rows_by_day(start.isoformat(), end.isoformat()):
        row = ContributionRow.from_values(values)
        commits += row.git_commit_attempts
        cost += row.cost_micro_usd
    return commits, cost


def _denominator(
    store: Store, today: date, window_days: int, window_cost: int
) -> EconomyDenominator | None:
    """Cost per commit attempt for the window, with the prior window beside
    it when that window also clears the floor. `window_cost` is the receipts
    total the headline already shows — the same dollars, divided, never a
    second derivation."""
    start = today - timedelta(days=window_days - 1)
    commits, _ = _window_commits_and_cost(store, start, today)
    if commits < DENOMINATOR_MIN_COMMITS or window_cost <= 0:
        return None
    per = window_cost // commits
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=window_days - 1)
    prior_commits, prior_cost = _window_commits_and_cost(store, prior_start, prior_end)
    prior_per: int | None = None
    if prior_commits >= DENOMINATOR_MIN_COMMITS and prior_cost > 0:
        prior_per = prior_cost // prior_commits
    line = ECONOMY_COPY["denominator-line"].format(
        per=_usd(per), commits=count(commits), spend=_usd(window_cost)
    )
    if prior_per is not None:
        line += ECONOMY_COPY["denominator-trend"].format(prior=_usd(prior_per))
    return EconomyDenominator(
        per_commit_micro_usd=per,
        commit_attempts=commits,
        prior_per_commit_micro_usd=prior_per,
        line=line,
    )


def _lever(
    receipts: ReceiptsWindow,
    board: AdvisorBoard | None,
    reasoning: EconomyReasoning | None,
    churn: EconomyChurn | None,
) -> EconomyLever:
    """The one lever, advisor-first: any decisive take owns the slot (as a
    pointer, never a restated number); otherwise the usage-hygiene facts in
    fixed order — cache reuse, cache churn, reasoning share, context weight,
    unpriced turns — otherwise calm. Low reuse outranks churn deliberately:
    when both fire, fresh-token leakage is the coarser problem and fixing it
    changes the churn arithmetic anyway. Premium share is deliberately not a
    candidate — that fact belongs to the Advisor alone (module docstring)."""
    if board is not None and any(take.take_id != ADVISOR_CALM_TAKE_ID for take in board.takes):
        return EconomyLever(
            lever_id="advisor",
            title=ECONOMY_COPY["lever-advisor-title"],
            body=ECONOMY_COPY["lever-advisor-body"],
            metric=0,
        )
    fresh = sum(f.tokens.input for f in receipts.families)
    cached = sum(f.tokens.cached for f in receipts.families)
    creation = sum(f.tokens.cache_creation for f in receipts.families)
    prompt_tokens = fresh + cached + creation
    cache_pct = _pct(cached, prompt_tokens)
    if prompt_tokens >= ECONOMY_CACHE_MIN_PROMPT_TOKENS and cache_pct < LOW_CACHE_MAX_PCT:
        return EconomyLever(
            lever_id="cache",
            title=ECONOMY_COPY["lever-cache-title"],
            body=ECONOMY_COPY["lever-cache-body"].format(pct=cache_pct),
            metric=cache_pct,
        )
    if churn is not None and churn.share_pct >= ECONOMY_CHURN_LEVER_MIN_PCT:
        return EconomyLever(
            lever_id="churn",
            title=ECONOMY_COPY["lever-churn-title"],
            body=ECONOMY_COPY["lever-churn-body"].format(pct=churn.share_pct),
            metric=churn.share_pct,
        )
    if reasoning is not None and reasoning.share_pct >= ECONOMY_REASONING_LEVER_MIN_PCT:
        return EconomyLever(
            lever_id="reasoning",
            title=ECONOMY_COPY["lever-reasoning-title"],
            body=ECONOMY_COPY["lever-reasoning-body"].format(pct=reasoning.share_pct),
            metric=reasoning.share_pct,
        )
    if receipts.total_assistant_turns > 0:
        per_turn = prompt_tokens // receipts.total_assistant_turns
        if per_turn >= CONTEXT_BLOAT_AVG_PROMPT_TOKENS:
            return EconomyLever(
                lever_id="context",
                title=ECONOMY_COPY["lever-context-title"],
                body=ECONOMY_COPY["lever-context-body"].format(tokens=compact(per_turn)),
                metric=per_turn,
            )
    if receipts.total_unpriced_turns >= ECONOMY_UNPRICED_MIN_TURNS:
        return EconomyLever(
            lever_id="unpriced",
            title=ECONOMY_COPY["lever-unpriced-title"],
            body=ECONOMY_COPY["lever-unpriced-body"].format(
                turns=count(receipts.total_unpriced_turns)
            ),
            metric=receipts.total_unpriced_turns,
        )
    return EconomyLever(
        lever_id="calm",
        title=ECONOMY_COPY["lever-calm-title"],
        body=ECONOMY_COPY["lever-calm-body"],
        metric=0,
    )


def _wow_delta(store: Store, today: date, wow: WeekOverWeek | None) -> int | None:
    """The week-over-week cost delta, gated: both 7-day ranges (aligned with
    history.week_over_week's windows) need ECONOMY_WOW_MIN_ACTIVE_DAYS active
    days, else the comparison is withheld."""
    if wow is None:
        return None
    this_start = today - timedelta(days=6)
    prior_end = this_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=6)
    if (
        len(store.active_days(this_start.isoformat(), today.isoformat()))
        < ECONOMY_WOW_MIN_ACTIVE_DAYS
    ):
        return None
    if (
        len(store.active_days(prior_start.isoformat(), prior_end.isoformat()))
        < ECONOMY_WOW_MIN_ACTIVE_DAYS
    ):
        return None
    return wow.cost_delta_pct


def _week_window_pct(store: Store, today: date) -> int | None:
    """The latest weekly rate-limit gauge within the lookback — the
    subscription user's real scarcity. None when no weekly reading exists
    (withheld, not zero)."""
    for offset in range(WEEK_GAUGE_LOOKBACK_DAYS):
        day = (today - timedelta(days=offset)).isoformat()
        weekly = [
            used_tenths
            for _ts, used_tenths, window_minutes in store.rate_limit_marks_for_day(day)
            if window_minutes == WINDOW_WEEK_MINUTES
        ]
        if weekly:
            return (weekly[-1] + 5) // 10  # latest reading, half-up percent
    return None


def _retro(store: Store, today: date) -> EconomyRetro | None:
    """The first-open verdict (AM-3): last RETRO_WINDOW_DAYS totals from
    already-ingested history, present only until dismissed once."""
    if store.meta_get(ECONOMY_INTRO_META_KEY) == "1":
        return None
    start = (today - timedelta(days=RETRO_WINDOW_DAYS - 1)).isoformat()
    end = today.isoformat()
    cost = 0
    turns = 0
    for _day, _tool, _model, values in store.usage_rows_by_day(start, end):
        row = ContributionRow.from_values(values)
        cost += row.cost_micro_usd
        turns += row.assistant_turns
    active = len(store.active_days(start, end))
    if active == 0:
        return None
    return EconomyRetro(
        days=RETRO_WINDOW_DAYS,
        active_days=active,
        assistant_turns=turns,
        cost_micro_usd=cost,
        line=ECONOMY_COPY["retro-line"].format(
            days=RETRO_WINDOW_DAYS,
            turns=count(turns),
            active=active,
            usd=_usd(cost),
        ),
    )


def record_economy_intro_seen(store: Store) -> None:
    """Dismiss the first-open retrospective, permanently and idempotently.
    Written only from the UI action path (never during a view read)."""
    store.meta_set(ECONOMY_INTRO_META_KEY, "1")


def compose_economy(
    store: Store,
    today: date,
    receipts: ReceiptsWindow,
    board: AdvisorBoard | None,
    wow: WeekOverWeek | None,
    billing_mode: str,
) -> EconomyReading:
    """The economy reading for one window: deterministic over (store, today,
    receipts, board, wow, billing_mode) — the receipts/board/wow inputs are
    the ones the shell already computes, so one page never derives the same
    number twice. Below the evidence floor the reading is unavailable and
    every surface hides it."""
    mode = billing_mode if billing_mode in ECONOMY_BILLING_MODES else "unknown"
    priced_turns = receipts.total_assistant_turns - receipts.total_unpriced_turns
    if priced_turns < ECONOMY_MIN_PRICED_TURNS or receipts.total_cost_micro_usd <= 0:
        return _UNAVAILABLE
    fresh = sum(f.tokens.input for f in receipts.families)
    cached = sum(f.tokens.cached for f in receipts.families)
    creation = sum(f.tokens.cache_creation for f in receipts.families)
    prompt_tokens = fresh + cached + creation
    window_start = today - timedelta(days=receipts.window_days - 1)
    reasoning = _reasoning(store, today, receipts.window_days)
    churn = _churn(store, today, receipts.window_days)
    denominator = _denominator(store, today, receipts.window_days, receipts.total_cost_micro_usd)
    denominator_note = ""
    if denominator is None and receipts.total_cost_micro_usd > 0:
        observed, _ = _window_commits_and_cost(store, window_start, today)
        denominator_note = ECONOMY_COPY["denominator-withheld"].format(
            floor=DENOMINATOR_MIN_COMMITS, commits=count(observed)
        )
    return EconomyReading(
        available=True,
        billing_mode=mode,
        window_days=receipts.window_days,
        active_days=len(store.active_days(window_start.isoformat(), today.isoformat())),
        cost_micro_usd=receipts.total_cost_micro_usd,
        priced_turns=priced_turns,
        cost_per_priced_turn_micro_usd=(receipts.total_cost_micro_usd // priced_turns),
        cache_hit_pct=_pct(cached, prompt_tokens),
        wow_cost_delta_pct=_wow_delta(store, today, wow),
        week_window_pct=(_week_window_pct(store, today) if mode == "subscription" else None),
        reasoning=reasoning,
        churn=churn,
        denominator=denominator,
        lever=_lever(receipts, board, reasoning, churn),
        retro=_retro(store, today),
        denominator_note=denominator_note,
    )
