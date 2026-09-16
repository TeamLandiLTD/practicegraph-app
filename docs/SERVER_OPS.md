# Aggregate server operations

The optional server accepts closed-schema aggregate submissions, serves catalogs,
and exposes aggregate views. It has no access to personal endpoint databases.
Deploy behind TLS; publish its container port on host loopback only.

## Configure and start

Copy `deploy/.env.example` to the ignored `deploy/.env`, replace its placeholders,
then run:

```sh
docker compose -f deploy/compose.prod.yaml --env-file deploy/.env up -d --build
```

Use a distinct random ingest credential for each independent contributor.
`PG_SERVER_INGEST_TOKENS` is a JSON object mapping an opaque stable source ID to
a secret of at least 32 characters. Generate secrets with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Do not use names or email addresses as source IDs. Supply each endpoint only
its own credential through its protected token configuration.

The server hashes the authenticated source ID with the organization ID for
storage; it does not accept contributor identity from the submitted payload.
Only one contribution per organization, source, and day is retained. **The first
accepted contribution wins**; retries or new emit IDs from that source/day
receive an idempotent acknowledgement without increasing cohort size.

Keep the stable source ID when rotating its secret, then restart the server and
update the endpoint credential. Creating a new ID for the same person can
inflate contributor counts. Provision one ID per independent contributor,
including when one person uses multiple installations. Administrator enrollment
is the trust boundary: credentials cannot prove distinct humans or prevent
collusion by authorized contributors.

`PG_SERVER_ORG_TOKEN` is a migration-only shared ingest credential. With no
source map, every holder counts as **one source**, regardless of emit IDs.
When a source map is configured, the shared credential is disabled.
Shared-token fleets will not reach the default minimum cohort of five.

Set a separate `PG_SERVER_DASHBOARD_PASSWORD` for administration. It permits
HTTP Basic on the dashboard, and Basic or Bearer on aggregate APIs. Ingest
credentials never authorize those reads. With no dashboard password, all
administrative reads are disabled. Reusing any ingest credential as the admin
password is rejected. Unauthenticated catalog and health responses contain
no organization aggregates.

The environment loader enforces `PG_SERVER_K_THRESHOLD >= 5`.
The threshold counts authenticated sources, not payloads.

## Upgrade and existing data

Back up the SQLite database while the server is stopped, deploy the new code
or image, then restart. Schema version 2 adds authenticated source attribution.
Old rows remain stored, but are excluded from released cohorts because they
cannot be attributed safely after the fact. Do not backfill them from emit IDs.
New attributed contributions rebuild eligible cohorts.

The retired shared personal workstation database is unrelated to this server
database. No automatic shared-to-personal data adoption takes place.

For native Windows server deployment, use `deploy/install-server.ps1` with
Python 3.14.7 and `-IngestTokensFile` pointing at a privately provisioned JSON
source map. This server installation is separate from the per-user app.
Keep its environment file and data restricted to the service account/admins.
Container deployment provides the simpler isolated reference.

## Operations

`GET /healthz` is unauthenticated and indicates that the process responds.
It does not prove database writability. Verify an admin-authenticated aggregate
request after configuration changes. Logs must never contain tokens or bodies.

`PG_SERVER_RETENTION_DAYS` prunes contributions older than the configured
window. Zero keeps them indefinitely. Stored submissions are individual
aggregate records **before cohort suppression**; treat the database and backups
as confidential organization data, not as a public anonymized dataset.

Stop before copying the SQLite database; restore with the server stopped and
preserve the container UID/GID (10001). Do not expose backup or static
`render-dashboard` output publicly. The latter deliberately bypasses HTTP
authentication because it is a local administrator command.

Endpoint submissions use HTTPS (loopback HTTP is for local testing), reject all
redirects, and accept only HTTP 202 as success. Configure the final ingest URL.
Public catalog fetches resolve and validate destination addresses at connection
time and connect to those checked addresses. Ambient proxy variables are ignored
for these fetches. [Hosted content](HOSTED_CONTENT.md) explains catalog delivery.
