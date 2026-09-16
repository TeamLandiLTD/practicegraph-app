# Product Requirements Specification

**Purpose.** A complete, implementation-agnostic specification of the functional and
non-functional requirements of the PracticeGraph application, sufficient for an independent
team to build the product from scratch. It describes WHAT the system must do and the
qualities it must have — not the current codebase. Requirement keywords MUST / SHOULD /
MAY follow RFC 2119. Every requirement is testable; where a concrete threshold appears
it is a named, configurable constant unless marked invariant.

**Product in one sentence.** A privacy-preserving AI-usage maturity platform: a local
endpoint agent analyzes each person's AI-tool activity entirely on their machine and
offers cost, efficiency, and sustainable work-pattern observations without claiming
validated wellbeing measurement; with explicit consent it
emits only closed anonymous aggregates to a passive backend that powers a k-anonymous
fleet dashboard for the organization.

**Release model.** The application is free and Apache-2.0 licensed. Official
editorial catalogs have separate content terms and private authoring. Public
source publication uses a reviewed export without private Git history.

---

## 0. Foundational invariants (non-negotiable, override all other requirements)

- **INV-1 Endpoint is the brain.** All raw data — logs, prompts, responses, file paths,
  identity, per-session detail — and all classification, pricing, analysis, report
  rendering, and recommendation selection MUST occur on the endpoint.
- **INV-2 Server is passive.** The backend MUST only: serve versioned catalog artifacts,
  accept schema-validated anonymous aggregates, store them, and serve k-anonymous
  aggregate queries. It MUST NOT receive, request, or derive raw content or identity.
- **INV-3 Closed wire schema.** Everything that crosses the network MUST conform to a
  closed schema: enumerated fields only, counters / closed enums / estimates / version
  identifiers / coarse cohorts. Free-text fields are forbidden. Unknown fields MUST be
  rejected, not ignored.
- **INV-4 No person-level organizational surface.** No API, dashboard, export, or log
  MAY enable ranking, comparing, or identifying individuals. Aggregates below the
  k-anonymity threshold MUST be suppressed, and suppression MUST be visibly honest
  (shown as unavailable, never as zero).
- **INV-5 Advisory only, out of the request path.** The system MUST NOT intercept,
  modify, block, or gate any AI request or tool. All influence on behavior is advice.
- **INV-6 Deterministic core.** Given identical inputs and configuration, every
  analysis, report, and recommendation MUST be byte-reproducible. No runtime LLM/ML
  inference in the default path (an optional, off-by-default semantic tier MAY exist
  but nothing may depend on it).

---

## 1. Scope and actors

| Actor | Description |
|---|---|
| Developer (end user) | Uses AI CLI/IDE tools; owns the endpoint data; receives local reports, tips, nudges. |
| Fleet administrator | Deploys agents org-wide; configures org connection; views the k-anon dashboard. |
| Support engineer | Triages unhealthy endpoint installs with a diagnostic command. |
| Works council / DPO | Audits the privacy posture; approves the emission boundary. |
| Backend operator | Runs the passive API + store. |

In scope: Windows-first endpoint (cross-platform core), one passive backend, one fleet
dashboard, enterprise deployment. Out of scope: see §10.

---

## 2. Functional requirements — Endpoint agent

### 2.1 Source acquisition and parsing (FR-SRC)

- **FR-SRC-1** The agent MUST discover and read the local activity logs of supported AI
  tools without any change to those tools. Initial supported sources (MUST): OpenAI
  Codex CLI session logs; Claude Code CLI session logs. Additional sources (SHOULD, as
  capability-classified adapters): Claude Desktop diagnostics, ChatGPT export archives,
  OpenTelemetry streams.
- **FR-SRC-2** Source discovery MUST cover platform-conventional locations (env-var
  override → tool-specific env (e.g. CODEX_HOME) → per-user default paths) and MUST be
  cross-platform correct (Windows profile paths and XDG paths).
