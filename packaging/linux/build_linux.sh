#!/usr/bin/env bash
# Build on Ubuntu 24.04 for its glibc floor; never cross-freeze a Python engine.
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'Build this package on Linux.' >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
for tool in python3 npm dpkg-deb; do command -v "$tool" >/dev/null || exit 1; done
python3 -c 'import sys; assert sys.version_info[:3] == (3, 14, 7), "Use the reviewed Python 3.14.7 build runtime"'
VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in amd64|arm64) ;; *) echo 'Supported package architectures: amd64, arm64.' >&2; exit 1 ;; esac
BUILD="$REPO_ROOT/build/linux-$ARCH"
DIST="$REPO_ROOT/dist/linux"
PACKAGE="$BUILD/package"
# All deletion targets are fixed descendants of this repository's build directory.
rm -rf "$BUILD"
mkdir -p "$BUILD" "$DIST"
(cd ui && npm ci && npm run build)
python3 -m PyInstaller --clean --noconfirm --name practicegraph-engine \
    --distpath "$BUILD/frozen" --workpath "$BUILD/pyi-work" --specpath "$BUILD/pyi-spec" \
    --paths "$REPO_ROOT/src" --collect-submodules practicegraph --collect-submodules practicegraph_server \
    --collect-all tzdata --collect-submodules tzlocal --collect-all cryptography \
    --collect-submodules cffi --hidden-import _cffi_backend "$REPO_ROOT/packaging/engine_entry.py"
APP="$PACKAGE/opt/practicegraph"
mkdir -p "$APP" "$PACKAGE/usr/bin" "$PACKAGE/usr/share/applications" \
    "$PACKAGE/usr/share/icons/hicolor/256x256/apps" "$PACKAGE/DEBIAN"
cp -R "$BUILD/frozen/practicegraph-engine" "$APP/engine"
cp -R webui "$APP/engine/webui"
cp linux/practicegraph_desktop.py packaging/linux/practicegraph LICENSE NOTICE "$APP/"
chmod 755 "$APP/practicegraph" "$APP/practicegraph_desktop.py"
ln -s /opt/practicegraph/practicegraph "$PACKAGE/usr/bin/practicegraph-desktop"
cp packaging/linux/practicegraph.desktop "$PACKAGE/usr/share/applications/"
python3 packaging/make_icon.py --png 256 "$PACKAGE/usr/share/icons/hicolor/256x256/apps/practicegraph.png"
python3 packaging/third_party_notices.py --platform linux --out "$APP/THIRD_PARTY_NOTICES.txt"
python3 packaging/desktop_provenance.py "$APP" --platform "linux-$ARCH"
cat > "$PACKAGE/DEBIAN/control" <<EOF
Package: practicegraph
Version: $VERSION
Architecture: $ARCH
Maintainer: TeamLandi Ltd <hello@practicegraph.dev>
Section: devel
Priority: optional
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, libc6 (>= 2.39)
Homepage: https://practicegraph.dev
Description: A private daily reading for work with AI
 Local observations, voluntary practice, curated editions and usage estimates.
 Includes the analysis engine and shared dashboard. No account is required.
EOF
dpkg-deb --root-owner-group --build "$PACKAGE" "$DIST/practicegraph_${VERSION}_${ARCH}.deb"
tar -C "$PACKAGE/opt" -czf "$DIST/PracticeGraph-${VERSION}-linux-${ARCH}.tar.gz" practicegraph
(cd "$DIST" && sha256sum "practicegraph_${VERSION}_${ARCH}.deb" \
    "PracticeGraph-${VERSION}-linux-${ARCH}.tar.gz" > "SHA256SUMS-${ARCH}.txt")
echo "Candidate packages: $DIST (native GTK smoke and installation review still required)"
