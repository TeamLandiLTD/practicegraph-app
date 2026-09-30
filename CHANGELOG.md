# Changelog

Every build is its own version; a version number is never rebuilt or reused. Windows installers, their SBOMs and the signed update manifests are attached to the [Releases](https://github.com/TeamLandiLTD/practicegraph-app/releases) page, which is the complete ledger.

## 0.2.29 — 2026-09-30

- Update notices are now verifiable: the client carries the maintainer's release-signing public key, so a signed `update.json` on a later release will show one in-app notice and one native toast per newer version. Nothing is downloaded or installed automatically; earlier versions carried no key and show no notices.
- A served rate card published as the second edition of its day (`rates-2026-09-29-2`) is now recognised as the product's own dated card. It was read as an organization's custom card, so it could not be aged and no later built-in card could ever outrank it, which would have reopened the stale-cache problem fixed in 0.2.22. Editions now rank by date, then by their same-day number.
- Windows: the first installer carrying 0.2.22 to 0.2.27 (display currency, downloads verified through the operating system's trust store, the faster dashboard, the rate-card fixes). It is signed with a publicly trusted certificate, so the installer names TeamLandi OOD instead of "Unknown publisher".
- Codex "Luna Reserve" turns, logged as `gpt-reserve`, are priced at GPT-5.6 Luna's rate; they were left out of cost estimates.
- Claude Opus 4.8 fast mode is priced at 2x its standard rate, like Opus 5.5.
- The served rate card is checked at most every 15 minutes instead of once a day, so a published price correction reaches installs the same day; the dashboard's refresh also picks up the model catalog and rate card.
- Windows-internal builds 0.2.22 to 0.2.28 were never published; their numbers are superseded by this release.

## 0.2.27 — 2026-09-29

- The app no longer re-reads your whole history twice after every launch. Costs are priced as logs are read, so a change of rate card re-reads all history; the dashboard's background refresh could start before the downloaded rate card was loaded, read history under the built-in card, and then re-read everything again under the downloaded one on the next refresh. On a long history that kept the Mac busy for minutes after each start. The card is now loaded before any history is read. The first start of 0.2.27 may still re-read once, to settle on the downloaded card; later starts do not.
- The dashboard no longer slows to a stall while it refreshes. Every request that found the view out of date rebuilt it, and while a refresh runs the page asks every five seconds, so on a long history the rebuilds overlapped, slowed each other past that interval and piled up at full CPU; the view could stop answering for more than a minute. Now one rebuild runs at a time and requests that arrive meanwhile share it, and the page never sends a new request while one is still waiting.
- Switching currency is instant: the chosen currency is applied to the view already built instead of rebuilding it.

## 0.2.26 — 2026-09-29

- Amounts can be shown in your own currency: a currency menu in the header offers the euro and every currency the European Central Bank publishes a daily rate for, 30 in all with the dollar. Usage totals, charts, the advisor's and spending sentences, and API Prices are converted at the ECB's daily reference rate, and the page says which rate and date it used. Figures are still computed and stored in US dollars; the currency changes only what is shown, and the CLI and file reports stay in dollars. The rates are downloaded only once you pick a currency other than US dollars, from www.ecb.europa.eu, a few times a day at most; until they arrive, or if they cannot be downloaded, the page stays in dollars and says so. Quoted news, community findings, build ideas and guides are shown as written. Managed installs (with an organization server configured) do not download them.
- The "From the community" line on Models, Work rhythm and Practice is gone. Community findings stay on the Community page.
- Claude Sonnet 5.5 is on the rate card at Sonnet 5's price ($2 / $10 per million input / output tokens, cache reads $0.20). Without it, its turns would be left out of cost estimates.
- OpenAI prices on the built-in rate card follow the 2026-09-29 changes: GPT-5.6 Sol $4 / $20 (promotional until at least 21 November), Terra $2 / $12 and Luna $0.20 / $1.20, down from $5 / $30, $2.50 / $15 and $1 / $6; GPT-6 Astra, Sol and Luna are added. Installs that can reach practicegraph.dev already had these from the published card.

## 0.2.25 — 2026-09-28

- Mac: an installer wizard (.pkg) alongside the disk image: Welcome, Licence, Install and Done, like the Windows installer. Upgrading closes a running PracticeGraph first (only processes running from the app in Applications), keeps your history and settings, and opens the new version at the end. "Background refresh", on by default under Customize, sets up the refresh that keeps model guidance and prices current while the app is closed, which previously needed a manual step. Apple silicon and macOS 13 or later.

## 0.2.24 — 2026-09-28

- No behaviour changes: source comments reworded during the open-source review. Built to exercise the upgrade path from 0.2.23.

## 0.2.23 — 2026-09-24

- Usage → How model use is billed: the dropdowns no longer stop responding after a change. One change disabled both dropdowns until the whole Usage view had been rebuilt, which takes seconds on a long history and much longer while a new install is still reading it, and a disabled dropdown looks unchanged. The choice now shows at once, the dropdowns stay usable, and saves run in order so a quick second change cannot undo the first.
- The daily text report no longer prints a consecutive-day streak. PRINCIPLES.md rules out streaks; active days remain as a neutral fact.
- Removed the retired paid-plan code: the licence-token parser, trial and seat logic, the licence-signer setting (`PRACTICEGRAPH_LICENSE_URL`, now ignored if present in a config file) and the build step that checked the compiled engine for leftover source text.
- The published source now builds on every platform: the Windows shell's input-watch module was missing from the public file list, and the list's completeness check now covers the native shells, the dashboard source and packaging, not only the engine.
- GitHub's source archive now contains every published file; `.gitattributes` export rules had dropped ten documents and the README screenshots.

## 0.2.22 — 2026-09-23

- Downloads now work on managed networks that inspect HTTPS (Zscaler, Netskope and similar). The engine verified certificates against a bundled CA file that never contains the organization's inspection root, so every edition download failed with "self-signed certificate in certificate chain" while the browser on the same machine worked. Certificates are now verified by the operating system's own trust store, the same one the browser uses. Verification is still mandatory: an untrusted certificate is refused as before, and public editions stay signature-checked.
- Mac: the Models page (guidance and benchmarks), advisor, docs, skills and the served rate card now refresh while the app is running. Those editions come from the background agent tick, which on a Mac ran only if a LaunchAgent had been installed by hand, so an install from the disk image never downloaded them. The app now runs the tick itself every fifteen minutes when one is due, and skips it when the LaunchAgent already ran.
- Claude Opus 5.5 is on the rate card ($4 / $20 per million input / output tokens, cache reads $0.20, fast mode 2x). Its turns were unpriced and left out of cost estimates.
- A rate card downloaded from practicegraph.dev no longer overrides a newer card built into the app. Its `rates-<date>` version was not recognised as the product's own dated card, so it was treated like an organization's custom card, which always wins, and an older download could undo a shipped price fix.

## 0.2.20 — 2026-09-17

- The installer now stops PracticeGraph's own running processes before it replaces files. Windows could not close the windowless background engine, so an upgrade left the old engine and the old shell on disk until a reboot, and a window could be served by an engine weeks older than the installed version. Only processes running from the install folder are ended; no other Python on the machine is touched.
- When the engine behind the window is a different version from the window itself, the page says so and says what to do, instead of failing page by page.
- Build ideas: "Worth a look on GitHub" is its own section of tiles, with repository names in the same type as the project titles.

## 0.2.19 — 2026-09-17

- An upgrade now takes effect even when an older engine is still running. A process that survives the install (started from another folder, so the installer cannot stop it) kept answering, and every later start reused it: the new version was installed and never ran. The engine now reuses a running instance only at its own version; otherwise it starts, publishes its endpoint over the old one, and the old instance stands down. The shell asks the engine to start whenever the running one reports another version. Nothing is deleted and your data is untouched.

## 0.2.18 — 2026-09-17

- First run: the page no longer looks empty while a long history is read. The engine reports a running refresh, the page asks again every few seconds while it runs, and a notice says the numbers are still filling in. Previously the first view came back empty and the page did not ask again for five minutes.
- Your own logs are read before any public download, and each finished step repaints, so numbers arrive even when a managed network stalls the downloads.
- The Tools feature scan no longer runs inside a page request, and an unchanged session log is never read twice. On a large history this cut each rebuilt view from several seconds to well under one.

## 0.2.17 — 2026-09-16

- Editions are pulled from practicegraph.dev, the product's own domain, instead of the preview host. Every channel derives from one content base URL, so they switched together; verified with a fresh profile across all fourteen public channels.

## 0.2.16 — 2026-09-16

- A rendering failure now shows a card with the error, its stack and a reload button instead of an empty window, and records the error locally under `pg-last-error` for the report.
- Browser storage failures can no longer take the page down: the per-render page markers and the theme choice tolerate a refused read or write.

## 0.2.15 — 2026-09-16

- Practice is off the menu while it is rethought; `#practice` still opens it and its records are kept.
- The in-app feedback form is gone; the footer links to the issue tracker instead.
- Footer branding and a header link to this repository.

## 0.2.14 — 2026-09-16

- API Prices: a Lowest column per model and a deals panel computed from the edition: hosts undercutting the developer, half-price batch and off-peak windows, and prices with an announced end date.
- Batch references are band-aware and deal groups are capped, so a lower-band price is never compared with a higher-band one.

## 0.2.13 — 2026-09-16

- The cheaper-host flag compares against the developer's lowest own rate across tariff windows, so a host below a peak tariff but above off-peak is no longer called cheaper.

## 0.2.12 — 2026-09-16

- The price feed carries the full provider matrix for the models people choose between now: one row per model, every priced host behind an expander, and a computed flag wherever a direct host undercuts the developer on both input and output.
- The feed size limit rose to 4 MiB; clients before 0.2.12 keep their last accepted edition.
- The workload comparison tab was retired.

## 0.2.11 — 2026-09-15

- Update check: the app reads this repository's latest release manifest once a day, shows a notice in the app and one native toast per newer version outside quiet hours. It never downloads or installs on its own.

## 0.2.10 — 2026-09-15

- Visual revamp: typography, the Usage hero with a day-by-day chart against the previous period, a one-line install notice, a folded focus strip, navigation pills with icons, provider monograms and a density toggle.
- API Prices became model-first: the developer's price as the headline with hosts expandable per model.
- Text fields use their full width.

## 0.2.8 — 2026-09-15

- Simplified navigation: Usage opens first and every page sits in one row.
- API prices are sourced editions with provenance behind every figure.

## Earlier

Versions 0.1.28 to 0.1.31 (August 2026) were pilot builds that introduced the harness inventory pages, the guided break system, the Let's build tab, one-click model defaults and dated shelves for news and build ideas. They are no longer published; 0.2.16 is the first release with its source.