- **FR-SRC-3** Each source MUST have a declared capability class from a closed set
  (deep_session_log, telemetry_stream, admin_compliance_feed, quota_billing_feed,
  export_importer, local_app_cache_probe) driving what analysis it can feed.
- **FR-SRC-4** Parsers MUST normalize records into a common local event model
  (“TurnEvent”) carrying at minimum: timezone-aware timestamp, local session id, source
  id, tool, model, token counts (input/output/cached/reasoning/cache-creation), tool-call
  counters, retry/interruption counters, and boolean presence flags for
  content/path/identity (the values themselves MUST NOT be stored in the event).
- **FR-SRC-5 Fail-open tolerance.** Unknown record types, unknown fields, unknown
  tool-call kinds, and malformed lines MUST be skipped without aborting the parse, and
  each skip class MUST be counted.
- **FR-SRC-6 Drift visibility.** Per-source parse-health counters (records seen /
  parsed / skipped, malformed count, unknown-field count, unsupported-record count)
  MUST be persisted per source lane and surfaced (a) in the daily report, (b) in the
  diagnostic command, and (c) as an advisory alert when drift is detected. Counts only —
  the literal unknown token/name MUST NOT be recorded.
- **FR-SRC-7 Incremental reading.** The agent MUST maintain per-file cursors (identity
  hash, offset, size, mtime) so re-scans are incremental; log rotation and truncation
  MUST be detected and trigger a safe re-read. A parser version bump MUST invalidate
  cursors (forced clean rescan).
- **FR-SRC-8** Parsing MUST be validated against a versioned, sanitized fixture pack
  with expected outputs per scenario, including drift/unknown-shape fixtures.

### 2.2 Analysis (FR-ANL)

- **FR-ANL-1 Cost estimation.** The agent MUST estimate spend per turn/session/day from
  token counts and a versioned local rate card; every estimate MUST carry the rate-card
  version and an is_estimate marker. Cached-token pricing MUST be separated from input
  pricing.
- **FR-ANL-2 Efficiency signals.** The agent MUST compute deterministic efficiency
  findings from closed detectors (e.g. context bloat, debug loops, retry storms) with
  per-detector ids and severities from closed vocabularies.
- **FR-ANL-3 Work-type classification.** The agent MUST classify activity into a closed
  work-type taxonomy deterministically (rules over event structure, never content
  round-trips to a model in the default tier).
- **FR-ANL-4 Model-fit advice.** The agent MUST compare observed usage against a
  versioned model policy catalog and produce advisory model-fit opportunities.
- **FR-ANL-5 Maturity signals.** The agent MUST derive closed AI-maturity signals
  (adoption/skill indicators) as counters/statuses per closed taxonomy.
- **FR-ANL-6 Spend pace.** Given a period budget/bound, the agent MUST compute pace and
  a projected total, endpoint-side only.
- **FR-ANL-7 Trends.** The agent MUST maintain a local daily aggregate history
  sufficient for a weekly trend view; history is subject to local retention (FR-CFG-4).

### 2.3 Focus & cognitive-load coaching (FR-FOC)

- **FR-FOC-1 Daily metrics.** From the same local events, the agent MUST compute per
  local processing day: longest single-session focus block (minutes; sessions split at
  a >30-min internal gap); max concurrent active sessions (15-min activity window);
  session switch count (alternations within 15 min); longest cross-session no-break
  streak (chains broken by ≥15-min gaps); first-interaction hour; count of interaction
  burst windows (≥6 events / 10 min). All thresholds are named constants.
- **FR-FOC-2 Positive-first presentation.** Wherever focus data renders, the positive
  metric (longest focus block) MUST lead; at most TWO observation tips MAY follow.
