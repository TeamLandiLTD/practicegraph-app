#!/usr/bin/env bash
#
# build_macos.sh — assemble PracticeGraph.app (+ .dmg) for macOS.
#
# The macOS analogue of packaging/build_workstation.ps1. It produces, under
# dist/macos/:
#   1. PracticeGraph.app   the shipping bundle: Swift shell + compiled engine
#                          + webui/ + icon. The engine is frozen for delivery;
#                          readable source is permitted under Apache-2.0.
#   2. PracticeGraph-<v>.dmg  a signed, notarizable disk image.
#
# MUST run on macOS with Xcode command-line tools (swiftc, xcodebuild),
# Python 3.14.7, and — for a shipping build — a Developer ID Application
# certificate in the login keychain plus a notarytool credential profile.
#
# This file is authored on Windows and is intended to be run on a Mac (or a
# macOS CI runner). It is deliberately conservative: every external step is
# guarded, and signing/notarization are opt-in flags so a plain `./build_macos.sh`
# yields a runnable (unsigned, dev) .app for local testing.
#
# Usage:
#   ./build_macos.sh                         # dev build: ad-hoc signed .app, no dmg
#   ./build_macos.sh --dmg                   # also build an (ad-hoc signed) .dmg
#   ./build_macos.sh --sign "Developer ID Application: TeamLandi (TEAMID)" \
#                    --notarize-profile pg-notary --dmg
#   ./build_macos.sh --sign "Developer ID Application: …" \
#                    --installer-sign "Developer ID Installer: …" \
#                    --notarize-profile pg-notary --dmg --pkg
#
# Flags:
#   --sign IDENTITY          codesign the app + dmg with this identity (hardened runtime)
#   --notarize-profile NAME  submit the dmg to Apple notarization via this
#                            `xcrun notarytool store-credentials` profile, then staple
#   --dmg                    also produce a .dmg
#   --pkg                    also produce the installer wizard (.pkg, packaging/macos/pkg)
#   --installer-sign ID      sign the .pkg with this Developer ID Installer identity
#   --engine-tool TOOL       pyinstaller (default) | nuitka
#   --skip-engine            reuse an already-built engine under build/macos/engine
#   --config CONFIG          swift build config: release (default) | debug
#
set -euo pipefail

# --- Locate the repo (this script lives at packaging/macos/) -----------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# --- Preflight ---------------------------------------------------------------
# This script is authored on Windows and run here. Failing halfway through with
# a cryptic toolchain error on a machine the author cannot see is the worst
# outcome, so everything it needs is checked first, by name, with the fix.
preflight() {
    local missing=0
    [[ "$(uname -s)" == "Darwin" ]] || {
        echo "error: this builds a macOS app and must run on macOS." >&2; exit 1; }
    for tool in swift python3 npm; do
        command -v "$tool" >/dev/null 2>&1 || {
            echo "error: '$tool' not found on PATH." >&2; missing=1; }
    done
    command -v xcrun >/dev/null 2>&1 || {
        echo "error: xcrun not found - install the Xcode command line tools:" >&2
        echo "         xcode-select --install" >&2; missing=1; }
    [[ "$missing" -eq 0 ]] || exit 1

    local arch; arch="$(uname -m)"
    echo "==> Host: macOS $(sw_vers -productVersion 2>/dev/null) on ${arch}"
    if [[ "$arch" != "arm64" ]]; then
        echo "    note: not Apple Silicon - the build will target ${arch}." >&2
    fi
    echo "    swift:  $(swift --version 2>&1 | head -1)"
    echo "    python: $(python3 --version 2>&1)"
}
preflight
python3 -c 'import sys; assert sys.version_info[:3] == (3, 14, 7), "Use Python 3.14.7 and its reviewed redistribution notice"'

# --- Defaults ---------------------------------------------------------------
SIGN_IDENTITY=""
NOTARIZE_PROFILE=""
MAKE_DMG=0
MAKE_PKG=0
INSTALLER_IDENTITY=""
ENGINE_TOOL="pyinstaller"
SKIP_ENGINE=0
SWIFT_CONFIG="release"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sign) SIGN_IDENTITY="$2"; shift 2 ;;
        --notarize-profile) NOTARIZE_PROFILE="$2"; shift 2 ;;
        --dmg) MAKE_DMG=1; shift ;;
        --pkg) MAKE_PKG=1; shift ;;
        --installer-sign) INSTALLER_IDENTITY="$2"; shift 2 ;;
        --engine-tool) ENGINE_TOOL="$2"; shift 2 ;;
        --skip-engine) SKIP_ENGINE=1; shift ;;
        --config) SWIFT_CONFIG="$2"; shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "error: build_macos.sh must run on macOS (found $(uname -s))." >&2
    echo "       Author on any OS; build on a Mac or a macOS CI runner." >&2
    exit 1
