# PracticeGraph architecture

Implementation of [REQUIREMENTS_SPEC.md](REQUIREMENTS_SPEC.md). This file records the
decisions that are not obvious from the code and the milestone plan.

## Stack decisions (spec-driven)

| Component | Choice | Why (spec ref) |
|---|---|---|
| Analysis core | Python 3.14.7 release runtime, standard library plus two pinned timezone-only dependencies (`tzdata`, `tzlocal`) | Deterministic IANA data and Windows-to-IANA resolution across desktop builds; otherwise a tiny OS-portable core (NFR-CMP-1, NFR-SEC-4) |
| Desktop shell (Windows) | Small native binary (Rust): per-user tray scheduler, WinRT toasts | C-2, C-5, C-6 — thin shell over the core CLI, single-writer discipline (C-4) |
| Desktop shell (macOS) | Native Swift app: menu-bar item, WKWebView window, `practicegraph:` scheme, login item + LaunchAgent tick | Same C-2/C-5/C-6 contracts as the Windows shell; the portable engine (NFR-CMP-1) is shared unchanged |
| Backend (later) | Containerized passive REST API, pluggable storage | FR-API, INV-2 |
| Dashboard (later) | Static build, versioned API only | FR-DSH-5 |

## Core conventions

- **Money** is integer micro-USD everywhere (1 USD = 1,000,000). No floats in the money
  path; formatting is integer half-up. This is what makes estimates byte-reproducible
  (INV-6, FR-RPT-3).
- **Time has two explicit day meanings.** Persisted timestamps and financial/history
  accounting remain UTC. Personal focus, day-close, preferred-hours, quiet-hours, and
  observation windows derive local days from the versioned confirmed IANA schedule in
  `analysis/schedule.py`; an unconfirmed suggestion never enables schedule claims.
- **Timestamps** are timezone-aware or rejected (`TurnEvent.__post_init__`). Naive
  timestamps read from logs are interpreted as UTC (documented fail-open).
- **Closed vocabularies** are `StrEnum`s pinned by exact-value tests (NFR-PRV-2).
  Changing one is a reviewed contract change by construction.
- **Presence flags, never values** (FR-SRC-4): parsers may inspect content transiently
  (e.g. to detect an interruption marker) but only booleans/counters reach `TurnEvent`.
- **Token semantics**: `TokenCounts.input` always *excludes* cached tokens. The Codex
  parser normalizes OpenAI-style usage (cached ⊆ input) at the boundary so pricing is
  uniform (FR-ANL-1: cached pricing separated from input pricing).
- **Renderers are pure functions** of `(snapshot, generated_at, versions)`. The
  generation timestamp is injectable (`--generated-at`) which is what golden tests pin.
- **Fail-open** (NFR-REL-1): per-line, per-file, and per-source guards; every skip class
  is counted in `ParseHealth` and surfaced (report, doctor) without recording the
  unknown token/name itself (FR-SRC-6).

## Module map

