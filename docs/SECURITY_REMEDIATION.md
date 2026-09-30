# OSS and security remediation — September 2026

The release-preparation review found flaws in contributor counting, fleet read
authorization, shared-machine privacy, authenticated HTTP redirects, configuration
write permissions, runtime maintenance, dependency auditing, and source export.

Implemented corrections:

| Area | Change |
| --- | --- |
| Cohort suppression | Server-provisioned source credentials; one source/day contribution; legacy unattributed records excluded |
| Fleet authorization | Separate administration credential on every aggregate read; no ingest fallback |
| Personal privacy | Per-user collection only; shared store adoption and machine-wide builds disabled; owner-restricted state |
| Transport | Ingest redirects refused; only 202 acknowledges submission |
| Config edits | Private atomic writes and backups preserve existing ACLs/modes; links refused |
| Catalog network | Checked public IPs pinned to TLS connections; redirects revalidated; ambient proxies disabled |
| Catalog authenticity | Public pulls refuse unsigned editions (2026-09-16 open-source readiness review): the pinned publisher key, not control of the content host, is the trust anchor for served prompts, playbooks and model pins |
| Runtime and dependencies | Verified Python 3.14.7; refreshed npm lockfile; CI audits all frontend dependencies |
| Public source | Explicit file inventory, committed-byte export without private history, synthetic fixtures |
| Redistribution | Reviewed Microsoft SDK notices/provenance; frozen macOS notices; container LICENSE/NOTICE |
| Artifact evidence | Exact extracted MSI payload hashes and installer-linked SBOM; per-user runtime smoke |

Regression tests cover both memory and SQLite contributor stores, concurrent
retries, role separation, redirect status codes, Windows ACL preservation, links,
POSIX permissions, and checked-address TLS connections.

Release gates still require the actual maintainer update-verification public key,
a confirmed monitored private reporting channel, supported-platform install/upgrade
and signing checks, and deliberate source/release publication. Preparing CI checks
does not mean those checks have run on every platform.

Public JSON is readable and copyable. The hosted catalog manifest is an index of
hashes and versions served over TLS and is not signed itself; the app does not
rely on it, and instead verifies each edition's own publisher signature. Keep authoring,
candidate research and archives private, and apply the separate published content
terms. Neither moving material nor this release retracts rights already granted.

The internal audit retained evidence privately. This document describes remedies
and their limits without including personal data or private exploit artifacts.
