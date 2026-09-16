# Maintaining the hosted content

Codex skills author editions in the private editorial workspace. The open client
owns the contracts, matching, and validation. Each feed has a distinct purpose:

| Channel | Maintainer skill | Production contract |
| --- | --- | --- |
| news | curate-practicegraph-news | analysis/news.py |
| build-ideas, including repository picks | build-ideas | analysis/build_ideas.py |
| community | curate-practicegraph-community | analysis/community.py |
| models (benchmark measurements) | curate-practicegraph-models | analysis/model_intelligence.py |
| model-catalog and model-catalog-productivity | curate-practicegraph-model-catalogs | analysis/model_catalog.py |
| docs and docs-productivity | curate-practicegraph-docs | analysis/docs.py |
| rate-card | curate-practicegraph-rate-card | analysis/ratecard.py |
| token-prices | curate-practicegraph-token-prices | analysis/token_prices.py and analysis/token_price_contract.py |
| advisor | curate-practicegraph-advisor | analysis/advisor_market.py |
| harness-playbooks | curate-practicegraph-harness-playbooks | analysis/harness_playbooks.py |
| training | curate-practicegraph-training | analysis/training_catalog.py |
| skills | curate-practicegraph-skills | analysis/skills.py and the registry compiler |

Contract paths are relative to `src/practicegraph`. Skills are maintainer tooling;
their canonical copies and installation map live in the private editorial
workspace's `skills/` directory. Installed copies are checked against that source
by its `sync.py`. The reusable contracts and validators remain open source.
Maintainer prompts, source-selection lists, drafts, and review notes are excluded
from the public source export. The export gate refuses maintainer skill paths.

New edition workflows produce both a readable private draft and a signed,
encrypted delivery file by invoking `tools.catalog_seal` themselves. See
[catalog encryption](CATALOG_ENCRYPTION.md) for key custody, previous-edition
reading, retry behavior and the client-first production rollout requirement.
The common validator accepts either representation. Do not overwrite a sealed
output with an older plaintext-writing helper. The public reusable skill
registry retains its compiler contract; seal a separate hosted delivery copy
when selecting that source, without claiming to hide public skill definitions.

## Review a draft

Use the existing channel tool where it offers a useful preview. Every registered
channel also has a common read-only validator:

```powershell
.venv/Scripts/python.exe -m tools.content_release validate --channel docs --draft ../practicegraph-site/editorial/drafts/docs.json --against ../practicegraph.dev/docs.json
```

Omit `--against` for a first edition. The command bounds input, uses the production
parser, rejects future edition dates and changed JSON under an existing version,
and reports the digest and review status. Harness playbooks additionally enforce
entry revisions and explicit withdrawals. Passing validation does not establish
editorial correctness or publication approval.

Keep evidence and review notes beside the private draft: source URL, access date,
the supported claim, unresolved ambiguity, and the exact draft version/digest.
Reusing old research does not justify advancing its review date. Missing prices,
measurements, or compatibility evidence stay missing; do not fill them with zero.

## Deliver an authorized edition

Use the owner's current authorization and the channel workflow. Draft/review-only
requests remain local. When delivery is authorized, replace only the reviewed
channel in the verified website worktree, add its filename to the deployment
allowlist, and run:

```powershell
.venv/Scripts/python.exe -m tools.content_release prepare --site ../practicegraph.dev --archive ../practicegraph-site/editorial/releases
.venv/Scripts/python.exe -m tools.content_release check --site ../practicegraph.dev
```

The gate includes the deployment allowlist check, so a new valid file cannot be
silently omitted by the website's `/*` exclusion. Commit the changed channels,
manifest, and required deployment-list changes together. Preserve unrelated
website work. The website's configured production branch determines whether a
push becomes the public edition; a local branch or preview deployment is not a
production release. Verify the live files and manifest against the reviewed
hashes, plus removal of any obsolete public side paths.

The canonical build feed is `build-ideas.json`. A local override to a preview
filename must be removed only after the canonical edition serves the intended
repository picks. Keep a private backup of a changed local setting and preserve
all unrelated configuration.