| Module | Requirements |
|---|---|
| `events.py` | FR-SRC-4 event model, closed vocabularies (FR-SRC-3, FR-RPT-7) |
| `sources/` | FR-SRC-1/2/5/6 adapters + framework; `claude_code.py`, `codex.py` |
| `analysis/ratecard.py` | FR-ANL-1 versioned rate card, integer cost estimation |
| `analysis/aggregate.py` | Daily snapshot the renderers consume |
| `analysis/schedule.py` | Versioned, append-only personal schedule history; IANA validation, OS timezone suggestion, DST-safe local-day bounds, preferred/quiet/off-schedule classification. `tzdata==2026.2` and `tzlocal==5.4.4` are pinned and packaged. |
| `analysis/performance.py` | Six closed 0-100 heuristic dimensions over local counters. They are explicitly experimental, secondary practice diagnostics; default wellbeing-facing surfaces do not expose their scores (NFR-PRV-6). |
| `report/text.py`, `report/html.py` | FR-RPT-1/2/3/4 offline deterministic renderers |
| `report/observation.py` | Deterministic selection of at most one evidence-gated work-pattern observation with closed confidence, period, and caveat; no health or causal inference. |
| `report/shell.py` | FR-RPT-1 multi-view shell + FR-RPT-5 Privacy Center. Today leads with the daily brief and qualified observation, then local-time day shape and one change. Numeric trajectories/performance are secondary under Experimental Practice detail. Range accounting remains UTC. Report action links use the `practicegraph:` protocol (closed verbs; still script-free). |
| `report/brief.py` | The daily brief: closed sentence-template catalogs (celebrate/flag/action per dimension), cost typicality vs own 28-day quartiles, weekly switch-rate baseline (capped multiple phrasing), Monday review line. Deterministic assembly, lexicon-scanned (FR-FOC-8) |
| `wire.py` | INV-3 closed wire schema + validator (endpoint AND server side) |
| `store.py` | FR-CFG-3 single local store; FR-EMT-2 queue states |
| `emit.py`, `transport.py` | FR-EMT-1/2/3 aggregates, backoff, closed error codes |
| `agent.py` | FR-ALR-1 scheduler tick, fail-open categories |
| `doctor.py` | FR-DIA-1/2/3 closed-JSON triage |
| `config.py`, `consent.py` | FR-CFG-1/2, FR-CNS-1 |
| `privacy.py` | NFR-PRV-1 leak scanners, FR-FOC-8 forbidden lexicon |
| `winsec.py` | NFR-SEC-1 DPAPI machine-scope secret protection (POSIX seam: 0600 file) |
| `cli.py` | Front door; the only writer of local state (C-4) |
| `practicegraph_server/` | INV-2/FR-API passive backend; `view.py` k-anon (NFR-PRV-3), `render.py` no-script dashboard (FR-DSH) |
| `ui/`, `uiserver.py` | Local React Today surface and loopback API. View schema `practicegraph.view/2` carries `local_day`, `accounting_day_utc`, confirmed schedule state, and one qualified observation; these local fields never enter wire payloads. |
| `shell/` (Rust) | C-2 native **Windows** shell: per-user scheduler, win32 tray, WinRT toasts, Event Log — thin over the CLI (C-6), reads only status.json/reports (C-4) |
| `macos/` (Swift) | C-2 native **macOS** shell: NSStatusItem menu bar, WKWebView dashboard window, `practicegraph:` scheme, SMAppService login item — mirrors `shell/`'s jobs against the same engine + `webui/`; the periodic tick is a LaunchAgent, not a service |
| `packaging/` | Windows bundle builder (embedded runtime per C-2) + `msi/Package.wxs` (FR-DEP-1..4, user-context finish-dialog launch); `packaging/macos/` freezes the engine, assembles `PracticeGraph.app`, signs + notarizes a `.dmg` |

### Personal data and fleet boundaries

- The Windows tray runs as the signed-in user and reads that user's source logs.
  Machine-wide collection and automatic shared-store adoption are retired.
- Data directories and session state are owner-restricted. Atomic config writes
  and backups preserve existing file permissions and refuse linked targets.
- The optional server authenticates a separate opaque source identity for each
  contributor and stores one completed-day contribution per source. Replayed emit
  IDs and rotated secrets cannot increase the cohort size. Administrator enrollment
  remains responsible for assigning one source identity per independent contributor.
- Aggregate reads require a separate admin credential. Ingest credentials are
  write-only; historical unattributed rows never enter released cohorts.
- Public guidance comes from the website's versioned catalogs. Generic parsers and
  synthetic tests are open source; private authoring and archives remain separate.
- WiX 5.0.2 builds the per-user MSI. The build verifies the runtime download,
  redistribution notices, exact installer payload and personal startup flow.

### Parser accounting rules (cross-checked against CodexBar's implementation)

- Claude Code streams the same API call repeatedly with *cumulative* usage
  under one `message.id + requestId` — dedup last-wins, never sum. The same
  call can appear in parent and subagent transcripts — first file wins
  (sorted order puts parents first).
- Codex can emit repeated `token_count` events per turn — per-turn deltas
  derive from `total_token_usage` against a per-file baseline; `last_token_usage`
  is only trusted when totals are absent; regressing totals (compaction) rebase.
- OpenAI `cached_input_tokens` ⊆ `input_tokens` (normalized out); Anthropic
  cache fields are separate from `input_tokens`. `TokenCounts.input` always
  excludes cached.

## Milestone plan

- **M0 — walking skeleton** *(done 2026-07-03)*: two source adapters with drift
  counters, cost estimation, deterministic text+HTML daily report, doctor, privacy
  scanners, golden/e2e/no-leak test gates.
