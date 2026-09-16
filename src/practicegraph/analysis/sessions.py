"""Session grain (W2.0): the receipt, and what long context actually costs.

Two readings over the `session_totals` table, both answering questions the
day x tool x model aggregates cannot:

**The receipt** — "what did that just cost me?" The most recent substantial
session, priced: how long it ran, how many turns, what it cost, and whether it
compacted. This is the post-flight bookend of the daily loop (and the thing a
person pastes into a channel).

**The context tax** — the cost-practice research's top lever, measured on the
person's own history instead of asserted. Agentic tools re-send the whole
conversation every turn, so a long session carries more prompt weight per turn
than a short one. We compare the two cohorts and report *both* numbers:
- how much more **context** long sessions carry per turn (the mechanism, free
  of model-tier confounds), and
- how much more they **cost** per turn (the consequence).

Those two can diverge, and the honest reading depends on which: when the cache
is serving that carried context, the weight climbs and the cost does not — that
is the cache doing its job, and the reading says so instead of manufacturing an
alarm. A steady state is a first-class result here (PRINCIPLES §3).

What this module refuses to do: claim a saving that did not happen. We never
price a counterfactual ("clearing would have saved $X") — the cohorts are two
things the person actually did, and the mechanism sentence explains the gap.

Determinism (INV-6): integer math over stored rows with "today" passed in;
double-compose is byte-identical. Local-only forever (NFR-PRV-6) — and
`session_key` is an identity that never reaches a surface or a payload (the
readings carry only derived facts; the wire scan pins the vocabulary).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from practicegraph.analysis.tokenizer import straddles_tokenizer_change
from practicegraph.report.format import compact, count, usd
from practicegraph.store import SessionRow, Store

# The wire field-name scan pins these out of any payload (test_wire).
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "session_totals",
    "session_key",
    "context_tax",
    "receipt",
)

# A session needs this much substance before it earns a receipt — a two-turn
# check-in is not a session anyone wants priced.
RECEIPT_MIN_TURNS = 5

# The cohort split. Sessions at or past this many assistant turns are "long";
# the rest are "short". Calibrated to sit well past a quick exchange and near
# where carried context starts to dominate a turn's prompt.
LONG_SESSION_MIN_TURNS = 40

# Each cohort needs this many sessions before any comparison is spoken
# (the sample gate — withheld, not zero).
COHORT_MIN_SESSIONS = 5

# Speak the comparison only when the carried context genuinely differs:
# 1.3x in tenths. Below this the cohorts are the same shape and there is
# nothing to report.
CONTEXT_MULTIPLE_MIN_TENTHS = 13

# Above this cost multiple the carried context is landing on the bill; at or
# below it, the cache is absorbing the weight (the two-sided branch).
COST_MULTIPLE_CARRIED_TENTHS = 13

# Closed tool labels — the only place a source id becomes a word.
TOOL_LABELS: dict[str, str] = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}

# Closed copy (NFR-QLT-3, lexicon-scanned). Calm register, no exclamation, no
# guilt: a session's cost is accounting, never a verdict on the work.
SESSION_COPY: dict[str, str] = {
    "receipt-eyebrow": "Session receipt",
    "receipt-title": "Your last full session",
    "receipt-line": (
        "{minutes} minutes, {turns} assistant turns, about {money} on "
        "{tool}."
    ),
    "receipt-compaction": (
        "It compacted its context {compactions} times. A fresh session "
        "usually reasons more sharply than a heavily compacted one."
    ),
    "receipt-cache": (
        "Cache served {pct}% of its prompt tokens."
    ),
    "tax-title": "What a long session costs you",
    "tax-carried": (
        "Your sessions past {threshold} turns carried about {context}x the "
        "context per turn of shorter ones, and cost about {cost}x per turn "
        "- every turn re-sends the whole conversation."
    ),
    "tax-absorbed": (
        "Your sessions past {threshold} turns carried about {context}x the "
        "context per turn of shorter ones, but cost only about {cost}x per "
        "turn - the cache is absorbing most of it."
    ),
    "tax-basis": (
        "Measured on {long_n} long and {short_n} shorter sessions of your "
        "own over {days} days."
    ),
    # Shown only when the window spans both tokenizer generations, where part
    # of a token-count gap is encoding rather than behaviour. Money is not
    # caveated: more tokens at the same rate is spend the person really made.
    "tax-tokenizer-caveat": (
        "This window mixes model generations that tokenize differently, so "
        "read the context multiple as a direction; the cost multiple is "
        "real money."
    ),
}


@dataclass(frozen=True, slots=True)
class SessionReceipt:
    """One session, priced. Carries no session identity — only derived facts
    a person would recognise as their own work."""

    day: str
    tool_label: str
    minutes: int
    assistant_turns: int
    cost_micro_usd: int
    compactions: int
    cache_hit_pct: int
    prompt_tokens_per_turn: int
    line: str
    detail: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextTax:
    """The two-cohort reading: carried context and its cost, both stated."""

    register: str  # "carried" | "absorbed"
    long_sessions: int
    short_sessions: int
    context_multiple_tenths: int
    cost_multiple_tenths: int
    line: str
    basis: str
    # Non-empty when the window spans tokenizer generations (see
    # analysis/tokenizer.py) — the context multiple then carries an encoding
    # artifact and says so rather than being silently reported as behaviour.
    caveat: str = ""


@dataclass(frozen=True, slots=True)
class SessionReading:
    """The composed session surface: the receipt, the tax, or neither."""

    available: bool
    receipt: SessionReceipt | None
    tax: ContextTax | None


_UNAVAILABLE = SessionReading(available=False, receipt=None, tax=None)


def _pct(numerator: int, denominator: int) -> int:
    return numerator * 100 // denominator if denominator > 0 else 0


def _tenths(numerator: int, denominator: int) -> int:
    """A ratio in tenths (2.3x -> 23), 0 when the denominator is empty."""
    return numerator * 10 // denominator if denominator > 0 else 0


def _render_tenths(value: int) -> str:
    return f"{value // 10}.{value % 10}"


def _minutes(first_ts: str, last_ts: str) -> int:
    try:
        start = datetime.fromisoformat(first_ts)
        end = datetime.fromisoformat(last_ts)
    except ValueError:
        return 0
    return max(0, int((end - start).total_seconds()) // 60)


def session_minutes(row: SessionRow) -> int:
    """One session's wall-clock span in whole minutes — the ground truth the
    calibration mirror measures an estimate against."""
    return _minutes(row.first_ts, row.last_ts)


def _prompt_tokens(row: SessionRow) -> int:
    """Everything the model read on that session's turns."""
    return row.input_tokens + row.cached_tokens + row.cache_creation_tokens