- **FR-FOC-3 Closed tip catalog.** Coaching tips MUST come from a closed catalog
  (initially five: protect-a-deep-work-block, break-after-long-streak,
  batch-parallel-sessions-to-one-task, pause-between-bursts, celebrate-longest-block)
  with fixed ids, trigger predicates, and fixed copy. Tips MUST be individually
  dismissible and dismissal MUST persist.
- **FR-FOC-4 On-time nudge.** The agent MUST raise at most one intraday notification
  per day when continuous engagement (no ≥15-min gap) reaches 120 minutes, suppressed
  during quiet hours. Streak evaluation state MUST accumulate across scheduler cycles
  (evaluation MUST NOT window itself to a single cycle) and MUST NOT re-fire for the
  same streak.
- **FR-FOC-5 Single switch.** One user-controllable toggle MUST govern the entire focus
  coaching surface: off = no nudges AND report shows only the positive metric.
- **FR-FOC-6 Single source of truth for visibility.** Tips shown in any section MUST be
  drawn from one dismissal-filtered visible list; a dismissed tip MUST disappear from
  every surface; a tip MUST NOT render in two sections simultaneously.
- **FR-FOC-7 Local only (invariant).** Focus metrics, tips, and interactions MUST NOT
  cross the network in any form (see NFR-PRV-6). Engagement MAY be counted only through
  the existing closed engagement counters.
- **FR-FOC-8 Language constraint (testable).** All focus-facing copy MUST use
  behavioral, evidence-aligned language and MUST NOT contain clinical or neurological
  claim vocabulary. The forbidden lexicon (case-insensitive minimum: dopamine, adhd,
  brain, addict, neuro, diagnos, disorder, clinical) MUST be enforced by an automated
  test over the catalog and rendered output.
- **FR-FOC-9 (v2) Focus timer.** The desktop shell SHOULD offer a one-click focus block
  timer (default 90 min) with a break timer (default 10 min), completion notifications
  with an actionable “start break” affordance, ephemeral in-session state, and local
  blocks-started/completed counters visible in the daily report.
- **FR-FOC-10 Qualified work-pattern observation.** The default Today surface MUST lead
  with at most one deterministic, evidence-gated behavioral observation. It MUST expose
  a closed confidence (`insufficient`, `early pattern`, `repeated pattern`), the period,
  supporting evidence in plain language, and the caveat that it is a work-pattern
  observation rather than a health assessment. It MUST NOT claim causation.
- **FR-FOC-11 Legacy daily feel.** A 1-5 daily-feel note MAY remain as local historical
  input, but MUST NOT be compared with activity volume, treated as objective ground
  truth, used to rank the person, or emitted across the wire.

### 2.4 Reporting (FR-RPT)

- **FR-RPT-1** The agent MUST render a daily report fully offline in three forms: plain
  text (CLI), a self-contained single-file HTML document, and a multi-view HTML shell
  (views: Today, Insights, Sources, Skills, Privacy Center).
- **FR-RPT-2 Offline hard rules (invariant).** Report HTML MUST contain no script, no
  external references (no http(s), no src attributes, no webfonts), only in-page anchor
  hrefs; all dynamic values MUST be escaped; charts MUST be inline vector markup.
- **FR-RPT-3 Determinism (invariant).** Given a fixed snapshot (including a supplied
  generation timestamp), each renderer’s output MUST be byte-identical across runs, and
  this MUST be enforced by golden-file tests with an intentional, audited regeneration
  flow.
- **FR-RPT-4 Content.** The report MUST include: spend hero with estimate framing;
  work-type mix; efficiency findings; advisory suggestions (readable cards with ids);
  Focus section (FR-FOC); source health incl. drift lines and plain-language pause
  reasons; weekly trend; optional org-benchmark context (only from k-anon aggregates);
  maturity summary; a quiet status footer. Debug telemetry (provider status walls, raw
  signal id lists) MUST live in the text/CLI report, not the default HTML views.