fi

if [[ -n "$NOTARIZE_PROFILE" && ( -z "$SIGN_IDENTITY" || ( "$MAKE_DMG" -ne 1 && "$MAKE_PKG" -ne 1 ) ) ]]; then
    echo "Notarization requires --sign and --dmg or --pkg." >&2; exit 2
fi
if [[ "$MAKE_PKG" -eq 1 && -n "$NOTARIZE_PROFILE" && -z "$INSTALLER_IDENTITY" ]]; then
    echo "A notarized --pkg needs --installer-sign (a Developer ID Installer identity)." >&2; exit 2
fi

VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text()
print(re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1))
PY
)"
# A monotonic-ish build number for CFBundleVersion. Falls back to VERSION when
# not in git (e.g. a source tarball).
BUILD_NUMBER="$(git rev-list --count HEAD 2>/dev/null || echo "$VERSION")"

echo "==> PracticeGraph macOS build  version=$VERSION build=$BUILD_NUMBER config=$SWIFT_CONFIG"

DIST="$REPO_ROOT/dist/macos"
BUILD="$REPO_ROOT/build/macos"
APP="$DIST/PracticeGraph.app"
ENGINE_OUT="$BUILD/engine"
rm -rf "$APP"
mkdir -p "$DIST" "$BUILD"

# ============================================================================
# 1. Build the analysis engine as a frozen Mac binary (source remains open).
#    Mirrors the Nuitka Windows engine: one command surface, the CLI +
#    `server` passthrough (packaging/engine_entry.py).
# ============================================================================
build_engine_pyinstaller() {
    echo "==> Freezing engine with PyInstaller"
    command -v pyinstaller >/dev/null 2>&1 || {
        echo "error: pyinstaller not found. In a venv: pip install pyinstaller" >&2
        echo "       (or pass --engine-tool nuitka, or --skip-engine to reuse a build)" >&2
        exit 1
    }
    rm -rf "$ENGINE_OUT"
    # Also drop PyInstaller's own caches. Its staleness check is TIMESTAMP
    # based, so anything that gives a source file an older mtime than the
    # cached bytecode makes it silently reuse the old code: a tarball that did
    # not record mtimes, a checkout that preserved them, a clock skew between
    # machines. The failure mode is the worst kind — the build reports success
    # and ships an engine several commits behind the source next to it. Freezing
    # is a minute; a stale release is not worth saving it.
    rm -rf "$BUILD/pyi-work" "$BUILD/pyi-spec"
    # --onedir (default) unpacks a private CPython + the package next to the
    # launcher; we ship that whole folder inside Contents/Resources/engine.
    # No source .py is included in importable form (PyInstaller bytecode-freezes
    # the package into base_library.zip / PYZ).
    pyinstaller \
        --noconfirm \
        --name practicegraph-engine \
        --distpath "$ENGINE_OUT" \
        --workpath "$BUILD/pyi-work" \
        --specpath "$BUILD/pyi-spec" \
        --paths "$REPO_ROOT/src" \
        --collect-submodules practicegraph \
        --collect-submodules practicegraph_server \
        --collect-all tzdata \
        --collect-submodules tzlocal \
        --collect-submodules truststore \
        --collect-all cryptography \
        --collect-submodules cffi \
        --hidden-import _cffi_backend \
        --console \
        "$REPO_ROOT/packaging/engine_entry.py"
    # Result: $ENGINE_OUT/practicegraph-engine/practicegraph-engine (+ libs).
    ENGINE_DIR="$ENGINE_OUT/practicegraph-engine"
    ENGINE_BIN="$ENGINE_DIR/practicegraph-engine"
}

