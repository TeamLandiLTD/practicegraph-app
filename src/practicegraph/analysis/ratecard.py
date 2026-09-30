"""Versioned local rate card and cost estimation (FR-ANL-1).

All money is integer micro-USD (1 USD = 1_000_000 micro-USD) so estimates are
byte-reproducible (INV-6) — no floats anywhere in the money path. Rates are
micro-USD per 1M tokens. This bundled card is the FR-EMT-4 fallback; catalog
pulls will replace it with server-versioned artifacts in a later milestone.

Every figure derived from this card is an estimate and must be presented with
its rate-card version and estimate framing (FR-ANL-1).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from practicegraph.events import TokenCounts

RATE_CARD_VERSION = "bundled-2026-09-29"

MICRO_PER_TOKEN_UNIT = 1_000_000  # rates are per 1M tokens


@dataclass(frozen=True, slots=True)
class Rate:
    """Micro-USD per 1M tokens, by token class. ``cache_creation`` prices the
    5-minute cache write (the default); ``cache_creation_1h`` prices the
    1-hour-TTL write (2x input on Anthropic models)."""

    input: int
    output: int
    cache_read: int
    cache_creation: int
    cache_creation_1h: int = 0


# Longest-prefix-wins would be ambiguous; this table is *ordered* and the first
# prefix match applies, so more specific prefixes must precede shorter ones.
# Sources: platform.claude.com pricing and developers.openai.com pricing, both
# re-verified 2026-07-25; Fable 5.1 / Mythos 5.1 added 2026-09-18; Anthropic
# re-verified and Opus 5.5 added 2026-09-23; Sonnet 5.5, GPT-6 and the cut
# GPT-5.6 prices 2026-09-29.
# Legacy entries are kept — historical logs still need them priced.
#
# Sonnet 5's launch-introductory $2/$10 became its standard price; the
# increase to $3/$15 once scheduled for 2026-09-01 was cancelled.
_RATES: tuple[tuple[str, Rate], ...] = (
    # Anthropic — current generation. The 5.1 point releases hold Fable 5's
    # price except cache reads, which drop to $0.25 (0.025x input). Listed
    # explicitly: matching fails closed across versions, so `claude-fable-5`
    # never prices `claude-fable-5-1` (it went unpriced on installs without a
    # pulled catalog card until this entry).
    ("claude-fable-5-1", Rate(10_000_000, 50_000_000, 250_000, 12_500_000, 20_000_000)),
    ("claude-mythos-5-1", Rate(10_000_000, 50_000_000, 250_000, 12_500_000, 20_000_000)),
    ("claude-fable-5", Rate(10_000_000, 50_000_000, 1_000_000, 12_500_000, 20_000_000)),
    ("claude-mythos-5", Rate(10_000_000, 50_000_000, 1_000_000, 12_500_000, 20_000_000)),
    # Opus 5.5 undercuts Opus 5 on every class; cache reads are 0.05x input
    # ($0.20). Listed explicitly: `claude-opus-5` never prices it.
    ("claude-opus-5-5", Rate(4_000_000, 20_000_000, 200_000, 5_000_000, 8_000_000)),
    # Opus 5 (2026-07-24) holds Opus 4.8's price exactly.
    ("claude-opus-5",Rate(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000)),
    ("claude-opus-4-8", Rate(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000)),
    ("claude-opus-4-7", Rate(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000)),
    ("claude-opus-4-6", Rate(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000)),
    ("claude-opus-4-5", Rate(5_000_000, 25_000_000, 500_000, 6_250_000, 10_000_000)),
    # Sonnet 5.5 (2026-09-28) lists at Sonnet 5's price, standard 0.1x cache
    # reads. Listed explicitly: `claude-sonnet-5` never prices it.
    ("claude-sonnet-5-5", Rate(2_000_000, 10_000_000, 200_000, 2_500_000, 4_000_000)),
    ("claude-sonnet-5", Rate(2_000_000, 10_000_000, 200_000, 2_500_000, 4_000_000)),
    ("claude-sonnet-4-6", Rate(3_000_000, 15_000_000, 300_000, 3_750_000, 6_000_000)),
    ("claude-sonnet-4-5", Rate(3_000_000, 15_000_000, 300_000, 3_750_000, 6_000_000)),
    ("claude-haiku-4-5", Rate(1_000_000, 5_000_000, 100_000, 1_250_000, 2_000_000)),
    # Anthropic — deprecated/retired generations still present in local logs.
    # Point releases are listed EXPLICITLY: since matching fails closed across
    # versions, `claude-opus-4-1` no longer inherits `claude-opus-4` and must
    # carry its own entry or historical turns would silently go unpriced.
    ("claude-opus-4-1", Rate(15_000_000, 75_000_000, 1_500_000, 18_750_000, 30_000_000)),
    ("claude-opus-4", Rate(15_000_000, 75_000_000, 1_500_000, 18_750_000, 30_000_000)),
    ("claude-sonnet-4", Rate(3_000_000, 15_000_000, 300_000, 3_750_000, 6_000_000)),
    ("claude-haiku-4", Rate(1_000_000, 5_000_000, 100_000, 1_250_000, 2_000_000)),
    ("claude-3-5-haiku", Rate(800_000, 4_000_000, 80_000, 1_000_000, 1_600_000)),
    # OpenAI — current generation (dotted ids precede the dash-era catch-alls).
    # The 5.6 family is the first OpenAI generation to CHARGE for cache writes
    # (1.25x uncached input; earlier models wrote to cache free), so these
    # carry a real cache_creation rate where every entry below it is 0.
    # GPT-6 (2026-09): each variant carded on its own, fail-closed matching.
    ("gpt-6-astra", Rate(10_000_000, 50_000_000, 1_000_000, 12_500_000)),
    ("gpt-6-sol", Rate(2_000_000, 10_000_000, 200_000, 2_500_000)),
    ("gpt-6-luna", Rate(100_000, 500_000, 10_000, 125_000)),
    # 5.6 prices were cut alongside GPT-6 (re-verified 2026-09-29). KNOWN
    # EXPIRY: Sol's is promotional "at least through 2026-11-21"; re-verify
    # then (`rate_card_stale_days` keeps the card's age visible meanwhile).
    ("gpt-5.6-sol", Rate(4_000_000, 20_000_000, 400_000, 5_000_000)),
    ("gpt-5.6-terra", Rate(2_000_000, 12_000_000, 200_000, 2_500_000)),
    ("gpt-5.6-luna", Rate(200_000, 1_200_000, 20_000, 250_000)),
    # Bare `gpt-5.6` aliases to Sol (OpenAI's documented default).
    ("gpt-5.6", Rate(4_000_000, 20_000_000, 400_000, 5_000_000)),
    # Codex logs Luna Reserve turns as `gpt-reserve`: extra usage with
    # GPT-5.6 Luna after the plan's allowance runs out ("a fallback mode, not a
    # separate model", help.openai.com). No API price of its own, so its
    # API-equivalent value is Luna's.
    ("gpt-reserve", Rate(200_000, 1_200_000, 20_000, 250_000)),
    ("gpt-5.5-pro", Rate(30_000_000, 180_000_000, 0, 0)),
    ("gpt-5.5", Rate(5_000_000, 30_000_000, 500_000, 0)),
    ("gpt-5.4-pro", Rate(30_000_000, 180_000_000, 0, 0)),
    ("gpt-5.4-mini", Rate(750_000, 4_500_000, 75_000, 0)),
    ("gpt-5.4-nano", Rate(200_000, 1_250_000, 20_000, 0)),
    ("gpt-5.4", Rate(2_500_000, 15_000_000, 250_000, 0)),
    ("gpt-5.3-codex", Rate(1_750_000, 14_000_000, 175_000, 0)),
    # OpenAI — earlier generations still present in local logs
    ("gpt-5-codex", Rate(1_250_000, 10_000_000, 125_000, 0)),
    ("gpt-5-mini", Rate(250_000, 2_000_000, 25_000, 0)),
    ("gpt-5-nano", Rate(50_000, 400_000, 5_000, 0)),
    ("gpt-5", Rate(1_250_000, 10_000_000, 125_000, 0)),
    ("gpt-4.1-mini", Rate(400_000, 1_600_000, 100_000, 0)),
    ("gpt-4.1", Rate(2_000_000, 8_000_000, 500_000, 0)),
    ("o3", Rate(2_000_000, 8_000_000, 500_000, 0)),
    ("o4-mini", Rate(1_100_000, 4_400_000, 275_000, 0)),
)


@dataclass(frozen=True, slots=True)
class RateCard:
    """A versioned rate card: the bundled default (FR-EMT-4 fallback) or a
    validated catalog artifact pulled from the backend."""

    version: str
    rates: tuple[tuple[str, Rate], ...]


BUNDLED_RATE_CARD = RateCard(version=RATE_CARD_VERSION, rates=_RATES)

# Process-level active card. Set exactly once per invocation from the local
# catalog directory (configuration input — determinism holds per INV-6:
# identical inputs AND configuration give identical outputs).
_ACTIVE: RateCard = BUNDLED_RATE_CARD

CATALOG_DIR_NAME = "catalog"
RATE_CARD_FILE_NAME = "rate-card.json"

_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PREFIX_RE = re.compile(r"^[a-z0-9.-]{1,64}$")
_RATE_KEYS = frozenset(
    {"input", "output", "cache_read", "cache_creation", "cache_creation_1h"}
)


def parse_rate_card_artifact(raw: object) -> RateCard | None:
    """Strictly validate a catalog rate-card artifact (closed schema: version,
    unit, prefix->rates ints). None on any deviation — never fail-open into
    garbage pricing."""
    if not isinstance(raw, dict) or set(raw.keys()) != {
        "rate_card_version", "unit", "rates",
    }:
        return None
    version = raw["rate_card_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    if raw["unit"] != "micro_usd_per_1m_tokens":
        return None
    rates_raw = raw["rates"]
    if not isinstance(rates_raw, dict) or not rates_raw:
        return None
    rates: list[tuple[str, Rate]] = []
    for prefix, values in rates_raw.items():
        if not isinstance(prefix, str) or not _PREFIX_RE.match(prefix):
            return None
        if not isinstance(values, dict) or set(values.keys()) != _RATE_KEYS:
            return None
        if not all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 0
            for v in values.values()
        ):
            return None
        rates.append(
            (prefix, Rate(values["input"], values["output"], values["cache_read"],
                          values["cache_creation"], values["cache_creation_1h"]))
        )
    # Longest prefix first so specific entries always win, deterministically.
    rates.sort(key=lambda item: (-len(item[0]), item[0]))
    return RateCard(version=version, rates=tuple(rates))


# Our own cards are date-stamped, which makes any two of them comparable: the
# client's bundled card is `bundled-YYYY-MM-DD`, and the publisher seals
# public-channel editions as `rates-YYYY-MM-DD`, or `rates-YYYY-MM-DD-N` when
# a day sees more than one edition (the publisher refuses to reuse a version
# for changed content; 2026-09-29 produced `rates-2026-09-29-2`). Editions
# rank by date, then by that sequence number. An org's curated card carries
# its own version string and is deliberately NOT date-comparable — an explicit
# override always wins (an org that names its card `rates-<date>` opts into
# date comparison).
_BUNDLED_DATE_RE = re.compile(
    r"^(?:bundled|rates)-(\d{4}-\d{2}-\d{2})(?:-(\d{1,2}))?$"
)


# Past this age a dated card is old enough that a price is likely to have moved
# under it. Calibrated on observed cadence: the 2026-07-03 card was already
# wrong on 2026-07-09 when GPT-5.6 shipped, and OpenAI put out twelve model
# releases in the fourteen months to July 2026.
RATE_CARD_STALE_DAYS = 30


def _bundled_stamp(version: str) -> str | None:
    """The date of one of our cards, or None for any other version string."""
    match = _BUNDLED_DATE_RE.match(version)
    return match.group(1) if match else None


def _stamp_key(version: str) -> tuple[str, int] | None:
    """Ranking key for one of our cards: (date, same-day sequence). The first
    edition of a day has no suffix and ranks as 0; `-2` and `-02` both rank 2."""
    match = _BUNDLED_DATE_RE.match(version)
    if match is None:
        return None
    return match.group(1), int(match.group(2) or 0)


def rate_card_age_days(version: str, today: date) -> int | None:
    """Days since a dated card was published, or None when the version is not
    date-stamped (an org's curated card, which we cannot age).

    Staleness is the failure mode this product cannot detect from its own
    numbers: a stale card does not error, it prices a new generation at an old
    rate and shows the result with full confidence. Surfacing the age is the
    cheapest defence — it turns a silent wrong number into a visible caveat.
    """
    stamp = _bundled_stamp(version)
    if stamp is None:
        return None
    try:
        published = date.fromisoformat(stamp)
    except ValueError:
        return None
    return max(0, (today - published).days)


def activate_rate_card_from(data_dir: Path) -> str:
    """Activate the best available card. Returns "catalog" or "bundled".

    The catalog card normally wins — that is the point of serving it, and a
    pull failure must never block local analysis (FR-EMT-4).

    One exception, learned from a real incident: a cached catalog card from
    2026-07-09 silently outranked a corrected bundled card, so a shipped
    price fix reached nobody who had ever pulled a catalog. Both of OUR cards
    are date-stamped, so when the cached one is demonstrably older we prefer
    the bundled card instead of trusting a stale cache forever. An org's
    curated card is not date-stamped and always wins, as intended.
    """
    global _ACTIVE
    path = data_dir / CATALOG_DIR_NAME / RATE_CARD_FILE_NAME
    try:
        card = parse_rate_card_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        card = None
    if card is None:
        _ACTIVE = BUNDLED_RATE_CARD
        return "bundled"
    cached_key = _stamp_key(card.version)
    bundled_key = _stamp_key(BUNDLED_RATE_CARD.version)
    if (
        cached_key is not None
        and bundled_key is not None
        and cached_key < bundled_key
    ):
        _ACTIVE = BUNDLED_RATE_CARD
        return "bundled"
    _ACTIVE = card
    return "catalog"


def reset_active_rate_card() -> None:
    global _ACTIVE
    _ACTIVE = BUNDLED_RATE_CARD


def active_rate_card() -> RateCard:
    return _ACTIVE


# Tail patterns that decide whether a prefix match is the SAME model or a
# DIFFERENT generation wearing a familiar prefix (see `prefix_matches`).
# A version tail: ".6-sol" after "gpt-5", or a bare digit run after "gpt-5"
# (so "gpt-5" never claims "gpt-50").
_TAIL_VERSION = re.compile(r"^[.\d]")
# A date stamp: "-20250514". Long digit runs are release dates, not versions,
# and the dated id is the same priced model ("claude-sonnet-4-20250514").
_TAIL_DATE = re.compile(r"^-\d{6,}$")
# A short numeric segment: "-8" after "claude-opus-4" — a point release, i.e.
# a different model with different pricing.
_TAIL_VERSION_SEGMENT = re.compile(r"^-\d{1,2}(-|$)")


def prefix_matches(model: str, prefix: str) -> bool:
    """Whether `prefix` prices `model` — FAIL CLOSED across generations.

    Plain ``startswith`` silently mispriced every new generation: OpenAI ships
    ``gpt-5.6-sol``, ``"gpt-5.6-sol".startswith("gpt-5")`` is True, and the
    turn is billed at the legacy gpt-5 rate — four times too cheap, with the
    ``unpriced_models`` finding never firing because the match "succeeded".
    Wrong numbers are strictly worse than absent ones: absent numbers are
    flagged to the person, wrong ones are shown with full confidence.

    So a prefix only prices a model when the remainder is a *variant* of the
    same generation, never a version bump:

    - exact match, or a named variant (``gpt-5`` -> ``gpt-5-codex``): priced
    - a release date (``claude-sonnet-4`` -> ``claude-sonnet-4-20250514``): priced
    - a version tail (``gpt-5`` -> ``gpt-5.6-sol``, ``claude-opus-4`` ->
      ``claude-opus-4-8``): NOT priced, so it surfaces as an unpriced model

    The deliberate cost: a genuinely new point release is unpriced until the
    card catches up. That is the intended trade — the person is told their
    spend estimate is incomplete instead of being shown a confident wrong one.
    """
    if not model.startswith(prefix):
        return False
    tail = model[len(prefix):]
    if not tail:
        return True
    if _TAIL_VERSION.match(tail):
        return False
    if _TAIL_DATE.match(tail):
        return True
    if _TAIL_VERSION_SEGMENT.match(tail):
        return False
    return tail.startswith("-")


def rate_for_model(model: str) -> Rate | None:
    """First matching prefix in the active card's ordered table; None for
    unpriced models. Matching is generation-aware (see `prefix_matches`)."""
    for prefix, rate in _ACTIVE.rates:
        if prefix_matches(model, prefix):
            return rate
    return None


def rate_card_artifact() -> dict[str, object]:
    """The rate card as a versioned catalog artifact (FR-EMT-4 serving side)."""
    return {
        "rate_card_version": RATE_CARD_VERSION,
        "unit": "micro_usd_per_1m_tokens",
        "rates": {
            prefix: {
                "input": rate.input,
                "output": rate.output,
                "cache_read": rate.cache_read,
                "cache_creation": rate.cache_creation,
                "cache_creation_1h": rate.cache_creation_1h,
            }
            for prefix, rate in _RATES
        },
    }


# Fast mode (`usage.speed == "fast"`) runs the same model at a premium on
# every token class. Kept apart from `Rate` so the served rate-card artifact
# schema stays closed and unchanged. Only documented multipliers are listed;
# a fast turn on any other model is UNPRICED rather than billed at the
# standard rate — half the real cost shown with full confidence would be the
# silent wrong number `prefix_matches` exists to prevent.
_FAST_MULTIPLIERS: tuple[tuple[str, int], ...] = (
    ("claude-opus-5-5", 2),  # $8 / $40 per 1M vs $4 / $20 standard
    ("claude-opus-5", 2),  # $10 / $50 per 1M vs $5 / $25 standard
    ("claude-opus-4-8", 2),  # $10 / $50 per 1M vs $5 / $25 standard
)


def fast_multiplier_for(model: str) -> int | None:
    """The fast-mode price multiplier for `model`; None when undocumented."""
    for prefix, multiplier in _FAST_MULTIPLIERS:
        if prefix_matches(model, prefix):
            return multiplier
    return None


def estimate_cost_micro_usd(
    tokens: TokenCounts, model: str, *, fast: bool = False
) -> int | None:
    """Integer micro-USD estimate for one turn; None when the model is unpriced.

    Cache writes split by TTL: the 1-hour subset prices at the 1h rate, the
    remainder at the 5-minute rate. Reasoning tokens are informational
    (already included in billable output where providers bill them). A fast
    turn multiplies every class by the model's fast multiplier, and is
    unpriced when the model has none on the card.
    """
    rate = rate_for_model(model)
    if rate is None:
        return None
    multiplier = 1
    if fast:
        fast_multiplier = fast_multiplier_for(model)
        if fast_multiplier is None:
            return None
        multiplier = fast_multiplier
    creation_1h = min(tokens.cache_creation_1h, tokens.cache_creation)
    creation_5m = tokens.cache_creation - creation_1h
    total = (
        tokens.input * rate.input
        + tokens.output * rate.output
        + tokens.cached * rate.cache_read
        + creation_5m * rate.cache_creation
        + creation_1h * rate.cache_creation_1h
    )
    return total * multiplier // MICRO_PER_TOKEN_UNIT
