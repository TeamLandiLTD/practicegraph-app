# Changelog

Every build is its own version; a version number is never rebuilt or reused. Windows installers, their SBOMs and the signed update manifests are attached to the [Releases](https://github.com/TeamLandiLTD/practicegraph-app/releases) page, which is the complete ledger.

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
