# Privacy

PracticeGraph reads private material: the session logs your AI coding tools keep on your machine. This page states exactly what the app does with them, what leaves the machine, and how to check it yourself. The short version: the reading is computed locally, the personal edition has no upload path, and anything optional is off until you turn it on.

## What is read

| Source | Read | Kept |
| --- | --- | --- |
| Claude Code transcripts | Timestamps, token counts, model names, tool and capability names, interruption and error markers | Counters and presence flags per turn |
| Codex rollout logs | The same, plus recorded usage-limit snapshots | Counters, hashed allowance identifiers, reset times |
| Harness configuration files | Installed versions, declared MCP servers and plugins, skills and their stated purpose | Names and on/off state |
| Git (optional denominator) | Commit counts in the working folders the sessions record | Counts |

Prompt and response text is inspected only transiently, for example to detect an interruption marker, and never written to the store. Parsers are fail-open: an unreadable line is counted as skipped and reported, never guessed.

Automated tests scan every rendered surface (text report, HTML report, doctor output) for seeded fixture markers to prove that log content cannot leak into output.

## What leaves the machine

**Nothing about your activity, by default.** Outbound traffic in the personal edition is read-only:

- the rate card and model catalog,
- the curated editions (news, community, build ideas, guides, API prices),
- the latest release tag of each supported harness, and once a day the latest PracticeGraph release manifest,
- the European Central Bank's daily exchange rates, only if you choose a display currency other than US dollars. An install that keeps US dollars never contacts the ECB.

Each download is schema-validated and fails closed. The hosts see ordinary HTTP request metadata such as your IP address and the time of the request, and nothing else. Editions are cached locally, so the app works offline.

**Optional flows, each off until you enable it, each disclosed where you enable it:**

| Flow | What is sent | Where it is switched on |
| --- | --- | --- |
| Aggregate sharing | Closed-schema anonymous aggregates: counters, closed enums, estimates, versions. No free text, no paths, no timestamps finer than a day. Contributions count once per source and day toward a minimum cohort of five before anything is shown. | `practicegraph consent on`, or the Privacy Center. `practicegraph consent show` prints the exact boundary. |
| Provider wording | Composed sentences and figures of a reflection, sent to the wording provider you configure. | `practicegraph reflect` |

Practice history, learning choices, notes and self-reports are local records. They are excluded from aggregates and from every catalog request.

## Where your data lives

| Platform | Data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\PracticeGraph` |
| macOS | `~/.local/share/practicegraph` |
| Linux | `~/.local/share/practicegraph` |

The directory holds one SQLite store, the cached editions, the configuration file and the dashboard's endpoint file. Uninstalling removes the application and leaves this directory alone; delete it yourself if you want the record gone. `practicegraph hygiene` prunes aged state on request.

## The local dashboard

The dashboard is served on the loopback interface only, behind a per-session token that the native window receives at launch. Requests without the token, or with a foreign `Host` header, are refused. Do not publish the launch URL. The page loads nothing from the network: its content-security policy allows only its own origin.

## How to verify

- `practicegraph consent show` prints the sharing boundary in force, which is off unless you changed it.
- `practicegraph doctor` prints install health as closed JSON, including whether an organization token is present. The token itself is never echoed by any command.
- The source is here. Parsers live in `src/practicegraph/sources/`, the wire schema and its validator in `src/practicegraph/wire.py`, and the leak scanners in `src/practicegraph/privacy.py`.
- Watch the network yourself: with sharing off, the only hosts contacted are the catalog host (practicegraph.dev), the release host (github.com, for PracticeGraph's own update manifest) and api.github.com (for each supported harness's latest release tag). If you chose a display currency other than US dollars, www.ecb.europa.eu is added, for the exchange rates.

## Observations, not assessments

Work-rhythm observations describe recorded behavior such as waiting time, follow-up timing and late hours. They are not health assessments, productivity scores or proof of capability, and the product's [principles](../PRINCIPLES.md) forbid presenting them that way. A figure the evidence cannot support is withheld with its reason, never shown as zero.
