# Improve my setup: delivery plan

Prepared September 5, 2026, following the owner's request for an implementation
approach. The first client journey and the Codex authoring workflow are now
implemented on `codex/setup-improvements`. This document retains the delivery
design. Official edition creation, publication, and the voluntary pilot remain
separate delivery steps. See [the implemented contract](HARNESS_PLAYBOOKS.md).

## Product outcome

Help a person make one concrete improvement to their AI working environment,
using evidence from a selected project and maintained editorial guidance. Keep
the proposed change, the person's assessment, and later observations together.

First complete journey:

**Notice a verification gap → select and inspect the project → review a
verification brief → use it in the existing harness → record what happened.**

The first release supports coding projects using the existing Claude Code and
Codex adapters. Begin with Python and JavaScript/TypeScript project manifests.
Unknown stacks remain usable through an explicit insufficient-evidence state.

## What we can reuse and what must change

| Existing implementation | Reuse | Required addition |
| --- | --- | --- |
| `analysis/capability.py` | Closed observations, one practice, feedback, later audit | Project-specific evidence and arbitration with existing coach prompts |
| `analysis/capability_units.py` and `analysis/workunits.py` | Work episodes, failures, retries, edit and test-attempt markers | Preserve project identity and source coverage in the improvement summary |
| `analysis/harness_inventory.py` | Observed tool versions and installed components | Treat unreadable or unrecognized versions as unknown compatibility |
| `content.py`, `catalog.py`, content release utilities | Strict contracts, bounded downloads, cached editions, provenance | A separate versioned `harness-playbooks.json` channel |
| Practice UI and authenticated local API | Existing layout and local command boundary | One setup-improvement card with an inspect/review/follow-up flow |
| SQLite and practice-time journal | Durable local persistence and explicit backup patterns | Separate improvement records and optional links to confirmed practice sessions |

Current capability summaries drop project identity, and follow-up matching uses
broad work categories. Extend that path deliberately; comparing unrelated
projects would make the proposed results misleading. A working-directory hash
also does not establish repository equivalence across subdirectories/worktrees.
Require a verified mapping, or report that project matching is unavailable.

The store intentionally does not retain raw project paths. A selected folder
can be held transiently for an inspection, with only its existing identity hash
and closed findings retained. A name explicitly entered by the user identifies
the check in their local history; it is not derived from a filesystem path.
Reinspection after restart requires selecting the folder
again. Do not introduce persistent raw paths as an incidental implementation
detail.

## Milestone 1: establish reliable evidence

Build a small local inspection module and project-scoped observation summary.

- Use existing recorded edits and test attempts to flag a possible verification
  gap. Say "no test run visible"; an absent marker does not prove no testing.
- The person selects the project and sees the inspection scope before it runs.
- Bound file count, depth, bytes, and parsing time. Resolve paths and refuse
  symlink/junction escapes beyond the selected root. Exclude secret files,
  dependencies, generated outputs, and unrelated directories.
- Inspect known manifests and specifically selected project guidance. Treat all
  contents as data. Do not execute scripts, install dependencies, or start an
  agent during inspection.
- Record closed findings such as a declared test command, missing documentation,
  conflicting declared commands, or unavailable evidence. Command discovery
  does not establish that a command works or is safe to run.
- Associate an observation with the selected project only when identity mapping
  is supported. Missing history must not appear as a zero failure rate.

**Exit gate:** synthetic Python/JS projects produce reproducible findings;
unsupported, unreadable, and ambiguous cases produce honest unknown states;
unrelated projects and files cannot enter the reading.

## Milestone 2: deliver one useful improvement

Start with a project-specific verification brief. It should state the observed
gap, the declared checks found, the relevant curated guidance, and a task the
person can give their coding harness to establish and document a working check.
Do not claim to have run or validated that check.

Use the existing Practice page with one card:

1. **Observation:** scoped evidence, source coverage, and the proposed next step.
2. **Inspect this project:** explicit selection and bounded local reading.
3. **Review the improvement:** exact brief, supporting evidence, and uncertainties.
4. **Copy task / export brief:** a usable artifact for the person's existing harness.
5. **Follow up:** "Tried it", "Not tried", or "Dismiss", followed by usefulness.

Make the brief concrete before the handoff. Copying or downloading does not
mean the person applied it. Direct repository edits and automatic harness
execution are subsequent capabilities, not requirements for this first release.

