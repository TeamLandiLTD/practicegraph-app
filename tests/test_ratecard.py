"""Cost estimation determinism (FR-ANL-1, INV-6)."""

from __future__ import annotations

import json
from pathlib import Path

from practicegraph.analysis.ratecard import (
    BUNDLED_RATE_CARD,
    estimate_cost_micro_usd,
    parse_rate_card_artifact,
    prefix_matches,
    rate_for_model,
)
from practicegraph.events import TokenCounts


def test_prefix_matching_prefers_specific_entries() -> None:
    mini = rate_for_model("gpt-5-mini-2026-01-01")
    plain = rate_for_model("gpt-5-2026-01-01")
    assert mini is not None and plain is not None
    assert mini.input == 250_000
    assert plain.input == 1_250_000


def test_unknown_model_is_unpriced_not_zero() -> None:
    assert rate_for_model("totally-new-model") is None
    assert estimate_cost_micro_usd(TokenCounts(input=100), "totally-new-model") is None


def test_estimate_is_exact_integer_math() -> None:
    tokens = TokenCounts(input=1200, output=350, cached=800, cache_creation=200)
    assert estimate_cost_micro_usd(tokens, "claude-sonnet-4-20250514") == 9840
    assert estimate_cost_micro_usd(TokenCounts(input=1), "claude-sonnet-4-20250514") == 3
    assert estimate_cost_micro_usd(TokenCounts(), "claude-sonnet-4-20250514") == 0


def test_current_generation_pricing_and_1h_cache_split() -> None:
    """Rates match platform.claude.com (2026-07-03): the 1h cache-write subset
    prices at 2x input; the 5m remainder at 1.25x."""
    fable = rate_for_model("claude-fable-5")
    assert fable is not None
    assert (fable.input, fable.output) == (10_000_000, 50_000_000)
    assert (fable.cache_read, fable.cache_creation) == (1_000_000, 12_500_000)
    assert fable.cache_creation_1h == 20_000_000
    # 1000 cache-write tokens, 400 of them 1h-TTL:
    # 600 * 12.50 + 400 * 20.00 per MTok = 7_500 + 8_000 = 15_500 micro-USD.
    tokens = TokenCounts(cache_creation=1000, cache_creation_1h=400)
    assert estimate_cost_micro_usd(tokens, "claude-fable-5") == 15_500
    # Sonnet 5 uses introductory pricing through 2026-08-31.
    sonnet5 = rate_for_model("claude-sonnet-5")
    assert sonnet5 is not None and sonnet5.input == 2_000_000
    # Dotted OpenAI ids resolve before the dash-era catch-alls.
    codex = rate_for_model("gpt-5.3-codex")
    assert codex is not None and codex.input == 1_750_000
    legacy_codex = rate_for_model("gpt-5-codex")
    assert legacy_codex is not None and legacy_codex.input == 1_250_000


def test_cached_tokens_priced_separately_from_input() -> None:
    """FR-ANL-1: cached-token pricing is separated from input pricing."""
    cached_only = estimate_cost_micro_usd(
        TokenCounts(cached=1000), "claude-sonnet-4-20250514"
    )
    input_only = estimate_cost_micro_usd(
        TokenCounts(input=1000), "claude-sonnet-4-20250514"
    )
    assert cached_only == 300
    assert input_only == 3000


def test_gpt_5_6_family_is_priced_correctly() -> None:
    """The 5.6 generation, verified against developers.openai.com 2026-07-25.
    Before this entry existed, `gpt-5.6-sol` prefix-matched `gpt-5` and was
    billed at $1.25/$10 — four times under on input, three under on output."""
    sol = rate_for_model("gpt-5.6-sol")
    assert sol is not None
    assert (sol.input, sol.output, sol.cache_read) == (5_000_000, 30_000_000, 500_000)
    # 5.6 is the first OpenAI generation to CHARGE for cache writes (1.25x
    # uncached input); every earlier OpenAI entry carries 0.
    assert sol.cache_creation == 6_250_000
    terra = rate_for_model("gpt-5.6-terra")
    assert terra is not None
    assert (terra.input, terra.output) == (2_500_000, 15_000_000)
    luna = rate_for_model("gpt-5.6-luna")
    assert luna is not None
    assert (luna.input, luna.output) == (1_000_000, 6_000_000)
    # The bare id aliases to Sol.
    bare = rate_for_model("gpt-5.6")
    assert bare is not None and bare.input == 5_000_000
    # Earlier OpenAI models still write to cache free.
    legacy = rate_for_model("gpt-5.5")
    assert legacy is not None and legacy.cache_creation == 0