def _receipt(rows: list[SessionRow]) -> SessionReceipt | None:
    """The most recent session with real substance — the post-flight bookend.
    Rows arrive cost-ordered, so pick by last_ts explicitly (deterministic:
    ties break on the stable secondary order the store already applied)."""
    eligible = [r for r in rows if r.assistant_turns >= RECEIPT_MIN_TURNS]
    if not eligible:
        return None
    latest = max(eligible, key=lambda r: r.last_ts)
    prompt = _prompt_tokens(latest)
    minutes = _minutes(latest.first_ts, latest.last_ts)
    per_turn = prompt // latest.assistant_turns if latest.assistant_turns else 0
    cache_pct = _pct(latest.cached_tokens, prompt)
    tool_label = TOOL_LABELS.get(latest.tool, latest.tool)
    detail: list[str] = [SESSION_COPY["receipt-cache"].format(pct=cache_pct)]
    if latest.compactions > 0:
        detail.append(
            SESSION_COPY["receipt-compaction"].format(
                compactions=count(latest.compactions)
            )
        )
    return SessionReceipt(
        day=latest.last_ts[:10],
        tool_label=tool_label,
        minutes=minutes,
        assistant_turns=latest.assistant_turns,
        cost_micro_usd=latest.cost_micro_usd,
        compactions=latest.compactions,
        cache_hit_pct=cache_pct,
        prompt_tokens_per_turn=per_turn,
        line=SESSION_COPY["receipt-line"].format(
            minutes=count(minutes),
            turns=count(latest.assistant_turns),
            money=usd(latest.cost_micro_usd),
            tool=tool_label,
        ),
        detail=tuple(detail),
    )


