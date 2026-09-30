# Harness playbooks

The app consumes **`harness-playbooks.json`** from a static content host. No
content-generation service or provider call is needed in the app.

The first implemented recipe is **verification setup**. The schema reserves
failure investigation and instruction mapping; those entries can be authored
but do not produce client recommendations yet.

## Contract

Top-level fields are exact: `schema` (`practicegraph.harness-playbooks/1`),
`playbooks_version` (a unique edition string, maximum 64 characters),
`published_on` (ISO date), and `entries` (up to 100). The download limit is 512 KB.
Unknown fields and executable extensions are refused by the production parser.

Each entry has these exact fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable lowercase letters/digits/hyphens, starting with a letter, up to 64 characters |
| `revision` | Positive integer; increase when any entry field changes |
| `recipe`, `detector` | A recognized pair from the table below |
| `stacks` | Unique nonempty selection of `python`, `javascript` |
| `harnesses` | Unique supported tools, each with `tool`, `min_version`, `max_version` |
| `title` | Plain text, up to 120 characters |
| `explanation` | Why this remedy is relevant, up to 1,000 characters |
| `guidance` | Reviewed explanatory guidance, up to 2,000 characters |
| `limitations` | What the remedy/evidence does not establish, up to 1,000 characters |
| `sources` | One to five HTTPS references, without credentials/query/fragment |
| `reviewed_on` | ISO date, no later than the edition publication date |
| `status` | `active` or `withdrawn` |
| `successor` | Another entry ID or `null` |
| `min_client_recipe_version` | Positive integer; the current engine supports `1` |

| Recipe | Detector | Client behavior |
| --- | --- | --- |
| `verification_setup` | `verification_gap` | Implemented |
| `failure_investigation` | `repeated_failures` | Reserved; no handoff |
| `instruction_map` | `instruction_gap` | Reserved; no handoff |

Harness tools are `codex` and `claude_code`. Both version bounds are inclusive
numeric `major.minor.patch` strings, or `null`. Two nulls explicitly declare
version-independent guidance. Missing installed versions and prerelease labels
do not establish compatibility with a bounded entry. Do not invent compatibility
ranges from a model's name or an unrelated product version.

Guidance is rendered as text. Entries cannot define shell execution, permissions,
filesystem scope, detectors, or new client logic. New capabilities need a client
release. User-facing briefs preserve the accepted entry and its digest.

## Client behavior and privacy

The existing content-base override covers this channel. An explicit
`PRACTICEGRAPH_HARNESS_PLAYBOOKS_URL` or `harness_playbooks_url` config field wins.
The agent and dashboard background worker refresh at most every 15 minutes.
Failed refreshes preserve the last valid edition and its provenance receipt.

Editions and individual entries older than 30 days are review-due and cannot
produce new handoffs. Missing, incompatible, changed, and withdrawn guidance
has explicit UI states. Existing records keep their frozen guidance. The first
release requires a current compatible edition before a brief can be prepared.
The receipt is provenance metadata, not a publisher signature.

In **Practice → Improve my setup**, name a check and enter an absolute local
folder path. Only the named root manifests/guidance and lockfile presence are
inspected. Commands and source contents are never executed or retained. The
user-entered check name, closed findings, file digests, and identity hashes stay
in the local improvement journal. Raw folder paths are transient and are not
derived into saved labels. Reselect the folder for another inspection.

Matching uses exact working-directory identity variants. Subdirectories,
different worktrees, and unrelated folders are not silently merged. Follow-up
uses edited, idle work episodes and conservative recorded tool/model context;
test attempts do not establish test success or learning. A copied brief is
not a recorded attempt. Feedback can be corrected separately from observations.

The journal uses additive SQLite schema 15. Export/restore merges exact duplicate
IDs once, rejects conflicts atomically, and allows at most one active improvement.
The UI shows the latest 50 historical records; exports include all records.
Imports support up to 5,000 records and 16 MB. Practice-session links add context
without changing hours. Setup history is excluded from shared aggregate payloads.

No official edition is bundled. Synthetic examples exist only in tests.
