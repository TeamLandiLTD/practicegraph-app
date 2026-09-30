# Linux desktop candidate

The Linux shell uses GTK 3 and WebKitGTK 4.1 to open the same dashboard as
Windows and macOS in a native window. The package carries the Python analysis
engine; users do not need pip or a Python development environment. GTK and
WebKit come from the distribution and receive its security updates.

The initial build/test target is Ubuntu 24.04 amd64. The script can also build
an arm64 package on an arm64 host, but that architecture is not part of the
current CI evidence. Do not advertise broader distribution compatibility or an
arm64 download until it has been tested. There is no AppImage or RPM yet.

## Build on Ubuntu 24.04

Use Python 3.14.7 and Node 22. Build natively for the target architecture.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]' -r packaging/posix-build-requirements.txt
bash packaging/linux/build_linux.sh
```

The output in `dist/linux/` includes a `.deb`, a tar archive, and SHA-256
checksums. The engine, shared UI, LICENSE, NOTICE, third-party notices and a
file-level provenance record are included. The tar archive needs the same
system GTK/WebKit dependencies as the Debian package; it is not a universal
portable binary. Build on the documented baseline to avoid requiring a newer
glibc than the package declares.

## Install, run, remove

For a reviewed candidate built above:

```bash
sudo apt install ./dist/linux/practicegraph_0.2.0_amd64.deb
practicegraph-desktop
```

The app also appears in the desktop application launcher. `practicegraph:open`
and `practicegraph:break` are registered as closed launch actions. No background
service, autostart entry, or administrator-level collector is installed. The
window is a normal desktop application; this first Linux shell has no tray
integration. The local dashboard engine remains available after window close.

```bash
sudo apt remove practicegraph
```

Removal does not delete personal records. They are stored under
`$XDG_DATA_HOME/practicegraph`, normally `~/.local/share/practicegraph`;
`PRACTICEGRAPH_DATA_DIR` is an explicit override. Export voluntary practice and
learning records from their pages before deliberately removing personal data.

For the tar archive, extract it under a directory you own, install `python3-gi`,
`gir1.2-gtk-3.0`, and `gir1.2-webkit2-4.1` through your package manager, then run
`./practicegraph/practicegraph`. It does not register a menu entry or protocol.

## Verification

```bash
python packaging/smoke_desktop.py --engine /opt/practicegraph/engine/practicegraph-engine
dbus-run-session -- xvfb-run -a /usr/bin/python3 packaging/linux/smoke_gtk.py \
  /opt/practicegraph/practicegraph_desktop.py
```

The first check uses disposable synthetic records with hosted catalogs disabled.
It verifies collection, served UI, record APIs and authentication. The second
starts the actual GTK shell and checks that Usage and the direct category menu render.
CI installs and removes the Debian candidate on a disposable runner.

Before public distribution, also test an ordinary GNOME session and a KDE
session: launch from the menu, keyboard navigation, external article links,
JSON export/restore through the native dialogs, close/reopen, upgrade, and
removal while preserving records. Automated rendering does not replace those
desktop integration checks.

The current signed-update manifest has no Linux entry. Linux candidates are
manually downloaded and installed; the app does not install updates itself.
No download should be promoted before its artifact and platform checks pass.

The native navigation uses [WebKitGTK policy decisions](https://www.webkitgtk.org/reference/webkit2gtk/stable/signal.WebView.decide-policy.html)
to keep the dashboard on its exact loopback origin. External HTTPS links open
in the user's browser. Export dialogs accept only local dashboard downloads.