- **FR-RPT-5 Privacy Center view.** The shell MUST show consent state, emit queue
  status, an exact preview of the next emit payload (or a clearly labeled synthetic
  example), and the CLI commands to change state. Static report surfaces MUST NOT
  mutate state (no forms/buttons that write).
- **FR-RPT-6 Report open flow.** A single command/menu action MUST render and open the
  report in the default browser, functioning identically in the packaged binary
  (no reliance on components the packaging may omit); failure to launch a browser MUST
  degrade to printing the file path.
- **FR-RPT-7 Engagement counters.** Rendering and interactions MUST increment only the
  closed engagement counter set (report_generated, tip_shown, tip_acted,
  recommendation_dismissed, recommendation_never_suggest, engagement_unavailable), with
  unmeasurable counters recorded as unavailable rather than zero.
- **FR-RPT-8 Suggestion lifecycle.** Users MUST be able to dismiss (and never-suggest)
  any advisory suggestion from the CLI; state persists locally and filters all surfaces.
- **FR-RPT-9 Observation-first hierarchy.** Interactive and static Today surfaces MUST
  put the qualified observation before usage/cost diagnostics. Numeric 0-100 heuristic
  dimensions MUST be absent from the default wellbeing-facing surface and, when shown,
  live under a clearly labeled experimental secondary detail section.

### 2.5 Alerts & scheduling (FR-ALR)

- **FR-ALR-1** A scheduler MUST run the daily-report generation and alert evaluation on
  a fixed poll interval (default 900 s) under the endpoint service, isolated so one
  failing evaluation never breaks the cycle (fail-open per category).
- **FR-ALR-2** Alert categories and reason codes MUST be closed vocabularies with
  per-category user toggles, per-category frequency caps (e.g. 1/day), a global quiet
  hours window, and persisted fired-state for dedupe. Category/vocabulary changes MUST
  be pinned by tests.
- **FR-ALR-3** Minimum categories: daily-report-ready, spend pace/budget, source health
  incl. parser drift, focus tip (FR-FOC-4).
- **FR-ALR-4 Delivery.** Personal-observation notification text MUST be closed
  (display names + integers; never private content). Curated public news may show
  validated headline/hook text (owner request, September 11, 2026), with editorial
  importance, a reason, and a bounded expiry. News delivery respects quiet hours,
  local mute/snooze choices, persistent story-level deduplication, and at most
  three deliveries in a rolling 24 hours, at least one hour apart. Important news
  uses an OS toast; urgent news may additionally open a compact native window
  without taking keyboard focus. Ordinary stories remain in the unread shelf.
  Desktop toast delivery MUST support at minimum body+click activation;
  actionable buttons SHOULD be supported by the desktop shell platform (see §9).

### 2.6 Diagnostics (FR-DIA)

- **FR-DIA-1** A single `doctor` command MUST print one closed JSON document with per-
  check status from {healthy, unhealthy, not_applicable, skipped} covering: connection
  config resolution (per-field source: env/file/default; token masked, presence flag
  only), data dir + state DB existence/openability (MUST NOT create the DB), per-source
  parser health incl. drift counters, emit queue counts by status + error codes, consent
  state (disabled is healthy), platform service status (not_applicable off-platform),
  versions (app, rate card, schema, catalog artifacts), and an OPT-IN network probe
  (closed error codes, no store writes).
- **FR-DIA-2** Exit codes: 0 all healthy; 1 any unhealthy; 2 command error. Only
  `unhealthy` may drive exit 1.
- **FR-DIA-3** Doctor output MUST pass the same no-leak constraints as every surface
  (no tokens, no full local paths, no content).

### 2.7 Consent & emission (FR-CNS / FR-EMT)

- **FR-CNS-1** Emission MUST be opt-in, default OFF, stored locally with decision
  timestamp, changeable at any time via CLI and desktop shell; first-run and the report
  MUST state plainly that data stays local while off.