def test_prefix_matching_fails_closed_across_generations() -> None:
    """The regression that made wrong numbers look confident: a new generation
    must never inherit an older generation's rate. Unpriced-and-flagged beats
    silently-wrong, because only the first is visible to the person."""
    from practicegraph.analysis.ratecard import prefix_matches

    # THE bug: a minor-version bump must not match the bare generation.
    assert prefix_matches("gpt-5.6-sol", "gpt-5") is False
    assert prefix_matches("gpt-5.5", "gpt-5") is False
    # ...nor may a prefix swallow a longer number.
    assert prefix_matches("gpt-50", "gpt-5") is False
    # Anthropic point releases are version bumps, not variants.
    assert prefix_matches("claude-opus-4-9", "claude-opus-4") is False
    assert prefix_matches("claude-sonnet-4-7", "claude-sonnet-4") is False
    # Release-date stamps ARE the same priced model.
    assert prefix_matches("claude-sonnet-4-20250514", "claude-sonnet-4") is True
    assert prefix_matches("claude-fable-5-20260630", "claude-fable-5") is True
    # Named variants of the same generation still price.
    assert prefix_matches("gpt-5-codex", "gpt-5") is True
    assert prefix_matches("gpt-5-mini", "gpt-5") is True
    # Exact match, and a non-match.
    assert prefix_matches("o3", "o3") is True
    assert prefix_matches("claude-opus-5", "claude-opus-4") is False


def test_unknown_generations_surface_as_unpriced() -> None:
    """End to end: models the card does not know return None, so the
    `unpriced_models` finding fires instead of a confident wrong figure."""
    for unknown in (
        "gpt-5.9-newthing",   # a future OpenAI generation
        "claude-opus-6",      # a future Anthropic generation
        "claude-opus-4-9",    # a hypothetical point release
        "model-router",       # real id seen in Codex logs
        "qwen3.6",            # third-party model
    ):
        assert rate_for_model(unknown) is None, unknown
    # ...while the point releases that DO exist are carded explicitly, because
    # fail-closed matching means they can no longer inherit a parent's rate.
    for known in (
        "claude-opus-5",              # shipped 2026-07-24
        "claude-opus-4-1-20250805",   # Opus 4.1, dated build
        "claude-sonnet-4-5",
        "gpt-5.6-sol",
    ):
        assert rate_for_model(known) is not None, known


def test_a_stale_cached_card_cannot_outrank_a_newer_bundled_card(
    tmp_path: Path,
) -> None:
    """Regression from a real incident: a catalog card cached 2026-07-09
    silently overrode a corrected bundled card, so a shipped price fix reached
    nobody who had ever pulled a catalog. Both of our cards are date-stamped,
    so the newer one wins; an org's curated card is not date-stamped and keeps
    its override."""
    from practicegraph.analysis.ratecard import (
        BUNDLED_RATE_CARD,
        CATALOG_DIR_NAME,
        RATE_CARD_FILE_NAME,
        activate_rate_card_from,
        active_rate_card,
        rate_card_artifact,
        reset_active_rate_card,
    )

    catalog_dir = tmp_path / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True)
    path = catalog_dir / RATE_CARD_FILE_NAME

    def write(version: str, rates: dict[str, object] | None = None) -> None:
        doc = rate_card_artifact()
        doc["rate_card_version"] = version
        if rates is not None:
            doc["rates"] = rates
        path.write_text(json.dumps(doc), encoding="utf-8")

    try:
        # A stale dated card loses to the newer bundled one.
        write("bundled-2026-07-03")
        assert activate_rate_card_from(tmp_path) == "bundled"
        assert active_rate_card().version == BUNDLED_RATE_CARD.version

        # A newer dated card wins — serving still works.
        write("bundled-2099-01-01")
        assert activate_rate_card_from(tmp_path) == "catalog"
        assert active_rate_card().version == "bundled-2099-01-01"

        # An org's curated card is not date-stamped and always overrides.
        write("acme-internal-v3")
        assert activate_rate_card_from(tmp_path) == "catalog"
        assert active_rate_card().version == "acme-internal-v3"
    finally:
        reset_active_rate_card()


