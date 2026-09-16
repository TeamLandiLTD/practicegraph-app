#!/usr/bin/env bash
#
# install-agent.sh — install (or remove) the PracticeGraph background-tick
# LaunchAgent for the current user.
#
# This is the macOS analogue of the Windows service registration: it schedules
# the frozen engine's `agent run --once` every 15 minutes via launchd so the
# dashboard's data stays fresh without the UI app having to be open.
#
# The UI app (PracticeGraph.app) does NOT need this to show data — opening the
# dashboard runs `ui serve`, which ingests on demand. The LaunchAgent exists so
# emits/alerts/refreshes happen on a cadence in the background, exactly like the
# Windows service.
#
# Usage:
#   ./install-agent.sh [--app /Applications/PracticeGraph.app]   # install/refresh
#   ./install-agent.sh --uninstall
#
set -euo pipefail
[[ "$(uname -s)" == Darwin ]] || { echo "Run this on macOS." >&2; exit 1; }

APP="/Applications/PracticeGraph.app"
UNINSTALL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

LABEL="dev.practicegraph.agent"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$AGENTS_DIR/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ "$UNINSTALL" -eq 1 ]]; then
    echo "==> Removing $LABEL"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "    done."
    exit 0
fi

ENGINE="$APP/Contents/Resources/practicegraph-engine"
if [[ ! -x "$ENGINE" ]]; then
    echo "error: engine not found/executable at:" >&2
    echo "       $ENGINE" >&2
    echo "       Pass --app <path to PracticeGraph.app> if it is not in /Applications." >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/$LABEL.plist"
[[ -f "$TEMPLATE" ]] || { echo "error: template plist missing: $TEMPLATE" >&2; exit 1; }

mkdir -p "$AGENTS_DIR"
echo "==> Installing LaunchAgent -> $PLIST_DEST"
# Serialize XML safely even when the app path contains '&', '<' or '#'.
python3 - "$TEMPLATE" "$ENGINE" "$PLIST_DEST" <<'PY'
import os, pathlib, plistlib, sys, tempfile
template, engine, destination = map(pathlib.Path, sys.argv[1:])
with template.open('rb') as source:
    document = plistlib.load(source)
document['ProgramArguments'][0] = str(engine.resolve(strict=True))
fd, temporary = tempfile.mkstemp(prefix='.practicegraph-', dir=destination.parent)
try:
    with os.fdopen(fd, 'wb') as output:
        plistlib.dump(document, output)
    os.replace(temporary, destination)
finally:
    pathlib.Path(temporary).unlink(missing_ok=True)
PY
plutil -lint "$PLIST_DEST"

# Reload cleanly: bootout any prior instance, then bootstrap the new one.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST_DEST"
launchctl enable "$DOMAIN/$LABEL"

echo "    installed and loaded. It will tick every 15 minutes (and once now)."
echo "    Verify with:  launchctl print $DOMAIN/$LABEL | head"
