# Frequently asked questions

**Is it really free?**
Yes. The app, the native shells and the optional aggregate server are Apache-2.0. There is no account, trial clock, license key or paid tier. Your Claude and OpenAI subscriptions are separate costs that PracticeGraph estimates but never bills.

**Does anything leave my machine?**
Not about your activity. The app downloads published content (rate cards, curated editions, release tags) and uploads nothing unless you switch on aggregate sharing or provider wording, both of which are off by default and explained where you enable them. [Privacy](PRIVACY.md) has the full boundary.

**Why does Usage show a dollar amount when I pay a flat subscription?**
The amount is what your recorded usage would cost at the listed API prices. It is a price comparison that makes days and models comparable, not your bill. It is marked as an estimate everywhere it appears.

**Why does the total say some turns have no listed price?**
A turn whose model has no price in the active rate card is counted but not priced, and the page says so. The total runs low rather than guessing. New rate cards arrive as published editions.

**Which tools are supported?**
Claude Code CLI and OpenAI Codex CLI on every platform, and Claude Desktop's embedded coding sessions on macOS. Override discovery with `PRACTICEGRAPH_CLAUDE_HOME`, `PRACTICEGRAPH_CODEX_HOME` and `PRACTICEGRAPH_CLAUDE_DESKTOP`. Other tools are welcome as contributed parsers; see the [contributing guide](../CONTRIBUTING.md).

**Does it read my prompts?**
Parsers inspect content only transiently and keep counters and presence flags. Prompt and response text is never written to the store, and automated tests prove that log content cannot reach any rendered output.

**Is Work rhythm a wellbeing or productivity score?**
No. It describes recorded behavior, such as waiting time, follow-up timing and late hours, compared with your own past. The product's [principles](../PRINCIPLES.md) rule out scores of the person, streaks, badges and diagnoses.

**Who picks the news?**
People who work in AI daily pick a few items, published as a signed, validated edition. The app shows the edition it downloaded and never generates news itself. The content terms are described in [Hosted content](HOSTED_CONTENT.md) and the format in [Content formats](CONTENT_FORMATS.md).

**Can I point the app at my own catalog host?**
Yes. Editions are plain or signed JSON with a published schema. See [Hosted content](HOSTED_CONTENT.md) and [Catalog encryption](CATALOG_ENCRYPTION.md).

**Where is my data, and what happens when I uninstall?**
In one directory per user, listed in [Install](INSTALL.md#where-data-lives). Uninstalling removes the application and leaves your data alone.

**How do updates work?**
The app checks this repository's latest release once a day, shows a notice in the app and one native toast per newer version outside quiet hours. It never downloads or installs on its own. Install the new version over the old one.

**Is there a macOS or Linux build?**
Both shells exist and build in CI as candidates. Signed public downloads are not published yet. [Install](INSTALL.md) explains how to build one and what remains.

**Can my team use it?**
Each person installs their own copy and reads their own logs. An optional self-hosted server accepts anonymous, closed-schema aggregates from people who opted in and shows cohorts of at least five. There is no person-level view. See [Server operations](SERVER_OPS.md).

**The window went blank. What happened?**
Since 0.2.16 a failure in the page shows a card with the error and a reload button instead of an empty window, and records the error locally for your report. See [Troubleshooting](TROUBLESHOOTING.md).
