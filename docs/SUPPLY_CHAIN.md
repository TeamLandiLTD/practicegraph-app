# Supply chain

The application runs as the signed-in user. Release controls cover the runtime,
compiled shell, frontend build, packages, and source export.

- Windows embeds CPython 3.14.7 from an official download with a reviewed SHA-256.
- Shipped Python wheels are pinned with hashes in `packaging/shipped-requirements.txt`.
- Rust notices and dependency metadata use the resolved Windows GNU target graph.
- `npm ci` enforces the lockfile; CI audits development and production packages.
- Microsoft WebView2 SDK redistribution notices are retained. Provenance in
  `packaging/third-party/webview2-sdk-provenance.json` matches the loader DLL and
  static library to the official NuGet package. Changed bytes fail notice generation.
- macOS notices include Python, frozen timezone packages, PyInstaller's bootloader
  license/exception, and production frontend dependencies.
- The server container uses a digest-pinned official Python image, runs as UID
  10001, and includes the root LICENSE and NOTICE.
- MSI verification extracts the actual installer and compares every file path
  and SHA-256 against staging. Its SBOM inventories the extracted files and records
  the installer hash. npm integrity values are decoded to hexadecimal hashes;
  frontend runtime packages are distinguished from development dependencies.
- Source publication uses an explicit inventory and a commit-based export without
  the old private Git history. Private planning and original editorial fixtures
  are excluded; synthetic parser fixtures are public.

Run Python `pip-audit`, `npm audit --audit-level=moderate`, and
`cargo +stable-gnu deny --target x86_64-pc-windows-gnu check`.
Scanners report known issues; they do not establish that dependencies are harmless.

Signed update notices remain disabled until the real maintainer public key is
embedded. Authenticode/Apple signing and the update signature are separate.
Local test builds are unsigned candidates. Full reproducible builds and startup
verification of each installed web asset are not implemented. See
[release checks](RELEASING.md) for platform validation and publication requirements.