build_engine_nuitka() {
    echo "==> Freezing engine with Nuitka (onefile)"
    command -v python3 >/dev/null 2>&1
    python3 -c "import nuitka" 2>/dev/null || {
        echo "error: nuitka not importable. pip install nuitka" >&2; exit 1; }
    rm -rf "$ENGINE_OUT"; mkdir -p "$ENGINE_OUT"
    python3 -m nuitka --onefile --assume-yes-for-downloads \
        --output-filename=practicegraph-engine \
        --output-dir="$ENGINE_OUT" \
        --include-package=practicegraph \
        --include-package=practicegraph_server \
        --include-package=tzdata \
        --include-package-data=tzdata \
        --include-package=tzlocal \
        --include-package=truststore \
        --include-package=cryptography \
        --include-package=cffi \
        --include-module=_cffi_backend \
        --company-name="TeamLandi" \
        --product-name="PracticeGraph engine" \
        --product-version="$VERSION" \
        "$REPO_ROOT/packaging/engine_entry.py"
    ENGINE_DIR=""  # onefile: single binary, no dir
    ENGINE_BIN="$ENGINE_OUT/practicegraph-engine"
}

if [[ "$SKIP_ENGINE" -eq 1 ]]; then
    echo "==> Reusing engine under $ENGINE_OUT"
    if [[ -x "$ENGINE_OUT/practicegraph-engine/practicegraph-engine" ]]; then
        ENGINE_DIR="$ENGINE_OUT/practicegraph-engine"
        ENGINE_BIN="$ENGINE_DIR/practicegraph-engine"
    elif [[ -x "$ENGINE_OUT/practicegraph-engine" ]]; then
        ENGINE_DIR=""
        ENGINE_BIN="$ENGINE_OUT/practicegraph-engine"
    else
        echo "error: --skip-engine set but no engine binary found under $ENGINE_OUT" >&2
        exit 1
    fi
else
    case "$ENGINE_TOOL" in
        pyinstaller) build_engine_pyinstaller ;;
        nuitka) build_engine_nuitka ;;
        *) echo "unknown --engine-tool: $ENGINE_TOOL" >&2; exit 2 ;;
    esac
fi
[[ -x "$ENGINE_BIN" ]] || { echo "error: engine binary missing at $ENGINE_BIN" >&2; exit 1; }

# Apache-2.0 source and dependency source files are permitted in the bundle.
# Freezing is a delivery format, not an intellectual-property boundary.

# ============================================================================
# 2. Build the Swift shell.
# ============================================================================
echo "==> Building Swift shell ($SWIFT_CONFIG)"
( cd "$REPO_ROOT/macos" && swift build -c "$SWIFT_CONFIG" )
SHELL_BIN="$REPO_ROOT/macos/.build/$SWIFT_CONFIG/PracticeGraphShell"
[[ -x "$SHELL_BIN" ]] || { echo "error: swift build produced no PracticeGraphShell" >&2; exit 1; }

# ============================================================================
# 3. Build the dashboard web app (webui/) if not already built.
#    Same artifact the engine's `ui serve` serves; must sit in Resources so the
#    engine's _webui_dir() finds it next to the executable.
# ============================================================================
# ALWAYS rebuild. This used to skip when webui/index.html already existed,
# which meant a checkout carrying an older committed bundle would ship that
# bundle inside the .app - the dashboard is where nearly every feature lives,
# so a stale one looks exactly like "the Mac version is missing things".
# npm ci (not install) so the build matches package-lock.json.
echo "==> Building webui (npm ci && npm run build)"
( cd "$REPO_ROOT/ui" && npm ci && npm run build )
[[ -f "$REPO_ROOT/webui/index.html" ]] || {
    echo "error: no built dashboard at webui/index.html" >&2; exit 1; }

# ============================================================================
# 4. Generate the .icns app icon from the shared brand mark.
# ============================================================================
echo "==> Rendering app icon (.icns) from the brand mark"
ICONSET="$BUILD/PracticeGraph.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
# The macOS iconset expects these named sizes (1x + 2x).
for spec in "16:16x16" "32:16x16@2x" "32:32x32" "64:32x32@2x" \
            "128:128x128" "256:128x128@2x" "256:256x256" "512:256x256@2x" \
            "512:512x512" "1024:512x512@2x"; do
    px="${spec%%:*}"; name="${spec##*:}"
    python3 "$REPO_ROOT/packaging/make_icon.py" --png "$px" "$ICONSET/icon_${name}.png"
