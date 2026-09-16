# Releasing PracticeGraph

The application is free and Apache-2.0 licensed. Publish reviewed source with the
binaries. Private editorial authoring and the old private repository history
must remain separate from the public application.

## Public source

Maintain `packaging/public-source-files.txt` as the explicit public inventory.
New application modules and tests must be listed; other new files are excluded
until reviewed. Use synthetic fixtures, never private editorial recommendations.

After committing the reviewed source:

```powershell
python tools/public_source.py --check
python tools/public_source.py --revision HEAD --out dist/PracticeGraph-source.zip
```

The exporter reads committed bytes, preserves executable flags, and writes a
SHA-256 provenance record beside the ZIP. Local changes are not exported.
Initialize a **new repository from that export** when publishing the first
public source tree. Do not change the private development repository's visibility
or push its branches/tags to the public repository. Export exclusions cannot
remove private material from historical commits or old archives.

Unpack the exact ZIP in a fresh directory, inspect its inventory, scan it for
secrets and editorial/private material, and run its tests before publication.
Retain the ZIP and provenance alongside the installer and SBOM.

### Screenshots

`docs/screenshots/*.png` come from a synthetic profile, never from a
maintainer's own logs (they are personal work-pattern readings). Regenerate
them with the demo generator, a throwaway data directory and a headless
browser at 1280 px wide:

```powershell
uv run python -m tools.demo_logs --out $env:TEMP\pg-demo\logs --days 35 --seed 7 --end (Get-Date -Format yyyy-MM-dd) --end-hour (Get-Date).Hour --tz Europe/Sofia
$env:PRACTICEGRAPH_DATA_DIR = "$env:TEMP\pg-demo\data"
$env:PRACTICEGRAPH_CLAUDE_HOME = "$env:TEMP\pg-demo\logs\claude"
$env:PRACTICEGRAPH_CODEX_HOME = "$env:TEMP\pg-demo\logs\codex"
uv run python -m practicegraph init
uv run python -m practicegraph schedule set --timezone Europe/Sofia --working-days mon,tue,wed,thu,fri --work-start 09:00 --work-end 18:00 --quiet-start 22:00 --quiet-end 07:00 --weekend-mode exceptional
uv run python -m practicegraph agent run --once
uv run python -m practicegraph ui serve
```

Then capture each page from the endpoint in `ui.json`: `--window-size=1280,980`
for `#spend`, `1280,1100` for `#models`, `#tools` and `#news`, `1280,1150` for
`#api-prices`, `1280,1200` for `#mindfulness`, with `--headless=new
--hide-scrollbars --virtual-time-budget=30000`. `--end-hour` keeps the last
day's readings before the moment of capture so the limits card shows times.

## Build and verify

Use Python 3.14.7 for release runtimes. The Windows build verifies the official
embeddable ZIP SHA-256 and shipped wheel hashes. Install full MinGW GCC/binutils
on PATH, Rust stable GNU, Node.js 22, and WiX 5.0.2.

```powershell
cd ui
npm ci
npm audit --audit-level=moderate
npm test
npm run build
cd ..
pwsh -File packaging/build_workstation.ps1 -Msi
```

The per-user MSI is the supported Windows package. Machine-wide personal stores,
LocalSystem collection, and shared in-place refits are retired. Existing shared
data is left untouched; users initialize fresh personal stores from their own
logs. Remove an old service only through a deliberate administrator migration.

The build validates MSI tables, extracts every payload file, compares exact
paths and hashes, and exercises the shipped interpreter, personal initialization,
agent tick, dashboard token rejection, and view using synthetic logs. Its SBOM
hashes the **finished MSI contents**, not a staged directory altered by smoke
tests. Build records identify the source commit and dirty state.

These checks do not replace a fresh install, upgrade, uninstall, native-window,
and multi-user isolation test in a disposable Windows VM. On macOS, build with
`packaging/macos/build_macos.sh`; CI checks the app shape, frozen engine, and
redistribution notices. A signed/notarized release needs the actual macOS
signing environment. Container CI builds the digest-pinned server image and
checks its non-root identity and included LICENSE/NOTICE.

## Two repositories

Internal builds come from this repository. Public installs and update checks
come from the public app repository, `TeamLandiLTD/practicegraph-app`, which
receives the reviewed source export (`tools/public_source.py`) and the release
assets, never private history. The client pins that repository in the binary
(`analysis/update.py: RELEASE_REPO`): it downloads
`/releases/latest/download/update.json` from it once a day, verifies the
Ed25519 signature, shows the update card in the app, and raises one native
toast per newer version outside quiet hours. It never downloads or installs
on its own. The URL form only resolves on a public repository.

Every public release therefore attaches three files: the MSI, the signed
`update.json` minted by `packaging/sign_release.py`, and the SBOM. The
Windows build prints the exact commands at the end of a run.

## Signing and publication

`UPDATE_PUBLIC_KEY_HEX` must contain the maintainer's Ed25519 **public**
verification key before official signed update notices can work. The empty
default trusts no manifest. Never commit a private signing key or generate a
replacement casually: the maintainer must control its custody and rotation.

Use `packaging/sign_release.py` with an offline-held key to sign the release
manifest. Authenticode and Apple Developer ID signing are separate from the
update-manifest signature. An unsigned candidate is not a trusted public release.

Before publishing, establish a monitored private vulnerability-reporting channel,
verify release signing and install/upgrade flows on supported platforms, and
review the exact source ZIP, MSI/DMG, SBOM, notices, and catalog version record.
Attach source and provenance as well as platform binaries. Bump the application
version for a distributed upgrade; never replace an existing version's assets.

The client verifies the update signature, version, asset URL, hash and size,
then offers a download link. It does not automatically download or install.
Follow [CODE_SIGNING.md](CODE_SIGNING.md) for certificate handling and
[SECURITY.md](../SECURITY.md) for contribution execution boundaries.
