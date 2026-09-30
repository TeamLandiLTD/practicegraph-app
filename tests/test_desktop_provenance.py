"""Desktop provenance: honest source identity inside and outside a git checkout."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "pg_provenance", Path("packaging/desktop_provenance.py")
)
assert spec and spec.loader
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "Resources"
    bundle.mkdir()
    (bundle / "engine.bin").write_bytes(b"engine")
    (bundle / "BUILD-INFO.json").write_text("{}")
    return bundle


def test_source_snapshot_records_unknown_identity(tmp_path: Path) -> None:
    source = tmp_path / "snapshot"
    source.mkdir()
    rec = provenance.record(_bundle(tmp_path), "macos-arm64", root=source)
    assert rec["source_commit"] is None
    assert rec["tracked_source_dirty"] is None
    # BUILD-INFO.json is the record itself, so it is never hashed into it.
    assert rec["files_sha256"] == {"engine.bin": hashlib.sha256(b"engine").hexdigest()}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_checkout_records_commit(tmp_path: Path) -> None:
    source = tmp_path / "checkout"
    source.mkdir()
    git = ["git", "-C", str(source), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    (source / "f.txt").write_text("x")
    subprocess.run([*git, "add", "f.txt"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], check=True)
    rec = provenance.record(_bundle(tmp_path), "linux-x86_64", root=source)
    assert isinstance(rec["source_commit"], str) and len(rec["source_commit"]) == 40
    assert rec["tracked_source_dirty"] is False