done
iconutil -c icns "$ICONSET" -o "$BUILD/PracticeGraph.icns"

# ============================================================================
# 5. Assemble PracticeGraph.app.
# ============================================================================
echo "==> Assembling $APP"
CONTENTS="$APP/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RES="$CONTENTS/Resources"
mkdir -p "$MACOS_DIR" "$RES"
cp "$REPO_ROOT/LICENSE" "$REPO_ROOT/NOTICE" "$RES/"
python3 "$REPO_ROOT/packaging/third_party_notices.py" --platform macos \
    --out "$RES/THIRD_PARTY_NOTICES.txt"

# 5a. Executable.
cp "$SHELL_BIN" "$MACOS_DIR/PracticeGraphShell"
chmod +x "$MACOS_DIR/PracticeGraphShell"

# 5b. Info.plist with substituted version/build.
sed -e "s/@VERSION@/$VERSION/g" -e "s/@BUILD@/$BUILD_NUMBER/g" \
    "$REPO_ROOT/macos/Resources/Info.plist" > "$CONTENTS/Info.plist"

# 5c. Icon.
cp "$BUILD/PracticeGraph.icns" "$RES/PracticeGraph.icns"

# 5d. Engine. EngineRunner looks for `practicegraph-engine` directly inside
#     Resources; place the launcher there and its support libs alongside.
if [[ -n "${ENGINE_DIR:-}" ]]; then
    # onedir: copy the whole engine folder, then symlink the launcher up to
    # Resources/practicegraph-engine so EngineRunner.resourcesDir finds it.
    cp -R "$ENGINE_DIR" "$RES/engine"
    ln -sf "engine/practicegraph-engine" "$RES/practicegraph-engine"
else
    # onefile: a single self-contained binary.
    cp "$ENGINE_BIN" "$RES/practicegraph-engine"
    chmod +x "$RES/practicegraph-engine"
fi

# 5e. Dashboard web app. The engine's _webui_dir() looks for `webui/` next to
#     sys.executable (and next to __file__ / sys.argv[0]). For a PyInstaller
#     onedir engine, sys.executable is the REAL launcher path inside
#     Resources/engine/ — a symlink at Resources/practicegraph-engine does not
#     change what sys.executable resolves to — so webui must sit beside the
#     real launcher, in Resources/engine/. For a onefile engine the exe lives
#     directly in Resources/, so webui goes there. Ship it in the right spot
#     for the chosen layout (and, cheaply, in Resources/ too so the top-level
#     bundle is self-describing).
# Always ship webui at Resources/webui so the bundle is self-describing and the
# verify (and any tooling) can find it in one known place. For the PyInstaller
# onedir engine, also place it beside the REAL launcher in Resources/engine/,
# because that is where sys.executable resolves and thus where _webui_dir() looks.
cp -R "$REPO_ROOT/webui" "$RES/webui"
if [[ -n "${ENGINE_DIR:-}" ]]; then
    cp -R "$REPO_ROOT/webui" "$RES/engine/webui"
fi

# 5f. A stamp for provenance/support (analogue of the Windows BUILD-INFO).
cat > "$RES/BUILD-INFO.txt" <<EOF
PracticeGraph.app
version: $VERSION
build:   $BUILD_NUMBER
engine:  $ENGINE_TOOL (frozen Apache-2.0 software)
built:   $(date -u '+%Y-%m-%dT%H:%M:%SZ') on $(sw_vers -productName) $(sw_vers -productVersion)
git:     $(git rev-parse HEAD 2>/dev/null || echo 'n/a')
EOF

python3 "$REPO_ROOT/packaging/desktop_provenance.py" "$RES" --platform "macos-$(uname -m)"
SHELL_ARCH="$(lipo -archs "$MACOS_DIR/PracticeGraphShell")"
ENGINE_ARCH="$(lipo -archs "$ENGINE_BIN")"
[[ "$SHELL_ARCH" == "$ENGINE_ARCH" ]] || {
    echo "Shell/engine architecture mismatch: $SHELL_ARCH / $ENGINE_ARCH" >&2; exit 1; }
echo "    app assembled: $SHELL_ARCH"