- **FR-CNS-2** A consent “show” command MUST display the exact boundary: what would be
  sent, where, and under which org identity.
- **FR-EMT-1** With consent ON, the agent MUST build period aggregates conforming to
  the closed emit schema (schema_version on the wire; counters, closed enums, estimates
  + rate-card version, coarse cohort, contributor/session counts, engagement counters,
  agent-health counters incl. drift counts; content_present=false and
  identity_present=false are validated invariants).
- **FR-EMT-2 Offline queue.** Emits MUST queue locally with statuses
  {pending, retry_wait, sent, dead_letter}; transport failures classify into closed
  error codes (server_unavailable, timeout, http_5xx, http_4xx, invalid_payload,
  transport_error, schema_version_not_supported); retry with backoff; version-rejection
  is terminal (dead_letter, never retried).
- **FR-EMT-3 Version negotiation.** The server MUST validate schema_version against its
  supported set BEFORE full validation and reject unsupported versions with a distinct
  closed error code + generic upgrade hint that MUST NOT echo the submitted value; the
  agent MUST surface that code distinctly in queue status and doctor. Operational rule:
  server support for version N deploys before any N-emitting agent.
- **FR-EMT-4 Catalog pull.** The agent MUST pull versioned catalog artifacts (rate
  cards, model policies, taxonomies, source capabilities, benchmarks) from the backend
  with a single catalog-wide coordination version; pull failures are closed-coded and
  never block local analysis (bundled defaults MUST exist).

### 2.8 Configuration & local data (FR-CFG)

- **FR-CFG-1** Connection settings (api_base_url, org_id, org_token, data_dir) MUST
  resolve env-var > config-file > built-in default; the config file lives INSIDE the
  private per-user data directory next to the local store so the agent and shell read
  the same file.
- **FR-CFG-2** The org token is a credential: writable via config/env/installer
  property, never echoed by any command or UI (masked display + presence flag only),
  excluded from all logs.
- **FR-CFG-3** All local state MUST live in one local store in the data dir (single
  writer discipline; concurrent readers safe). Schema MUST carry a version with
  additive, idempotent, monotonic migrations (a rollback re-run must not downgrade).
- **FR-CFG-4 Retention/hygiene.** A hygiene command MUST prune local state by
  configurable age/row caps (defaults: 90-day age cutoff; caps for sent emits, fired
  alerts, inference invocations, audit rows); un-sent emits MUST never be pruned.
- **FR-CFG-5** Every persisted or emitted timestamp MUST be timezone-aware. Raw events,
  financial totals, and cross-machine accounting days remain UTC; reports MUST label UTC
  accounting days distinctly from local calendar days.
- **FR-CFG-6 Personal schedule.** The agent MUST suggest an IANA timezone but treat it
  as unconfirmed until explicitly saved. A versioned schedule record MUST include IANA
  timezone, working days, preferred start/end, quiet start/end, and closed weekend mode.
  Local-day boundaries and schedule-relative observations MUST use the schedule version
  active for that date and handle DST transitions deterministically. Preferred-hours,
  quiet-hours, and off-schedule percentages MUST be withheld while unconfirmed.

---

## 3. Functional requirements — Backend (FR-API)

- **FR-API-1** Versioned REST surface (path-versioned, e.g. /v1/...) with exactly:
  catalog artifact serving, aggregate ingest, dashboard aggregate queries, source-
  coverage metadata. Nothing else.
- **FR-API-2** Ingest MUST authenticate org id + bearer token, validate the closed
  schema exactly (reject unknown fields, forbidden keys, presence-flag violations),
  enforce org-id match between auth and payload, and apply FR-EMT-3 version gating.
- **FR-API-3** Error responses MUST carry only {closed code, generic message, request
  id} plus a stable error-code header; logs MUST record method, route template, status,
  request id, error code — never bodies, tokens, or exception text.
- **FR-API-4** Storage MUST be pluggable (production relational DB; in-memory for
  test) with sequential, append-only-biased migrations.
