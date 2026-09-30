# Understanding Usage

Usage answers three questions: how much activity is recorded, where its listed-rate
estimate comes from, and what evidence is worth investigating next. Counts do not
measure completed work, correctness, productivity, or skill.

## Periods and prices

One UTC period controls totals, the trend, model breakdown, sessions, and recorded
session families. Session timestamps display in the viewer's local timezone. A
session crossing the period boundary contributes only its recorded activity within
that period. “All retained history” covers the local store, which may have been
pruned; it is not necessarily all usage of the account. Long trends use monthly
buckets. Sessions are paginated in descending order of listed-rate estimate.

Amounts use the active rate card. Subscription users see API-equivalent usage;
this is not their bill or marginal subscription cost. Unpriced turns are identified
separately. Reasoning is a subset of output and is counted once in totals, trends,
and report token composition. One-hour cache creation is likewise a subset of
total cache creation.

## Quota provenance

Codex primary and secondary log snapshots are independent readings. Each retains
its window duration and kind, observation timestamp, and reset timestamp when
provided. Provider-declared account and allowance-pool identifiers are reduced to
local hashes. Missing identity stays unknown: the client never guesses an account
from a project, session, or model. Accounts and allowance pools are not merged.

Allowance readings are separate from the historical period selector. Older
readings are labeled, and a passed reset is not treated as evidence of a new
allowance balance. Earlier peaks are kept folded because they may precede a reset.
No provider credentials are read and no live quota endpoint is called.

## Forks and session families

The Codex parser uses explicit `forked_from_id` metadata and the available parent
log to identify a matching leading record sequence. Top-level timestamps can be
rewritten by the provider, so those timestamps do not establish replay identity.
The observed absent-versus-null `content` serialization of reasoning records is
normalized. No elapsed-time cutoff is used.

Only a matching prefix through a token-count record is excluded. Its cumulative
token baseline is retained for the child's new work. Copied session headers cannot
rename the child. Different content ends matching; matching cumulative totals alone
never establish that a later request is a duplicate. Missing, ambiguous, or cyclic
parents cannot authorize dropping usage. A changed dependency causes the child to
be reconsidered on ingestion, including when its parent appears or disappears.

This is conservative reconciliation, not a guarantee for every provider format.
An unconfirmed fork is flagged because replay may remain in its totals. A changed
or truncated parent can limit what is provable. Duplicate exports with no explicit
relationship are not inferred to be the same session.

Families follow provider parent links, including Codex
`source.subagent.thread_spawn.parent_thread_id`. Forks and delegation are labeled
separately. They are not groups inferred from time proximity or shared branches,
and they are not confirmed tasks. A family total includes each available member
once within the selected period. Claude transcript composition is supported; this
release does not infer Claude agent ancestry from transcript ordering or filenames.

## Transcript composition

Session investigation shows integer character counts in five closed categories:
visible instructions, user-role messages, assistant messages, tool arguments and
results, and other visible text. Approximate tokens use four characters per token.
Claude streaming revisions use the last record for a message/request identity.

This is the latest available transcript file at its last scan, including inherited
and earlier compacted material. If a session spans multiple files, this view uses
the latest file's composition; accounting still sums its retained files. It is not
the provider's current context window.
Metadata-only instructions, hidden material, tool schemas, images, and format
overhead are not allocated. The latest provider-recorded input-token count is
shown independently and never used to rescale the categories into a falsely exact
allocation. Composition may extend outside the selected accounting period.

Only counts, timestamps, local correlation identifiers and bounded structural
metadata are retained. The new authenticated `/api/usage` endpoint reads stored
counts, accepts a closed period/page/session query, and cannot open a supplied
file path. Session IDs exposed to this view are opaque hashes. Raw prompts,
commands, outputs, and file contents are not retained for composition. These
details, quota provenance, and session-family data are excluded from shared emits.
Deletion, replacement and retention pruning follow the source file's lifecycle.

## From evidence to guidance

Sessions with recorded file changes and no visible test attempt can point to the
existing project setup check. This is a coverage gap in the available logs, not a
claim that tests failed or were never run. Namespaced Codex shell tools, `cmd` and
`command` arguments, and custom apply-patch calls are supported. Arbitrary scripts,
wrapped JavaScript orchestration and external checks may remain unclassified.

The project inspection checks whether a current, compatible curated verification
playbook applies. The existing workflow retains the inspected baseline, dated
sources, reviewed brief, explicit attempted change, and three later comparable
observations. Copying a brief does not mark it attempted. Missing or stale editions
are visible and cannot authorize a new handoff. Later observations are not causal
proof and a recorded test attempt is not a passing test result.

Curated editions remain independently authored and delivered as JSON. The client
does not invent substitute advice when an edition is missing. Live quota
integrations and additional provider adapters remain separate future work.