# ============================================================================
# 6. Codesign (hardened runtime) — inside-out: engine first, then the app.
# ============================================================================
if [[ -n "$SIGN_IDENTITY" ]]; then
    echo "==> Codesigning with: $SIGN_IDENTITY"
    ENT="$REPO_ROOT/macos/Resources/PracticeGraph.entitlements"

    # Sign every Mach-O in the engine payload first (nested code must be signed
    # before the outer bundle). --options runtime everywhere for notarization.
    if [[ -n "${ENGINE_DIR:-}" ]]; then
        find "$RES/engine" -type f -print0 \
            | while IFS= read -r -d '' lib; do
                file -b "$lib" | grep -q "Mach-O" || continue
                codesign --force --timestamp --options runtime \
                    --sign "$SIGN_IDENTITY" "$lib"
            done
        codesign --force --timestamp --options runtime \
            --entitlements "$ENT" --sign "$SIGN_IDENTITY" \
            "$RES/engine/practicegraph-engine"
    else
        codesign --force --timestamp --options runtime \
            --entitlements "$ENT" --sign "$SIGN_IDENTITY" \
            "$RES/practicegraph-engine"
    fi

    # Finally the app bundle itself (the shell inherits the app's entitlements).
    codesign --force --timestamp --options runtime \
        --sign "$SIGN_IDENTITY" "$APP"

    echo "==> Verifying signature"
    codesign --verify --deep --strict --verbose=2 "$APP"
    # A signed-but-not-yet-notarized app fails the notarized-source assessment;
    # that is expected until step 7 staples the ticket.
    spctl -a -vvv "$APP" || echo "    (spctl: not notarized yet — expected pre-notarization)"
else
    # No Developer ID: ad-hoc sign rather than shipping an unsigned bundle.
    #
    # This is not cosmetic. Apple Silicon refuses to execute unsigned code, so
    # an unsigned build is not a "dev build" there — it is a bundle that cannot
    # launch, including an x86_64 one under Rosetta. An ad-hoc signature costs
    # nothing, has no identity behind it, and makes the artifact runnable.
    # Gatekeeper still warns on first open; that is a separate gate and is
    # expected until there is a real Developer ID.
    echo "==> No --sign identity: ad-hoc signing so the bundle can launch"
    codesign --force --deep --sign - "$APP"
    codesign --verify --deep --strict "$APP" \
        && echo "    ad-hoc signature valid (Gatekeeper will still warn on first open)"
fi

# ============================================================================
# 7. Package a .dmg and (optionally) notarize + staple.
# ============================================================================
if [[ "$MAKE_DMG" -eq 1 ]]; then
    DMG="$DIST/PracticeGraph-$VERSION-macos-$SHELL_ARCH.dmg"
    echo "==> Building $DMG"
    rm -f "$DMG"
    STAGE="$BUILD/dmg-stage"
    rm -rf "$STAGE"; mkdir -p "$STAGE"
    cp -R "$APP" "$STAGE/"
    ln -s /Applications "$STAGE/Applications"   # drag-to-install affordance
    hdiutil create -volname "PracticeGraph" -srcfolder "$STAGE" \
        -ov -format UDZO "$DMG"

    if [[ -n "$SIGN_IDENTITY" ]]; then
        codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
    fi

    if [[ -n "$NOTARIZE_PROFILE" ]]; then
        echo "==> Submitting to Apple notarization (profile: $NOTARIZE_PROFILE)"
        xcrun notarytool submit "$DMG" \
            --keychain-profile "$NOTARIZE_PROFILE" --wait
        echo "==> Stapling the notarization ticket"
        xcrun stapler staple "$DMG"
        # Staple the app too so a copy dragged out of the dmg stays notarized.
        xcrun stapler staple "$APP"
        echo "==> Final Gatekeeper assessment"
        xcrun stapler validate "$DMG"
        spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
    else
        echo "    (dmg not notarized — pass --notarize-profile to submit)"
    fi
    echo "    dmg ready: $DMG"
fi

