# Content formats

The app downloads a small set of JSON editions from its content host and reads
each one with a closed production parser. The parser is the contract: a field it
does not know, a value outside its bounds, or a malformed reference rejects the
whole edition, and the last valid local copy stays in place. New content is data,
never executable instructions. A capability the parser does not describe needs
a client release first.

| Channel | Served file | Production contract |
| --- | --- | --- |
| news | `news.json` | `analysis/news.py`; attention fields in [News notifications](NEWS_NOTIFICATIONS.md) |
| build ideas, including repository picks | `build-ideas.json` | `analysis/build_ideas.py` |
| community | `community.json` | `analysis/community.py` |
| models (benchmark measurements) | `models.json` | `analysis/model_intelligence.py` |
| model catalogs | `model-catalog.json`, `model-catalog-productivity.json` | `analysis/model_catalog.py` |
| docs | `docs.json`, `docs-productivity.json` | `analysis/docs.py` |
| rate card | `rate-card.json` | `analysis/ratecard.py` |
| token prices | `token-prices.json` | `analysis/token_prices.py`, `analysis/token_price_contract.py`; see [Token price tracker](TOKEN_PRICES.md) |
| advisor | `advisor.json` | `analysis/advisor_market.py`; see below |
| harness playbooks | `harness-playbooks.json` | `analysis/harness_playbooks.py`; see [Harness playbooks](HARNESS_PLAYBOOKS.md) |
| training | `training.json` | `analysis/training_catalog.py`; see [Learning paths](TRAINING.md) |
| skills | `skills.json` | `analysis/skills.py`; see [Skill catalog](SKILL_CATALOG.md) |

Contract paths are relative to `src/practicegraph`. `src/practicegraph/content.py`
registers every channel with its parser, version field and review window.
Public editions arrive inside the signed envelope described in
[Catalog encryption](CATALOG_ENCRYPTION.md); the parsers read the JSON inside it.

Rules shared by every channel:

- Unknown is `null` or absent, never zero. Only a sourced zero means free.
- Changed content needs a new edition version. The same version with different
  bytes is refused, and downloading an old edition again never makes it fresh.
- Every edition carries its own date. The UI names the source and date and flags
  editions past their review window (3 days for news, 7 for build ideas and
  community, 14 for model guidance, 30 for docs).
- Links are public HTTPS without credentials, query strings or fragments.

## Advisor evidence contract

`practicegraph.advisor/2` has the root fields `schema`, `artifact_version`,
`as_of`, `expires`, `models`, and `verdicts`. There is no global source
attribution. A model has a unique supported `family`, an exact `model_id`, and a
`metrics` object with at least one supported measurement.

Each known metric contains `value`, `source_name`, `source_url`, `observed_on`,
and `basis`. Observation dates cannot postdate the edition. `basis` names the
benchmark method and configuration, or the API pricing conditions; automatic
comparisons need equal price bases.

| Metric | Value |
| --- | --- |
| released_on | ISO date, no later than its observation date |
| coding_index_tenths, agentic_index_tenths | The named index multiplied by ten |
| price_in_micro, price_out_micro, price_cache_read_micro | Integer micro-USD per million tokens |
| median_tps_tenths | Output tokens per second multiplied by ten |
| ttft_ms | Time to first token in milliseconds |
| context_window | Tokens |

A synthetic price measurement:

```json
{
  "price_in_micro": {
    "value": 2000000,
    "source_name": "Example provider",
    "source_url": "https://provider.example.org/docs/pricing",
    "observed_on": "2026-09-06",
    "basis": "Standard API, USD per million tokens, up to 200K context"
  },
  "coding_index_tenths": null
}
```

Mutable measurements stop supporting recommendations 14 days after observation;
the release date does not age out. Whole-edition expiry gates every market
claim. Cached-input rates are needed only for volumes containing cached tokens.
The contract has no cache-write prices, so such volumes suppress the routing
estimate instead of pricing them at the input rate. API equivalence is not a
subscription bill or a measured quality improvement.

Verdicts carry `id`, `tier`, `families`, `tools`, `verdict`, `boundary`,
`steelman`, `action`, `experiment`, `as_of`, `expires`, and a nonempty `evidence` list of
`{"family": "...", "metric": "..."}` references. References must belong to the
verdict's families and point to measurements current on its review date; only
such verdicts appear, and their visible deadline is capped by both edition and
measurement expiry. The client reads v1 and v2; an older client rejects v2 and
keeps its last valid cache.
