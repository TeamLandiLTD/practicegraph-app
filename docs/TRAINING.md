# Learning paths

Practice includes an optional, collapsed Learning paths section. It offers one
next step and at most two matching alternatives from a maintained catalog. It
uses explicit learning preferences, not a diagnosis from activity logs.

Choose a goal, coding tool, free-only preference and optional total time limit.
Goals are verifying AI-generated work, using a coding harness, building/evaluating
agents, and exploring a professional certification. Unknown cost does not count
as free; unknown duration does not meet a time limit. Certifications are only
eligible for the certification goal. No matching/current edition means an honest
empty state, not a manufactured recommendation.

One item can be saved or in progress across browser windows. Start, complete or
set it aside explicitly. Completion is self-reported, not provider-verified or a
proficiency score. Course links and completion never add practice hours. Use the
existing practice journal separately for actual study time.

Choices and snapshots live in the local SQLite store under a dedicated metadata
key. They never enter fleet aggregates or catalog requests. Export a readable
JSON record or explicitly clear learning choices without clearing practice time.
The first version does not import learning exports or integrate provider accounts.
Up to 100 records are retained; reaching the bound requires an explicit export/
clear decision rather than silently discarding history. Old snapshots survive
catalog changes and are labeled when changed, old or unavailable.

## Maintained catalog

`training.json` is registered in the shared channel registry with a 30-day review
window. The client supports plain JSON and the shared signed/encrypted envelope.
`PRACTICEGRAPH_TRAINING_URL` or `training_url` overrides the URL; the normal content
host override also applies. Default public training pulls are suppressed in
enterprise mode unless explicitly configured. Refreshes happen in background
workers; a view request never waits for training network access.

Production contract: `src/practicegraph/analysis/training_catalog.py`.
The root is exactly `schema: practicegraph.training/1`, `training_version`,
`published_on`, and `entries` (at most 50). Each entry contains exactly:

| Fields | Meaning |
| --- | --- |
| `id`, `revision` | Stable kebab-case identity and positive revision |
| `title`, `provider`, `url` | Resource identity and canonical public HTTPS page |
| `goal`, `tools` | Recognized goal and `codex`, `claude_code`, or singleton `any` |
| `kind`, `credential` | Course/lab/path/certification; none/completion_badge/completion_certificate/professional_certification |
| `summary`, `why`, `prerequisites`, `limitations` | Bounded plain text describing outcome, relevance and limits |
| `duration_minutes`, `duration_basis` | Positive whole total duration with provider/editorial_estimate basis, or null with unknown basis |
| `cost`, `cost_note` | Free/paid/unknown and an explanation distinguishing learning fees, exams and required tool subscriptions |
| `steps` | 1–12 ordered objects with title, HTTPS URL and practical outcome |
| `sources`, `reviewed_on` | 1–5 official evidence links and actual review date |
| `status`, `successor` | Active/retired; optional reference to an active successor |

The validator rejects contradictory professional-certification labels, future
entry dates relative to the edition, invalid URLs and fields, bad duration/basis
pairs, duplicate IDs and invalid successor references. Release comparison
requires a revision increase for changed entries and explicit retention of
retired entries. An old entry is not made eligible by redating the envelope or
edition. Ordering expresses editorial priority; the app invents no match score.

## Authoring skill

Use `curate-practicegraph-training`, maintained privately alongside the other
editorial skills. It must read official curricula and requirements, distinguish
provider facts from our recommendation rationale, validate against the actual
parser and **run the catalog encryption command as part of producing output**.
It should not pad a first edition with unrelated certifications, call a badge an
industry certification, promise employment outcomes, or infer mastery from hours.

Draft and validate with:

```powershell
python -m tools.content_release validate --channel training --draft <private-draft>
python -m tools.catalog_seal --channel training --draft <private-draft> --out <private-prepared-edition>
```

Research, prompts and plaintext drafts stay private; production parser, client
matching and synthetic tests are public. Read [catalog encryption](CATALOG_ENCRYPTION.md)
and [editorial workflows](EDITORIAL_WORKFLOWS.md) for compatibility and delivery.