- **FR-API-5** Aggregate query endpoints MUST enforce the k-anonymity threshold
  server-side; below-threshold cells return an explicit suppressed marker.
- **FR-API-6** The backend MUST be containerized, configured via environment only,
  loopback-bindable for pilots, with access logging disabled by default.

---

## 4. Functional requirements — Fleet dashboard (FR-DSH)

- **FR-DSH-1** The dashboard MUST visualize only k-anon aggregates: org spend + trend,
  work-type mix, maturity distribution, adoption funnel, source coverage, agent-health
  (incl. drift) rollups, error bands, and estimate-bias framing.
- **FR-DSH-2** Suppressed cells MUST render as “unavailable” — never zero, never
  interpolated (testable requirement).
- **FR-DSH-3** No drill-down below the aggregate; no person, machine, or session
  identifiers anywhere in payloads or UI.
- **FR-DSH-4** States: loading, error, populated, suppressed — each with a designed
  rendering, covered by structural render tests.
- **FR-DSH-5** Static build servable by any web server; no runtime coupling to the
  backend beyond the versioned API.

---

## 5. Functional requirements — Deployment & operations (FR-DEP)

- **FR-DEP-1** Windows endpoint packaging MUST be a per-user installer requiring
  no administrator privileges. A personal tray process schedules collection.
  It MUST NOT install a LocalSystem collector or read other users' profiles.
- **FR-DEP-2** Optional fleet provisioning MUST give each independent contributor
  a distinct protected ingest credential. Credentials MUST NOT appear in installer
  logs or public files. The interactive installer MUST NOT collect a secret.
- **FR-DEP-3 Upgrade/uninstall semantics.** Upgrades MUST preserve personal data
  by default. Shared historical personal databases MUST NOT be adopted automatically.
  Distributed upgrades require a new version and verified installer provenance.
- **FR-DEP-4** Background scheduling and shell launch MUST run in the user's context.
  Quiet installation MUST NOT trigger an interactive launch action.
- **FR-DEP-5 Signing.** Build pipeline MUST support Authenticode signing of binaries
  and installer, activated by configured secrets; unsigned builds MUST be loudly
  labeled.
- **FR-DEP-6 CI.** Every change MUST pass: full test suite on the target OS, type
  gates, lint ratchet (exact-count ledger that can only go down), golden-file UI gate,
  packaging smoke (build + run the real binary), installer compile + validate, and
  privacy/no-leak suites. Advisory vs blocking lanes MUST be explicit.
- **FR-DEP-7** Backend MUST ship with container build + compose reference including
  migration bootstrapping.

---

## 6. Non-functional requirements — Privacy (NFR-PRV) *(the product’s core)*

- **NFR-PRV-1** No raw content, prompt, response, file path, hostname, username, or
  free text may be persisted outside parser-internal transients, appear in any report
  beyond the user’s own machine, or cross the network. Enforced by automated forbidden-
  key and content-pattern scanners over every wire payload, report surface, doctor
  output, alert text, and pinned golden.
- **NFR-PRV-2** All cross-machine vocabularies are closed sets pinned by tests; adding
  a wire field is a governed contract change (schema + validators + docs + fixtures
  move together; breaking change ⇒ version bump).
- **NFR-PRV-3** K-anonymity threshold (configurable, default ≥5) enforced server-side
  on every aggregate view. Count independently provisioned authenticated sources,
  at most once per source/day; never count client-supplied emit IDs as people.
  Historical records without source attribution MUST remain excluded. Shared-token
  fleets count as one source. Administration credentials MUST be separate from
  ingest credentials on every aggregate read route.
- **NFR-PRV-4** No individual ranking: comparative language and per-person metrics are
  forbidden in every surface (tested via a ranking-term scanner).
