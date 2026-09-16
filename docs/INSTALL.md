# Install

PracticeGraph is a per-user desktop application. Each person installs their own copy, which reads their own tools' logs. No administrator rights, service, account or license key is involved.

## Windows

Windows 10 or 11 with Microsoft WebView2, which current builds of both include.

1. Download `PracticeGraph-<version>.msi` from the [latest release](https://github.com/TeamLandiLTD/practicegraph-app/releases/latest).
2. Verify it. Each release publishes the installer's SHA-256:

   ```powershell
   Get-FileHash .\PracticeGraph-<version>.msi -Algorithm SHA256
   ```

3. Run the installer. Builds are not yet Authenticode-signed, so SmartScreen objects once: *More info → Run anyway*.
4. Open PracticeGraph from the Start menu. The tray icon runs the background tick about every 15 minutes while it is present, and left-clicking it opens the dashboard window.

The application installs under `%LOCALAPPDATA%\Programs\PracticeGraph` with a readable Python engine, the built dashboard, LICENSE and NOTICE. `BUILD-INFO.txt` in that folder records the source commit the build came from. The engine's command line is available as:

```powershell
& "$env:LOCALAPPDATA\Programs\PracticeGraph\runtime\python.exe" -m practicegraph doctor
```

### Upgrade

Install the newer MSI over the old one. Every build is its own version and a version number is never reused. The data directory is untouched and the store migrates itself forward on first open. The app tells you about a newer release once a day; it never downloads or installs on its own.

### Uninstall

Use *Settings → Apps* or the Control Panel entry. Uninstalling removes the application and leaves your data directory alone; delete `%LOCALAPPDATA%\PracticeGraph` yourself if you want the record gone.

## macOS

A native Swift shell exists: a menu-bar item, the dashboard in a WKWebView window, `practicegraph:` actions and an optional Open at Login setting. It targets macOS 13 and later and builds separately for Apple Silicon and Intel.

Signed and notarized downloads are not published yet. Until they are, build a candidate on a Mac with the Xcode command-line tools, Python 3.14 and Node 22:

```bash
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]' -r packaging/posix-build-requirements.txt
bash packaging/macos/build_macos.sh --dmg
```

The result is `dist/macos/PracticeGraph.app` and a disk image. A default candidate is ad-hoc signed; clear the quarantine flag once if you copy it between machines. The optional per-user background tick installs as a LaunchAgent. Everything, including signing and notarization, is in the [macOS guide](MACOS_BUILD.md). Earlier releases carried an Intel zip that runs on Apple Silicon through Rosetta.

## Linux

A GTK 3 and WebKitGTK 4.1 shell opens the same dashboard in a native window. The build target is Ubuntu 24.04 amd64, producing a `.deb`, a tar archive and checksums:

```bash
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[dev]' -r packaging/posix-build-requirements.txt
bash packaging/linux/build_linux.sh
sudo apt install ./dist/linux/practicegraph_<version>_amd64.deb
practicegraph-desktop
```

No background service or autostart entry is installed; this first Linux shell has no tray integration. Other distributions, arm64 packages, AppImage and RPM are not offered until they have been tested. See the [Linux guide](LINUX_BUILD.md).

## Where data lives

| Platform | Data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\PracticeGraph` |
| macOS | `~/.local/share/practicegraph` |
| Linux | `$XDG_DATA_HOME/practicegraph`, by default `~/.local/share/practicegraph` |

Override it with `PRACTICEGRAPH_DATA_DIR`. The directory holds one SQLite store, cached editions, the configuration file and the dashboard's endpoint file. The engine is the only writer.

### Where logs are found

| Tool | Default locations | Override |
| --- | --- | --- |
| Claude Code | `~/.claude/projects`, `~/.config/claude/projects`, `CLAUDE_CONFIG_DIR` | `PRACTICEGRAPH_CLAUDE_HOME` |
| Codex | `~/.codex/sessions` and `archived_sessions`, honoring `CODEX_HOME` | `PRACTICEGRAPH_CODEX_HOME` |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/{local-agent-mode,claude-code}-sessions` | `PRACTICEGRAPH_CLAUDE_DESKTOP` |

## Build from source

Any platform with Python 3.14 and Node.js 22 can run the engine and the dashboard in a browser tab, which is enough for development and for reading your own logs:

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
practicegraph init
practicegraph ui serve --open
```

The repository ships the built dashboard in `webui/`. Rebuild it after UI changes with `npm ci && npm run build` in `ui/`. The native Windows installer is built with `packaging/build_workstation.ps1 -Msi`; its toolchain and gates are in [packaging/README.md](../packaging/README.md) and the [release guide](RELEASING.md).
