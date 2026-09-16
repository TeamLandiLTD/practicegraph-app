# macOS desktop candidate

PracticeGraph's Swift shell provides a menu-bar entry, a native WKWebView
dashboard, `practicegraph:` actions and an optional Open at Login setting. It
uses the same Python analysis engine and React UI as the other desktop builds.
The shell targets macOS 13 and later. CI builds separate Apple Silicon and Intel
candidates; only completed native runs are evidence that those artifacts work.

## Build on a Mac

Install the Xcode command-line tools, Python 3.14.7 and Node 22. The shell links
AppKit, WebKit and ServiceManagement, so it cannot be compiled on Windows.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]' -r packaging/posix-build-requirements.txt
bash packaging/macos/build_macos.sh --dmg
```

The output is `dist/macos/PracticeGraph.app` plus a disk image named with its
version and architecture. Shell and engine architectures must match. A default
candidate is ad-hoc signed for development and is not a public release.

The build always rebuilds the UI. It includes LICENSE, NOTICE, dependency
notices, source identity and bundled-file hashes. Python is frozen for delivery;
readable source is permitted under Apache-2.0. The old no-readable-source gate
has been retired. `--engine-tool nuitka` remains an optional build format;
`--skip-engine` is only for local development and can reuse stale code.

## Sign and notarize a release

With a Developer ID Application identity and an already configured notarytool
credential profile on the Mac:

```bash
bash packaging/macos/build_macos.sh \
  --sign 'Developer ID Application: TeamLandi (TEAMID)' \
  --notarize-profile pg-notary --dmg
```

The build signs nested Mach-O files before the app, submits the disk image,
staples the ticket and performs the final assessment. Notarization requires
both `--sign` and `--dmg`. Credentials and signing keys are never part of the
repository or app. Follow [Apple's notarization guidance](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
when provisioning the release machine. Test the downloaded artifact under
normal Gatekeeper settings; do not use quarantine removal as a release test.

The shell admits only the authenticated engine's exact loopback origin.
External HTTPS links open in the user's browser, and local record exports use
a native save panel. App Transport Security governs the webview; it is not a
firewall for the Python engine. Hosted catalog downloads and explicitly enabled
optional data flows retain their documented behavior.

## Personal data and optional background refresh

The app initializes its store on first use. Records live at
`$XDG_DATA_HOME/practicegraph`, normally `~/.local/share/practicegraph`.
`PRACTICEGRAPH_DATA_DIR` overrides that location. This preserves compatibility
with existing Mac installations. Moving to Library/Application Support would
need a deliberate migration.

Opening the dashboard reads current activity. For optional refresh while the
window is closed, the repository includes a per-user LaunchAgent installer:

```bash
bash packaging/macos/install-agent.sh --app /Applications/PracticeGraph.app
bash packaging/macos/install-agent.sh --uninstall
```

The helper requires Python 3 to safely serialize its plist and runs the engine
every fifteen minutes under the current user. It installs no system daemon.
The LaunchAgent is separate from the app's Open at Login option. If moving or
removing the app, remove that job first or reinstall it for the new path.
Neither removal path deletes personal records.

## Verification and release status

```bash
(cd macos && swift test)
python packaging/smoke_desktop.py \
  --engine dist/macos/PracticeGraph.app/Contents/Resources/practicegraph-engine
```

Swift tests cover endpoint token and origin boundaries. The built-engine smoke
uses isolated synthetic records, disables catalogs, and exercises collection,
dashboard serving, private record APIs and authentication. CI uploads the
candidate disk image, checksum and provenance so reviewers can identify the
exact build.

Before public release, verify on an actual Mac: a downloaded notarized install,
first launch, menu-bar reopen, Cmd-C/V and keyboard navigation, external links,
JSON export and restore, login behavior, quiet background refresh, upgrade,
and uninstall with records preserved. A successful compile or engine smoke does
not establish that these interactions passed. The signed-update trust key is
still a separate release configuration; candidates do not install updates.
