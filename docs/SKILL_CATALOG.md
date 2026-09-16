# Skill catalog: host your skills on GitHub Pages

The agent UI shows each person a short list of **skills** — reusable prompts /
workflows — matched to the work they've actually been doing. The catalog of
skills is curated by the org and served to endpoints through the admin server.

This doc covers hosting that catalog as a **GitHub repo published to GitHub
Pages**. The admin server fetches the published JSON, validates it, and
re-serves it at `/v1/catalog/skills`. Endpoints only ever talk to your server —
GitHub is never contacted by a client (NFR-SEC-5). Skills are display text
(a title, a one-line summary, and a copyable prompt), never runnable code.

## How the pieces fit

```
your GitHub repo  ──publish──▶  GitHub Pages (skills.json)
                                      │  server fetches + VALIDATES + caches
                                      ▼
                            admin server  /v1/catalog/skills
                                      │  endpoint pulls on the daily cadence
                                      ▼
                            agent UI  "Skills for your practice"
```

The server validates every fetch with the **same strict rules the endpoint
uses**: closed schema, length caps, closed tag vocabularies, the forbidden-
lexicon scan, and the no-leak patterns. An invalid or careless catalog is
rejected and the server keeps serving the last good one (or the bundled
starter set) — a bad publish can never push unsafe copy to anyone.

## 1. Author `skills.json`

One JSON file, this exact shape:

```json
{
  "skills_version": "acme-skills-2026-07-09",
  "entries": [
    {
      "id": "scope-before-build",
      "title": "Scope a change before writing code",
      "summary": "Turn a vague build task into a short written plan you approve first.",
      "prompt": "Before writing any code, read the relevant files and produce a short plan: the files you will change, the approach, and the one risk most likely to bite. Wait for my approval before editing.",
      "work_types": ["build"],
      "tools": ["any"],
      "role": "engineering",
      "findings": [],
      "dimensions": []
    }
  ]
}
```

### Field rules

| field | required | rule |
|---|---|---|
| `skills_version` | yes | `[A-Za-z0-9._-]`, ≤64 chars. Bump it on every change. |
| `entries` | yes | 1–200 skills. |
| `id` | yes | `[a-z0-9-]`, ≤64, unique within the file. |
| `title` | yes | ≤90 chars. |
| `summary` | yes | ≤200 chars — one line, "what it's for". |
| `prompt` | yes | ≤2000 chars — the reusable prompt the user copies. **Plain text only.** |
| `work_types` | yes | non-empty; each of `any` \| `build` \| `investigate` \| `converse`. |
| `tools` | yes | non-empty; each of `any` \| `claude_code` \| `codex`. |
| `role` | no | ≤40 chars — a label shown for grouping (e.g. `cost`, `review`). |
| `findings` | no | tags that make this skill surface when that finding fires (below). |
| `dimensions` | no | tags that make it surface when that maturity dimension is weak (below). |

All text is scanned: it may not contain the forbidden clinical/behavioral
lexicon or secret-shaped strings. There is **no code/script field** — anything
beyond the fields above is rejected.

### The match tags (how a skill gets picked)

Matching runs **on the endpoint, against the person's own local signals** —
never on the server, never over the wire. The richer you tag a skill, the more
precisely it surfaces. Priority, highest first:

1. **`findings`** — the skill answers a problem happening *now*. Valid ids:
   `interruption_cluster`, `low_cache_reuse`, `context_bloat`, `premium_heavy`,
   `late_night_drift`, `rework_heavy`, `command_friction`,
   `refire_after_failure`, `marathon_session`, `approaching_quota`,
   `approvals_waved_through`.
2. **`dimensions`** — the skill strengthens a measured-weak maturity dimension.
   Valid ids: `deep_work`, `recovery`, `single_threading`, `context_hygiene`,
   `model_economy`, `execution_quality`.
3. **`work_types`** — the skill fits the person's dominant way of working.

A skill tagged `"findings": ["premium_heavy"], "dimensions": ["model_economy"]`
surfaces (and ranks near the top) for someone whose spend is premium-heavy or
whose model-economy score is low. Leave both empty to match on work-type alone.
The UI shows *why* each skill surfaced ("answers premium heavy", "strengthens
model economy", "build work"), so nothing is a black box.

## 2. Publish to GitHub Pages

1. Put `skills.json` in a repo (e.g. `acme/practicegraph-skills`), on the
   branch/folder you'll publish.
2. Repo **Settings → Pages** → deploy from that branch (root or `/docs`).
3. Confirm the file is live: `https://<org>.github.io/practicegraph-skills/skills.json`
   returns the JSON over https.

Because it's a normal repo, curation is a normal workflow: edit, PR, review,
merge. Bump `skills_version` in the same change.

## 3. Point the admin server at it

Set on the server (see `deploy/.env.example`):

```
PG_SERVER_SKILLS_URL=https://<org>.github.io/practicegraph-skills/skills.json
PG_SERVER_SKILLS_TTL=900
```

Restart the server. It fetches on first request, caches for the TTL, and
re-serves at `/v1/catalog/skills`. To verify:

```
curl -s https://your-server/v1/catalog/skills | jq .skills_version
```

should show *your* version. If it shows the bundled version instead, the fetch
failed validation or was unreachable — check the URL is https, the JSON is
valid, and every entry obeys the field rules above.

## Notes & limits

- **The server is the only thing that fetches GitHub.** Endpoints never do
  (NFR-SEC-5). If you run no server (pure local install), endpoints use the
  bundled starter skills.
- **https only**, and the fetch is size-bounded (512 KB) and time-bounded
  (10 s). A huge or slow file is rejected, not truncated.
- **A bad publish is safe**: invalid content is rejected and the previous good
  catalog keeps serving. Nothing unsafe reaches a client.
- **Skills are never executed.** They are text a person chooses to copy into
  their agent. There is deliberately no mechanism to run server-provided code.
