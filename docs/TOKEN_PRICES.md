# Token price tracker

API Prices shows one row per model. The headline price is the developer's own
standard first-party offer (default cache duration, lowest input band, dearer
tariff when a tariff varies). Models with no first-party seller show the lowest
direct standard host price marked "from"; router routes (OpenRouter) qualify only
when no direct host prices the model. Expanding a row lists every priced offer
with its host or pinned endpoint, tier, precision, cache duration, input band and
the terms, sources and history behind it. Filters cover provider, serving tier,
open weights and freshness; models with no matching offer are hidden. Input,
output, cache read and cache write remain separate USD-per-million prices. Null
is unknown; only a sourced zero is displayed as free. The provider directory
accepts provider IDs from editions. Providers without a verified quote retain their coverage status and
explanation.

## Where a model is cheaper

The app computes, never hand-writes, where a host undercuts the developer's
own price. For every model with a first-party offer, any direct, available,
standard-tier host whose input **and** output rates are both below the
developer's lowest own rate (across tariff windows and variants) is flagged: the model row says "cheaper at <host> · output N% less", the Hosts
cell carries a "N hosts cheaper" chip, and the expanded host row shows both
savings against the developer. Routers, batch and priority tiers and preview
offers never qualify. Precision, context limit and region stay visible beside
the flag because a cheaper quantized copy is a different product; the row's
headline stays the developer's price. Models with no first-party seller show
the lowest direct host price marked "from" and carry no flag.

Editions may still carry `benchmarks` and `offer_evidence` for compatibility;
the app ignores them.

## Production contract

`analysis/token_price_contract.py` validates the research snapshot shape. `analysis/token_prices.py` registers the production reader. Production
schema `practicegraph.token-prices/2` has the same fields as the research
snapshot, plus required `history`: up to 12 chronological, complete research
snapshots preceding the current observation. The version field is
`edition_version`; the edition date comes from `observed_at`. Research/production
v1 remain readable and can be retained unchanged in v2 history. A v1 edition shows
the price board and directory with an honest awaiting-scores recommendation state.

The reader bounds the whole feed at 4 MiB, rejects unknown fields, malformed
prices, bad references, future observations, invalid intervals, changed
identities and nonchronological history. Production strings are bounded at
2,000 characters and timestamps use second-resolution UTC ending Z.

Rates are decimal strings. History compares the same stable offer identity;
changes to region, tier, precision, context band, cache TTL or reasoning billing
require a new offer ID. Copy edits do not establish an earlier price. Missing
offers say not reverified, not withdrawn. First editions have no price deltas.
Billing-condition changes are flagged alongside observed rate changes.

Quotes or their pricing-source checks older than 48 hours display a freshness
notice without disabling comparisons. Every current model must have a sourced
input/output-priced offer; unpriced models and offers are omitted from selectors
and the board. Optional unknown cache rates block only workloads needing them.
Future-effective and expired prices are labeled separately. Freshly downloading
an old edition never refreshes its observation timestamps.

## Runtime

Default host path is `/token-prices.json` on the configured content host.
`PRACTICEGRAPH_TOKEN_PRICES_URL` / config `token_prices_url` overrides it, with
the normal environment > file > default precedence. Explicit content-base
overrides also work. Enterprise mode skips the default public pull unless a
source was explicitly configured.

Agent ticks and dashboard background refreshes attempt a bounded pull every
15 minutes at most. Invalid, unavailable, rewritten or backward editions
leave the last valid cached file intact. `GET /api/token-prices` is a read-only,
token/host-gated local endpoint; it performs no provider requests or log scan.
The Models screen rereads the local endpoint once per minute while mounted.
The existing usage-accounting `rate-card.json` and historical usage costs are
unaffected. The app makes no provider or paid inference calls for this page.