- **MA — emission boundary, agent, backend, admin UI** *(done 2026-07-03)*:
  closed wire schema + validator (INV-3), SQLite store + emit queue with backoff and
  terminal version rejection (FR-EMT-1/2/3), consent CLI (FR-CNS-1/2), agent scheduler
  loop with fail-open categories (FR-ALR-1 core), multi-view shell + Privacy Center
  (FR-RPT-5), `report --open` (FR-RPT-6), passive backend with authenticated ingest and
  k-anon views (FR-API, NFR-PRV-3), server-rendered no-script dashboard with honest
  suppression (FR-DSH), Docker/compose (FR-DEP-7), pilot workstation bundle +
  install/uninstall scripts (embedded runtime per C-2), CodexBar-verified parser
  accounting (dedup, totals-delta).
- **M1 — incremental reads & history** *(done 2026-07-03)*: per-file cursor
  fingerprints (identity hash + size + mtime + parser version) with rotation/truncation
  detection, version-bump invalidation, and deleted-file pruning (FR-SRC-7); atomic
  per-file replacement into `file_contributions`/`activity_marks` so re-parses never
  double-count; persisted per-lane parse health (FR-SRC-6); spend series + week-over-
  week (FR-ANL-7); `hygiene` command (FR-CFG-4, un-sent emits never pruned). The
  store-backed snapshot is pinned byte-equal to the direct-parse snapshot.
- **M2 — catalog pull** *(done 2026-07-03)*: once-per-day pull under a single
  coordination version with closed failure codes; artifacts strictly validated
  (closed schema, atomic swap) before touching disk; rate-card activation with
  bundled fallback so pull failures never block local analysis (FR-EMT-4). Doctor
  reports rate-card source and catalog status.
- **M3 — focus coaching** *(done 2026-07-03)*: FR-FOC-1 daily metrics from the local
  activity timeline (named-constant thresholds), the closed five-tip catalog with
  fixed copy + permanent dismissal (FR-FOC-3/6), positive-first max-two rendering
  (FR-FOC-2), the 120-minute break nudge with cross-cycle streak state and
  never-refire dedupe (FR-FOC-4), the single master switch (FR-FOC-5), and
  wire-absence enforcement by field-name scan (NFR-PRV-6).
- **M4 — insight analysis** *(done 2026-07-03)*: closed efficiency detectors
  (FR-ANL-2), structural work-type taxonomy build/investigate/converse/unknown
  (FR-ANL-3), model-fit advice (FR-ANL-4), maturity signals with the design's level
  ramp (FR-ANL-5), spend pace vs monthly budget (FR-ANL-6), suggestion
  dismiss/never-suggest lifecycle filtering every surface (FR-RPT-8), all copy in
  closed lexicon-scanned catalogs (NFR-QLT-3).
- **M5 — alert engine** *(done 2026-07-03)*: closed categories/reasons
  (daily_report_ready, spend_pace, source_health, focus_break), per-category toggles,
  1/day caps with persisted fired-state, quiet hours (suppression does not consume the
  cap), WinRT toast delivery through the native shell with closed delivery codes
  (FR-ALR-1..4).
- **M6 — dashboard depth** *(done 2026-07-03, via wire schema v2)*: the governed
  contract change (NFR-PRV-2) shipped as schema v2 — two additive closed fields,
  `work_type_sessions` (closed map) and `maturity` (signal→level enums); schema,
  validator, fixtures, and docs moved together; the server supports {1, 2} and v1
  agents stay valid (FR-EMT-3 operational rule). The dashboard gains the fleet
  work-type mix and the maturity distribution with ramp colors; maturity level
  buckets smaller than k withhold the whole card ("withheld, not zero" — INV-4).
- **M7 — deployment:** the former LocalSystem collector has been retired. Current
  Windows packages are per-user and the optional aggregate server is separately
  deployed. Signing, disposable-VM installation/upgrade checks, and macOS native
  validation remain release gates. See [release checks](docs/RELEASING.md).

## Testing doctrine (NFR-QLT-1/2)

Every feature lands with: fixture-pack conformance, byte-golden rendering (×2 runs),
no-leak scans over every surface, and an end-to-end test that drives the *real CLI*
over real-shaped logs. Golden regeneration is a deliberate script run, reviewed as a
contract change.