- **NFR-PRV-5** Consent is granular-at-the-boundary: one clear switch for emission;
  sensitive sub-systems (work profiling, focus) carry their own capture/visibility
  gates.
- **NFR-PRV-6** Wellbeing/focus data never leaves the machine (v1 invariant): enforced
  by (a) module-level declarations, (b) contract-boundary rejection of any snapshot
  containing focus fields, (c) tests asserting field-name absence from every prepared
  payload. Fleet-level wellbeing aggregation, if ever proposed, requires an explicit
  governance review and new consent.
- **NFR-PRV-7** A maintained privacy evidence pack (DPIA-ready) MUST map every wire
  field to purpose and legal basis and document all scanners/gates.

## 7. Non-functional requirements — Security (NFR-SEC)

- **NFR-SEC-1** Org token: bearer over TLS, masked everywhere, hidden in installer
  logs, stored in machine config readable only by the service/user context; documented
  as an org-shared (not personal) credential.
- **NFR-SEC-2** Report/dashboard HTML: strict no-script/no-external policy (see
  FR-RPT-2); all interpolation escaped.
- **NFR-SEC-3** Backend: authenticated ingest only; strict payload size limits;
  privacy-safe logging; no dynamic query construction from client input.
- **NFR-SEC-4** Supply chain: pinned toolchain versions in CI; checksummed release
  artifacts; signed binaries when secrets present.
- **NFR-SEC-5** The agent runs with least required privilege; no listening sockets on
  the endpoint; outbound connections only to the configured API base URL, only for
  catalog pull and (consent-gated) emit flush, and only when configured.

## 8. Non-functional requirements — Reliability, performance, quality

- **NFR-REL-1 Fail-open everywhere on the endpoint:** a malformed input, missing
  optional dependency, locked store, or failed sub-check MUST degrade that one feature
  (empty result / closed error code) and never crash the service, the tick, the report,
  or the parse.
- **NFR-REL-2** The scheduler cycle is self-isolating per category; the service
  survives indefinitely unattended; a report-generation collision with a user command
  (store lock) resolves by retry, not corruption.
- **NFR-PRF-1** Endpoint footprint targets: idle CPU ≈ 0 between ticks; a full daily
  parse+analyze+render completes in seconds for a heavy developer-day log volume;
  install size small enough for fleet distribution (target ≤ ~50 MB installer; ≤ ~150 MB
  acceptable ceiling); no persistent background UI cost beyond a tray icon.
- **NFR-PRF-2** Dashboard initial load ≤ 2 s on a typical intranet; report opens
  instantly (local file).
- **NFR-CMP-1** The analysis core MUST be OS-portable (Windows/macOS/Linux); Windows-
  only integrations (service, tray, installer) MUST be isolated behind guarded seams so
  the core runs headless anywhere.
- **NFR-CMP-2** Upstream tool format changes MUST degrade gracefully (FR-SRC-5/6) and
  be recoverable by shipping parser updates without data loss (cursor invalidation
  rescan).
- **NFR-CMP-3** Desktop artifacts MUST package pinned IANA timezone data and deterministic
  local-zone resolution (`tzdata==2026.2`, `tzlocal==5.4.4` for this release) so Windows
  and macOS frozen builds do not depend on an optional host timezone database.
- **NFR-QLT-1** Test-first development; every feature lands with its tests in the same
  change; adversarial review against a written spec with numbered, checkable acceptance
  criteria (unchecked ACs fail CI).
- **NFR-QLT-2** Gates that MUST exist from day one: type-checking (zero-error), lint
  ratchet ledger, byte-golden report regression, no-leak/forbidden-lexicon scanners,
  fixture-pack parser conformance, end-to-end “real CLI on real-shaped logs renders the
  feature” tests (fixture-only proof is insufficient — a feature is done when the
  product path exercises it), and real-artifact packaging smoke (mocked packaging tests
  are insufficient — build and run the installer/binary).