def test_rate_card_age_makes_staleness_visible() -> None:
    """Staleness is the one pricing failure the product cannot detect from its
    own numbers — a stale card is confidently wrong, not empty. The age is
    therefore computed and surfaced instead of inferred."""
    from datetime import date as _date

    from practicegraph.analysis.ratecard import (
        RATE_CARD_STALE_DAYS,
        rate_card_age_days,
    )

    published = _date(2026, 7, 25)
    assert rate_card_age_days("bundled-2026-07-25", published) == 0
    assert rate_card_age_days("bundled-2026-07-25", _date(2026, 8, 1)) == 7
    # The real incident: this card was already wrong six days later.
    assert rate_card_age_days("bundled-2026-07-03", _date(2026, 7, 9)) == 6
    # Boundary either side of the stale threshold.
    from datetime import timedelta as _td

    assert (
        rate_card_age_days("bundled-2026-07-25", published + _td(days=RATE_CARD_STALE_DAYS))
        == RATE_CARD_STALE_DAYS
    )
    # A clock skewed backwards reports 0, never a negative age.
    assert rate_card_age_days("bundled-2026-07-25", _date(2026, 1, 1)) == 0
    # An org's curated card carries no date and cannot be aged.
    assert rate_card_age_days("acme-internal-v3", published) is None
    assert rate_card_age_days("bundled-not-a-date", published) is None


def test_the_published_artifact_matches_the_bundled_card_exactly() -> None:
    """`site/rate-card.json` is the file served to every installation, and it
    is synced by hand into the site repo. Drift between it and the bundled card
    is silent and expensive: the served card WINS over the bundled one when it
    is newer, so a stale or hand-edited artifact mis-prices every window without
    a single error. Two real defects on 2026-07-24 were exactly this class —
    a prefix matching an older generation's rates, and a cached card outranking
    a corrected bundled one — and together they understated lifetime spend by
    about 5%.

    So the artifact is pinned here, and it must also survive the product's own
    strict validator: whatever is published has to be something the client
    would actually accept."""
    import dataclasses
    import json
    from pathlib import Path

    artifact_path = Path(__file__).resolve().parent.parent / "site" / "rate-card.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert artifact["rate_card_version"] == BUNDLED_RATE_CARD.version
    bundled = {
        prefix: dataclasses.asdict(rate) for prefix, rate in BUNDLED_RATE_CARD.rates
    }
    assert artifact["rates"] == bundled

    # And it round-trips through the same closed-schema parser the endpoint
    # uses, to the same PRICING — not the same representation. The authored
    # table is hand-ordered; the parser re-sorts longest-prefix-first, which is
    # what makes specificity survive a trip through JSON (object key order must
    # never decide whether `gpt-5-mini` or `gpt-5` matches first).
    parsed = parse_rate_card_artifact(artifact)
    assert parsed is not None
    assert dict(parsed.rates) == dict(BUNDLED_RATE_CARD.rates)
    lengths = [len(prefix) for prefix, _ in parsed.rates]
    assert lengths == sorted(lengths, reverse=True)

    # The property that actually matters: every model family seen in the wild
    # prices identically under the published card and the bundled one.
    def price(rates: tuple[tuple[str, object], ...], model: str) -> object:
        for prefix, rate in rates:
            if prefix_matches(model, prefix):
                return rate
        return None

    for model in (
        "claude-opus-4-1-20250805", "claude-opus-5", "claude-sonnet-4-5",
        "claude-fable-5", "claude-3-5-haiku-20241022",
        "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5-mini", "gpt-5",
        "totally-new-model",
    ):
        assert price(parsed.rates, model) == price(
            BUNDLED_RATE_CARD.rates, model
        ), model
