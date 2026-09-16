# Reading and navigation

The desktop UI opens on **Usage**. The menu is a flat row of twelve pages;
every page is visible and opens in one click. In narrow windows the links wrap.

| Menu order | Pages |
| --- | --- |
| 1–6 | Usage, Models, API Prices, Tools, Agent skills, Integrations |
| 7 | Work rhythm (Practice is off the menu as of 2026-09-16; `#practice` still resolves) |
| 9–12 | News, Community, Build ideas, Guides |

## Page content (September 15, 2026)

- **Usage** — totals, trends, model breakdown and recorded limits. Session investigation and family inspection are removed.
- **Models** — tool defaults and reasoning settings only.
- **API Prices** — the separate API market: one row per model with its first-party or lowest direct host price, hosts expandable per model, and a flag wherever a direct host undercuts the developer's own price.
- **Tools** — tool versions and feature names, descriptions and counts, expanded by default. Codex recognizes modern tool records; orchestration scripts are distinct from shell calls.
- **Agent skills** — installed skills only, with one complete description each. No Suggested tab or duplicate Details text.
- **Integrations** — configured integrations and configuration states.
- **Practice** — off the menu since 2026-09-16 pending a rethink; the page and its records remain and `#practice` still opens it.
- **Work rhythm** — expanded last-prompt history, activity patterns (including quiet hours), and work mix. Interaction counters, failures/retries and reflection prompts are not mounted.
- **News** — the main news edition without Community or the Let's build promotion.
- **Community** — a separate practitioner edition.
- **Build ideas** — existing ideas and repositories.
- **Guides** — direct official training and certification links grouped by tool, with separate coding/productivity shelves.

Focus and breaks remain expanded on every page. Course-completion credentials are labeled separately from professional certifications.

Practice brief notes use browser local storage, scoped to the current practice.
Saving is explicit. A finished personal brief can be archived locally before
starting another. Notes do not award hours, finish a course or coach practice,
or become part of the engine's backup/export. Read/write failures are visible;
invalid stored notes are not overwritten. This feature adds no network calls.

Each page opens with a plain intro. Section headings use short names such as
“Tool versions”, “Installed skills”, “Configured integrations” and “Official
guides”. Repeated provenance labels stay out of heading rows. Details and
methodology can be folded; facts needed to interpret a price stay visible.

## Navigation behavior

- Usage remains the default. Current-page underlines, keyboard focus,
  back/forward and hashes work as before. Renaming does not break addresses.
- Live addresses: `#spend`, `#models`, `#tools`, `#skills`, `#connectors`,
  `#practice`, `#baseline` (no menu entry), `#mindfulness`, `#news`, `#community`, `#api-prices`,
  `#build`, `#docs`. `#usage` aliases `#spend`; `#toolkit` aliases `#models`.
- Retired addresses: `#history`, `#learning`, `#advice`, `#setup` → Practice;
  `#reading`, `#updates` → News; `#prices` → API Prices; `#projects` → Usage.
- The AI working checklist is reached from Practice through `#baseline`.
  Projects and Improve setup remain absent from the menu.
- News and Community have independent update indicators; new practice moves mark Practice.
- Practice-time and learning controllers stay mounted across pages. A running
  or paused timer has a return link on every page except Practice.
  Navigation never confirms time or completes learning.
- Working-hours confirmation remains accessible in the header.
- Empty or unknown hashes open Usage. The native `#break` intent is consumed
  once before later navigation.

UI tests and a browser walkthrough cover these behaviors. Editorial
publication and signed catalog delivery are unchanged.
