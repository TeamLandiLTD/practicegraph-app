# Windows packaging

Run `packaging/build_workstation.ps1 -Msi` to build a per-user Windows MSI and
staged application. It requires the repository Python development environment,
the built frontend, Rust stable GNU, full MinGW binutils/GCC on PATH, and WiX 5.0.2.

The MSI installs a personal tray app, Start menu shortcut, and protocol handler
under the user's profile. The tray schedules collection. It does not install a
LocalSystem collector. Machine-wide builds and shared in-place refits are retired.
Provision a separate fleet credential per contributor through protected endpoint
configuration; never use one shared token to represent multiple contributors.

Python 3.14.7 and shipped wheel bytes are hash-verified. The default bundle
contains readable Apache-2.0 Python source. Optional engine compilation is a
delivery format and does not change source availability or licensing.

The build retains application and dependency notices, validates MSI tables,
extracts and compares every payload file, tests the shipped per-user runtime
against synthetic logs, and generates an SBOM from the actual installer.
`BUILD-INFO.txt` records source commit, dirty state, runtime and UI identity.

Unsigned local candidates are for validation. Actual installation, upgrade,
uninstall, native-window and signing checks must run in a disposable test
environment before public release. See [the release guide](../docs/RELEASING.md).
