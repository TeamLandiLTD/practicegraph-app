"""The macOS installer wizard (packaging/macos/pkg): what it shows, and that
its scripts touch only PracticeGraph and never fail an install."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging" / "macos" / "pkg"
SCRIPTS = [PKG / "scripts-app" / "preinstall", PKG / "scripts-app" / "postinstall",
           PKG / "scripts-agent" / "postinstall"]
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="installer scripts are macOS shell")


def test_the_wizard_offers_the_app_fixed_and_background_refresh_optional() -> None:
    doc = ET.parse(PKG / "distribution.xml").getroot()
    assert doc.find("welcome").get("file") == "welcome.html"
    assert doc.find("license").get("file") == "LICENSE.txt"
    assert doc.find("conclusion").get("file") == "conclusion.html"
    assert doc.find("options").get("hostArchitectures") == "arm64"
    choices = {c.get("id"): c for c in doc.findall("choice")}
    assert choices["app"].get("enabled") == "false" and choices["app"].get("selected") == "true"
    assert choices["agent"].get("start_selected") == "true"
    refs = {r.get("id"): (r.text or "").strip() for r in doc.findall("pkg-ref") if r.text}
    assert refs == {"dev.practicegraph.app": "app.pkg", "dev.practicegraph.agent": "agent.pkg"}


def test_the_wizard_minimum_matches_the_app() -> None:
    doc = ET.parse(PKG / "distribution.xml").getroot()
    minimum = doc.find("volume-check/allowed-os-versions/os-version").get("min")
    plist = (ROOT / "macos" / "Resources" / "Info.plist").read_text(encoding="utf-8")
    assert f"<key>LSMinimumSystemVersion</key>\n    <string>{minimum}</string>" in plist


def test_the_build_installs_into_applications_only() -> None:
    """A relocatable component would "upgrade" any copy with the bundle id it
    finds on disk (a build folder, Downloads) instead of /Applications."""
    build = (ROOT / "packaging" / "macos" / "build_macos.sh").read_text(encoding="utf-8")
    assert "plutil -replace 0.BundleIsRelocatable -bool NO" in build
    assert "--identifier dev.practicegraph.app" in build
    assert "--identifier dev.practicegraph.agent" in build
    for page in ("welcome.html", "conclusion.html"):
        assert (PKG / "resources" / page).is_file()


def test_scripts_are_executable_and_never_fail_an_install() -> None:
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/bin/bash\n"), script
        assert "set -u" in text and "set -e" not in text, script
        assert text.rstrip().endswith("exit 0"), script
        if sys.platform != "win32":
            assert script.stat().st_mode & stat.S_IXUSR, script


def test_background_refresh_writes_the_home_folder_as_the_user() -> None:
    text = (PKG / "scripts-agent" / "postinstall").read_text(encoding="utf-8")
    assert 'as_user() { /usr/bin/sudo -u "$user" "$@"; }' in text
    assert "as_user /usr/bin/plutil -replace ProgramArguments.0" in text
    assert 'as_user /bin/mv -f "$tmp" "$dest"' in text
    assert '/bin/launchctl bootstrap "gui/$uid" "$dest"' in text


@POSIX
def test_background_refresh_skips_cleanly_without_an_engine(tmp_path: Path) -> None:
    result = subprocess.run(
        ["/bin/bash", str(PKG / "scripts-agent" / "postinstall")],
        env={**os.environ, "PRACTICEGRAPH_INSTALL_APP": str(tmp_path / "PracticeGraph.app")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "engine not found" in result.stdout


@POSIX
def test_opening_is_skipped_for_command_line_installs(tmp_path: Path) -> None:
    result = subprocess.run(
        ["/bin/bash", str(PKG / "scripts-app" / "postinstall")],
        env={**os.environ, "COMMAND_LINE_INSTALL": "1",
             "PRACTICEGRAPH_INSTALL_APP": str(tmp_path / "PracticeGraph.app")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "not opening" in result.stdout


@POSIX
@pytest.mark.skipif(shutil.which("pgrep") is None, reason="needs pgrep")
def test_preinstall_stops_only_processes_running_from_the_app(tmp_path: Path) -> None:
    """A process whose command line starts inside the app bundle is stopped;
    one from a look-alike path survives ("Prxapp" would match an unescaped
    "Pr.app"). The processes are Python with argv[0] set to those paths — the
    command line pgrep sees — because macOS kills a copied system binary."""
    app = tmp_path / "Pr.app"
    inside = app / "Contents" / "MacOS" / "PracticeGraphShell"
    lookalike = tmp_path / "Prxapp" / "Contents" / "MacOS" / "PracticeGraphShell"
    nap = ["-c", "import time; time.sleep(60)"]
    ours = subprocess.Popen([str(inside), *nap], executable=sys.executable)
    other = subprocess.Popen([str(lookalike), *nap], executable=sys.executable)
    try:
        time.sleep(0.5)
        assert ours.poll() is None and other.poll() is None
        result = subprocess.run(
            ["/bin/bash", str(PKG / "scripts-app" / "preinstall")],
            env={**os.environ, "PRACTICEGRAPH_INSTALL_APP": str(app)},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert ours.wait(timeout=10) is not None
        assert other.poll() is None
        assert "stopped" in result.stdout
    finally:
        for proc in (ours, other):
            if proc.poll() is None:
                proc.kill()


@POSIX
def test_preinstall_with_nothing_running_is_a_no_op(tmp_path: Path) -> None:
    result = subprocess.run(
        ["/bin/bash", str(PKG / "scripts-app" / "preinstall")],
        env={**os.environ, "PRACTICEGRAPH_INSTALL_APP": str(tmp_path / "PracticeGraph.app")},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "no running copy" in result.stdout
