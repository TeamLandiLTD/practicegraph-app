# Troubleshooting

Start with the app's own diagnosis:

```bash
practicegraph doctor
```

It prints install health as closed JSON: data directory, store version, parser health per source, cached editions, consent state and whether an organization token is present. Attach that output to a bug report; it contains no log content.

On an installed Windows build the command lives at `%LOCALAPPDATA%\Programs\PracticeGraph\runtime\python.exe -m practicegraph doctor`. On a source checkout use the virtual environment's `practicegraph`.

## Installing

**SmartScreen blocks the installer.** Builds are not yet Authenticode-signed. Choose *More info → Run anyway* once, after verifying the SHA-256 published with the release.

**The installer says WebView2 is missing.** The dashboard needs Microsoft WebView2, which ships with current Windows 10 and 11. Install the evergreen runtime from Microsoft, then run the installer again.

**Upgrading.** Install the newer version over the old one. Never reuse a version number; each build is its own version. Your data directory is untouched and the store migrates itself forward on first open.

## Opening the app

**The window shows "Reading this machine…" for a long time.** The engine is starting. After an install or upgrade it may re-read months of history from your tools' logs; nothing is lost, and the window becomes the dashboard the moment the engine answers.

**"Could not reach the local agent".** The window could not find a running dashboard server. The native window recovers by itself when the engine comes back. If it does not, quit the tray icon and reopen the app, then run `practicegraph doctor`.

**"This page may be out of date".** The background tick has not run recently, usually because the tray application is not running. Reopen PracticeGraph from the Start menu; the tray icon schedules the tick about every 15 minutes.

**The page shows "PracticeGraph hit an error".** A component failed to render. Reload with the button. The error and its stack are on the card under *Details for an issue report* and are kept in the dashboard's local storage under the key `pg-last-error`. Please open an issue with the message; it contains no log content.

**The window is blank.** Versions before 0.2.16 could lose the whole page to one rendering error. Upgrade; the current version shows the error card described above instead.

## Readings

**Usage is empty.** No supported source was found. Check `practicegraph doctor` for the paths it scanned, and set `PRACTICEGRAPH_CLAUDE_HOME` or `PRACTICEGRAPH_CODEX_HOME` if your tools keep logs elsewhere.

**Some turns have no listed price.** The model is not in the active rate card. The count is shown and the total runs low rather than guessing. Rate cards update as published editions.

**An edition is missing (news, prices, build ideas).** Editions are downloaded once a day and cached. A missing edition means the download failed validation or has not run yet; the page says which. Nothing about you is needed to fetch one.

**A figure is withheld.** The app shows a reason instead of a number when the evidence cannot support it, for example schedule-relative observations before you confirm your working hours in the header.

## Reporting

Open a [bug report](https://github.com/TeamLandiLTD/practicegraph-app/issues/new?template=bug_report.yml) with the version, operating system, what you expected, what happened and the `doctor` output. Never attach session logs, tokens, databases, prompts or private paths. Report suspected vulnerabilities privately as described in [SECURITY.md](../SECURITY.md).