- **NFR-QLT-3** All copy user-facing strings live in closed catalogs; a wording change
  is a reviewed diff (goldens make silent drift impossible).
- **NFR-UX-1** Coaching tone: positive-first, max-two observations, dismissible,
  quiet-hours aware, frequency capped — designed to be turn-off-able and never nagging.
- **NFR-UX-2** Accessibility: report navigation must work script-free (static ARIA
  roles documented as the accepted limit); color/contrast per the design tokens;
  keyboard reachability on the dashboard.
- **NFR-UX-3** Default wellbeing-facing copy MUST use confidence, period, evidence, and
  behavioral labels. It MUST NOT display numeric wellbeing-style scores, the label
  `Discipline`, activity-as-productivity claims, or causal improvement claims.
- **NFR-OPS-1** One-command support triage (FR-DIA); machine-readable everywhere
  (closed JSON); every operational failure mode maps to a closed code a runbook can
  reference.

## 9. Platform constraints & recommendations (from prior art)

These are constraints the business has chosen, and hard-won guidance a greenfield team
SHOULD adopt:

- **C-1** Windows-first delivery via MSI/GPO is a hard business constraint (§5).
- **C-2** The desktop shell (tray + service host) SHOULD be a single small native
  binary (Rust-class: `windows-service`, `tray-icon`, WinRT toasts with an AUMID +
  Start-menu shortcut; protocol-activation for toast actions), while the analysis core
  SHOULD remain a high-iteration language runtime shipped as an embedded distribution
  (no static-analysis freezing — the missing-module bug class is otherwise systemic).
- **C-3** Never manage the Windows service through installer custom actions when native
  service tables exist; deferred custom actions read data only via same-Id properties.
- **C-4** The shell reads status directly (read-only) but writes ONLY through the core
  CLI (single-writer discipline).
- **C-5** Toast interaction baseline is whole-toast click; button actions require the
  native shell (C-2) — do not attempt them through cross-platform GUI toolkits.
- **C-6** Keep the tray/service as thin shells over the CLI so the shell is replaceable
  without touching the brain.

## 10. Explicit non-goals / anti-requirements

- NO interception, blocking, or modification of AI requests (no proxy in the path).
- NO per-user outcome attribution or individual productivity scoring.
- NO raw-content analytics on the server, ever.
- NO runtime dependency on third-party trackers/telemetry SDKs.
- NO always-on LLM inference in the default analysis path.
- NO person-level dashboard, exports, or admin queries.
- NO lockout/enforcement features in wellbeing coaching (advice only).
- Research-only sources (e.g. desktop app cache probing) stay research-only until they
  pass the same fixture + privacy gates as first-class sources.

## 11. Initial acceptance snapshot (definition of “feature-complete v1”)

1. Fresh silent install on a clean Windows machine via one msiexec line with org
   properties → per-user tray at logon, private config seeded, token
   masked everywhere; uninstall preserves data; REMOVE_DATA removes it.
2. Developer uses Codex + Claude Code normally → next tick produces a daily report with
   spend, mix, efficiency, focus (positive-first), source health; report is
   byte-deterministic offline HTML passing all no-leak scans.
3. Two hours of continuous use → exactly one break nudge, quiet-hours and toggle
   respected; dismissal of a tip removes it from every surface permanently.
4. `doctor` on a broken install pinpoints the failing check with exit 1 in one command.
5. Consent OFF (default): zero outbound traffic except catalog pull if configured.
   Consent ON: emits validate against the closed schema; a v-next payload is rejected
   with the distinct code and dead-letters; the dashboard shows k-anon aggregates with
   honest suppression.
6. Upstream tool ships a new log field → parse continues, drift counters appear in
   report + doctor + advisory alert; no crash, no silent data loss.
7. All CI gates green: types 0-error, lint ledger, goldens ×2 runs byte-stable,
   fixture pack, packaging smoke on the real artifact, privacy suites.

---

*End of specification.*