Coordinate with the existing capability coach: a setup-improvement journey
replaces a competing recommendation for the same observation. Show at most one
active improvement, with folded evidence and history. Do not add a navigation
category or a setup score.

### Editorial contract and maintenance

Keep code, detector implementations, supported recipe IDs, parser, renderer,
and synthetic fixtures in the open application. Keep research, candidate
selection, testing notes, and approved-edition archives in the private editorial
workspace described in [HOSTED_CONTENT.md](HOSTED_CONTENT.md).

A proposed playbook entry includes:

- Stable ID and revision; supported client schema version.
- Closed detector and recipe IDs recognized by the installed engine.
- Supported project stacks and harness/version ranges, including explicit
  version-independent applicability where appropriate.
- Explanation, source links, review date, and limitations.
- Retirement status and successor reference when applicable.

The first edition needs only three carefully reviewed entries: verification
setup, repeated command-failure investigation, and project instruction mapping.
Only verification setup is activated in the first complete journey. Test the
contract using synthetic entries before any official edition is published.

Hosted entries select supported capabilities and supply explanatory guidance;
they cannot define new filesystem access, executable detectors, shell commands
to auto-run, or permission changes. Extend the existing release validator,
manifest inventory, download receipt, host override, stale/empty UI, and retained
valid-cache behavior. Compatibility must be checked before offering a recipe.

Freeze the accepted edition/digest with each improvement so later edits cannot
rewrite its history. A withdrawal blocks new handoffs and is visible on an
existing improvement. Editorial dates remain review metadata, not authentication.

**Exit gate:** the user can obtain a useful project-specific brief from a valid
catalog, understand why it was suggested, and dismiss it. Offline, stale,
incompatible, withdrawn, malformed, and missing catalogs behave predictably.
No private research is included in the app's public-source inventory.

## Milestone 3: close the loop and pilot

Persist the observation, project identity, declared context, selected recipe
revision, inspection findings, brief digest, explicit attempted date, and user
feedback in a dedicated local ledger. Keep it independent of the coach's
48-entry feedback retention and the practice-time totals.

Use an explicit state machine: observed → inspected → prepared → attempted →
reviewed, with dismissed and insufficient-evidence states. Preserve history
across restarts, support corrections and export/restore, and make retries
idempotent. Clear operations need separate, explicit scope.

After an attempt, show the next three comparable changes in that same project
alongside up to three preceding comparable changes. For the initial recipe,
the measurement is whether a verification attempt is visible. State sample
sizes and observation coverage. Do not turn an attempted check into a passing
test, a skill score, or a causal claim about the recipe.

Record harness/model context where available. Material context changes or weak
project/work-type matching produce "not comparable" or "too little evidence".
Show the person's helpful/not-helpful/unsure assessment separately. They can
save the result in their private playbook and optionally link an independently
confirmed practice session; no extra hours are awarded automatically.

**Exit gate:** one real project completes observation through personal review;
the record survives restart and backup/restore; cross-project activity cannot
alter its result; aggregate payloads remain byte-identical with and without it.

## Validation and release order

1. Fixtures, project identity rules, and the contract/evidence tests.
2. Local inspector and verification-brief generation with synthetic playbooks.
3. Authenticated API, Practice card, and durable improvement ledger.
4. Catalog download/cache and private editorial preparation using the same schema.
5. Full journey on this repository, then a small voluntary pilot.
6. Publish the compatible client and approved official edition; verify the public
   source export, installed build, live edition, and actual fetched receipt.

Use focused backend/UI tests for matching, unknowns, stale state, duplicate
actions, restore conflicts, path containment, hostile content, and privacy.
Exercise narrow and desktop layouts, keyboard controls, empty history, and a
completed follow-up. Run the established full gates before release. Publication
is a separate delivery action; this plan does not claim any live change.

Pilot success means users understand the observation, actually try a specific
change, and report usefulness or a clear mismatch. Record recurring false
positives and unsupported setups. Keep personal evidence local; obtain pilot
feedback explicitly rather than adding background outcome uploads.

Only after this journey proves useful, activate repeated-failure investigation
and instruction-mapping recipes. Direct patch application, broad repository
scanning, model selection advice, and cross-harness orchestration follow their
own evidence and product decisions.