## Known contract limits

- Model catalogs have three unique roles, so they are an editorial selection,
  not a complete provider model inventory. Use only verified pin identifiers;
  omit a pin when availability or identity is uncertain. Effort is currently
  defined per harness rather than per model; list a supported subset for models
  that have the dial and identify exceptions such as Haiku. The recommended
  effort must work for the everyday model used by the pin button. Explain that
  the actual picker and per-model overrides determine the active setting.
- Rate cards describe standard API token prices. The closed format has no
  context-tier, region, priority, or negotiated-price fields. Document the
  standard-context scope in the review; do not claim exact billing or subscription
  credit equivalence. Preserve historically used model entries and record which
  ones were reverified versus retained as historical references.
- Advisor v2 accepts partial measurements with individual sources. Unknowns are
  omitted or null, never zero. Its v1 reader preserves existing Artificial
  Analysis editions and their original attribution. See the contract below.
- Community may preserve dated historical observations with their limitations.
  A current review must read the discussion, not just its title or old summary.
- New feed content is never a mechanism for adding executable client logic.

## Advisor evidence contract

New drafts use `practicegraph.advisor/2`. Root fields are `schema`,
`artifact_version`, `as_of`, `expires`, `models`, and `verdicts`. There is no global
source attribution. A model has a unique supported `family`, an exact `model_id`,
and a `metrics` object. At least one supported measurement is required per model.

Each known metric contains `value`, `source_name`, `source_url`, `observed_on`,
and `basis`. Source URLs must be public HTTPS links without credentials, query
parameters, fragments, or nonstandard ports. Observation dates cannot postdate
the edition. `basis` identifies the benchmark method and configuration, or the
API pricing conditions; equal price bases are required for automatic comparisons.
Use identical basis text only when those conditions actually match.

Supported keys and units:

| Metric | Value |
| --- | --- |
| released_on | Verified ISO date, no later than its observation date |
| coding_index_tenths, agentic_index_tenths | The named index multiplied by ten |
| price_in_micro, price_out_micro, price_cache_read_micro | Integer micro-USD per million tokens |
| median_tps_tenths | Output tokens per second multiplied by ten |
| ttft_ms | Time to first token in milliseconds |
| context_window | Tokens |

For example, a synthetic price measurement has this shape:

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

Use primary provider documentation for prices, model identity, context, and
release dates. Use the benchmark publisher for measured performance. Preserve
the exact metric meaning: Intelligence Index is not Coding Index or Agentic
Index. The client validates structure and attribution metadata; the curator
still verifies that the cited page supports the value and scope.

Mutable measurements stop supporting recommendations 14 days after observation,
even if a later edition retains them. The verified release date is historical
and does not age out. Whole-edition expiry still gates every market claim.
Omitting a benchmark does not prevent price comparisons. Routing trials use
comparable prices and observed token volumes, with no inferred capability rank.
Cached-input rates are needed only for volumes containing cached tokens. The
contract has no cache-write prices, so such volumes suppress the routing estimate
instead of being priced at the input rate. API equivalence is not a subscription
bill or a measured task-quality improvement.

Verdicts retain the v1 copy fields and add nonempty `evidence`: a list of
`{"family": "...", "metric": "..."}` references. References must belong to the
verdict's families and point to present measurements current on its review date.
Only verdicts whose cited measurements remain current can appear. Their visible
review deadline is capped by both edition and measurement expiry. References
support the editorial claim; unrelated citations do not make prose verified.

`curate_advisor preview` exposes unknown fields, sources, observation dates, and
measurements unavailable today. The report and view-model expose only the
measurements each surfaced recommendation used. Main-app advisor cards remain
removed as previously requested; this contract change does not restore them.

The new client reads v1 and v2. An older client rejects v2 safely and retains its
last valid cache. Release a client supporting v2 before making it the public
canonical advisor feed. Keep a first v2 edition private until that deployment is
ready; do not rewrite the expired v1 edition with fresh dates.