def _context_tax(
    rows: list[SessionRow], window_days: int, mixed_tokenizers: bool = False
) -> ContextTax | None:
    """Long vs short sessions on carried context and on cost, both per turn.
    Withheld unless both cohorts clear the sample gate AND the carried context
    genuinely differs — no gate, no claim."""
    long_rows = [r for r in rows if r.assistant_turns >= LONG_SESSION_MIN_TURNS]
    short_rows = [
        r
        for r in rows
        if RECEIPT_MIN_TURNS <= r.assistant_turns < LONG_SESSION_MIN_TURNS
    ]
    if (
        len(long_rows) < COHORT_MIN_SESSIONS
        or len(short_rows) < COHORT_MIN_SESSIONS
    ):
        return None

    def _per_turn(cohort: list[SessionRow]) -> tuple[int, int]:
        turns = sum(r.assistant_turns for r in cohort)
        if turns <= 0:
            return 0, 0
        prompt = sum(_prompt_tokens(r) for r in cohort) // turns
        # Priced turns only, so unpriced models cannot deflate a cohort's rate.
        priced = sum(r.assistant_turns - r.unpriced_turns for r in cohort)
        cost = sum(r.cost_micro_usd for r in cohort) // priced if priced else 0
        return prompt, cost

    long_prompt, long_cost = _per_turn(long_rows)
    short_prompt, short_cost = _per_turn(short_rows)
    if short_prompt <= 0 or short_cost <= 0:
        return None
    context_multiple = _tenths(long_prompt, short_prompt)
    cost_multiple = _tenths(long_cost, short_cost)
    if context_multiple < CONTEXT_MULTIPLE_MIN_TENTHS:
        return None
    register = (
        "carried" if cost_multiple >= COST_MULTIPLE_CARRIED_TENTHS else "absorbed"
    )
    return ContextTax(
        register=register,
        long_sessions=len(long_rows),
        short_sessions=len(short_rows),
        context_multiple_tenths=context_multiple,
        cost_multiple_tenths=cost_multiple,
        line=SESSION_COPY[f"tax-{register}"].format(
            threshold=LONG_SESSION_MIN_TURNS,
            context=_render_tenths(context_multiple),
            cost=_render_tenths(cost_multiple),
        ),
        basis=SESSION_COPY["tax-basis"].format(
            long_n=count(len(long_rows)),
            short_n=count(len(short_rows)),
            days=window_days,
        ),
        caveat=SESSION_COPY["tax-tokenizer-caveat"] if mixed_tokenizers else "",
    )


def compose_sessions(
    store: Store, today: date, window_days: int
) -> SessionReading:
    """The session surface for one trailing window: the latest receipt and the
    context-tax cohorts. Unavailable when the window holds no substantial
    session — the surfaces hide it rather than render a hollow card."""
    start = (today - timedelta(days=window_days - 1)).isoformat()
    rows = store.sessions_between(start, today.isoformat())
    if not rows:
        return _UNAVAILABLE
    # session_totals holds no model id, so the tokenizer check reads the models
    # actually used in the same window from the day aggregates. Coarser than
    # per-session, and deliberately so: the question is only whether the window
    # mixes generations at all.
    mixed = straddles_tokenizer_change(
        {model for _day, _tool, model, _values in store.usage_rows_by_day(
            start, today.isoformat()
        )}
    )
    receipt = _receipt(rows)
    tax = _context_tax(rows, window_days, mixed)
    if receipt is None and tax is None:
        return _UNAVAILABLE
    return SessionReading(available=True, receipt=receipt, tax=tax)


def session_volume_line(reading: SessionReading) -> str:
    """A compact token figure for the receipt's tooltip — the one place the
    prompt weight per turn is spoken as a number."""
    if reading.receipt is None:
        return ""
    return compact(reading.receipt.prompt_tokens_per_turn)