# ============================================================================
# 8. The installer wizard (.pkg): Welcome -> Licence -> Install -> Done, with
#    "Background refresh" (the per-user LaunchAgent) as a Customize choice.
#    Two component packages under one distribution (packaging/macos/pkg):
#    the app, whose preinstall stops a running copy and whose postinstall
#    opens it, and a payload-free one that installs the LaunchAgent.
# ============================================================================
if [[ "$MAKE_PKG" -eq 1 ]]; then
    PKG="$DIST/PracticeGraph-$VERSION-macos-$SHELL_ARCH.pkg"
    PKGSRC="$REPO_ROOT/packaging/macos/pkg"
    PKGWORK="$BUILD/pkg"
    echo "==> Building $PKG"
    rm -rf "$PKGWORK"; mkdir -p "$PKGWORK/root/Applications" "$PKGWORK/resources" "$PKGWORK/scripts-agent"
    rm -f "$PKG"
    ditto "$APP" "$PKGWORK/root/Applications/PracticeGraph.app"
    cp -R "$PKGSRC/scripts-app" "$PKGWORK/scripts-app"
    # Drop removable checkout metadata (e.g. a download's quarantine flag) from
    # the scripts before they are archived. macOS keeps com.apple.provenance on
    # every file, so a harmless ._ entry still rides along for it. The app
    # bundle itself is left exactly as signed.
    xattr -cr "$PKGWORK/scripts-app"
    # Never relocatable: otherwise Installer "upgrades" whatever copy with this
    # bundle id it finds on disk (a build folder, a copy in Downloads) instead
    # of installing into /Applications.
    pkgbuild --analyze --root "$PKGWORK/root" "$PKGWORK/component.plist"
    plutil -replace 0.BundleIsRelocatable -bool NO "$PKGWORK/component.plist"
    pkgbuild --root "$PKGWORK/root" --install-location / \
        --component-plist "$PKGWORK/component.plist" \
        --scripts "$PKGWORK/scripts-app" \
        --identifier dev.practicegraph.app --version "$VERSION" \
        "$PKGWORK/app.pkg"
    cp "$PKGSRC/scripts-agent/postinstall" "$PKGWORK/scripts-agent/"
    cp "$REPO_ROOT/packaging/macos/dev.practicegraph.agent.plist" "$PKGWORK/scripts-agent/"
    xattr -cr "$PKGWORK/scripts-agent"
    pkgbuild --nopayload --scripts "$PKGWORK/scripts-agent" \
        --identifier dev.practicegraph.agent --version "$VERSION" \
        "$PKGWORK/agent.pkg"
    cp "$PKGSRC/resources/"*.html "$PKGWORK/resources/"
    cp "$REPO_ROOT/LICENSE" "$PKGWORK/resources/LICENSE.txt"
    sed "s/@VERSION@/$VERSION/g" "$PKGSRC/distribution.xml" > "$PKGWORK/distribution.xml"
    if [[ -n "$INSTALLER_IDENTITY" ]]; then
        productbuild --distribution "$PKGWORK/distribution.xml" \
            --resources "$PKGWORK/resources" --package-path "$PKGWORK" \
            --sign "$INSTALLER_IDENTITY" --timestamp "$PKG"
    else
        productbuild --distribution "$PKGWORK/distribution.xml" \
            --resources "$PKGWORK/resources" --package-path "$PKGWORK" "$PKG"
        echo "    (pkg unsigned — pass --installer-sign to sign it)"
    fi

    if [[ -n "$NOTARIZE_PROFILE" ]]; then
        echo "==> Submitting the installer to Apple notarization"
        xcrun notarytool submit "$PKG" --keychain-profile "$NOTARIZE_PROFILE" --wait
        xcrun stapler staple "$PKG"
        xcrun stapler validate "$PKG"
        spctl --assess --type install --verbose=2 "$PKG"
    fi
    ( cd "$DIST" && shasum -a 256 "$(basename "$PKG")" > "$(basename "$PKG").sha256" )
    echo "    pkg ready: $PKG"
fi

echo ""
echo "Done."
echo "  app: $APP"
echo "  arch: shell=${SHELL_ARCH}  engine=${ENGINE_ARCH}"
if [[ "$MAKE_DMG" -eq 1 ]]; then
    # Bare filename, so `shasum -c` works wherever the dmg is downloaded to.
    ( cd "$DIST" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256" )
    echo "  dmg: $DMG"
fi
if [[ "$MAKE_PKG" -eq 1 ]]; then
    echo "  pkg: $PKG"
fi
if [[ -z "$SIGN_IDENTITY" ]]; then
    echo "Development candidate: ad-hoc signed, not notarized."
    echo "Public macOS distribution requires Developer ID signing and notarization."
fi
