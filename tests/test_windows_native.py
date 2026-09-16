"""Windows-native runtime seams: DPAPI token protection (NFR-SEC-1), MSI
registry bootstrap (FR-DEP-2), and service-context profile discovery
(FR-SRC-2). Windows-only pieces are skipped elsewhere — the core stays
OS-portable (NFR-CMP-1)."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from practicegraph import winsec
from practicegraph.cli import main
from practicegraph.config import (
    BOOTSTRAP_REG_PATH,
    ORG_TOKEN_FILE_NAME,
    import_bootstrap,
    load_org_token,
    resolve,
    resolve_org_token,
    store_org_token,
)
from practicegraph.sources.claude_code import ADAPTER as CLAUDE_ADAPTER
from practicegraph.sources.codex import ADAPTER as CODEX_ADAPTER

IS_WINDOWS = os.name == "nt"


def test_timezone_runtime_is_pinned_and_packaged() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    windows = Path("packaging/build_workstation.ps1").read_text(encoding="utf-8")
    macos = Path("packaging/macos/build_macos.sh").read_text(encoding="utf-8")

    assert '"tzdata==2026.2"' in pyproject
    assert '"tzlocal==5.4.4"' in pyproject
    assert "--include-package=tzdata" in windows
    assert "--include-package=tzlocal" in windows
    # The Windows bundle installs them --require-hashes from a lockfile
    # (2026-07-26): a version pin still trusts whatever PyPI serves under that
    # version today, and these two packages run inside a background agent that
    # reads someone's logs. A substituted wheel now fails the install.
    assert "pip install --no-compile --no-deps --require-hashes" in windows
    assert "shipped-requirements.txt" in windows
    pins = Path("packaging/shipped-requirements.txt").read_text(encoding="utf-8")
    assert "tzdata==2026.2" in pins and "tzlocal==5.4.4" in pins
    assert pins.count("--hash=sha256:") == 5
    assert "cryptography==50.0.1" in pins and "cffi==2.1.0" in pins and "pycparser==3.0" in pins
    assert "--platform win_amd64 --implementation cp --python-version $PythonVersion" in windows
    assert "--collect-all cryptography" in macos
    assert "--collect-all tzdata" in macos
    assert "--collect-submodules tzlocal" in macos
    assert "--include-package=tzdata" in macos
    assert "--include-package=tzlocal" in macos


def test_timezone_runtime_imports() -> None:
    import tzdata
    import tzlocal
    from tzlocal.windows_tz import win_tz

    assert tzdata.__version__ == "2026.2"
    assert callable(tzlocal.get_localzone_name)
    assert win_tz["Pacific Standard Time"] == "America/Los_Angeles"
    assert win_tz["GMT Standard Time"] == "Europe/London"
    assert win_tz["UTC"] == "Etc/UTC"


@pytest.mark.skipif(not IS_WINDOWS, reason="DPAPI is Windows-only")
def test_dpapi_roundtrip_and_ciphertext() -> None:
    secret = b"tok-super-secret-123"
    protected = winsec.protect(secret)
    assert protected != secret
    assert secret not in protected
    assert winsec.unprotect(protected) == secret


def test_token_store_roundtrip_and_precedence(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store_org_token(data_dir, "tok-protected")
    assert load_org_token(data_dir) == "tok-protected"

    env = {"PRACTICEGRAPH_DATA_DIR": str(data_dir)}
    config = resolve(env)
    assert config.org_token_present
    assert config.org_token_source == "protected"
    assert resolve_org_token(env, config) == "tok-protected"

    # Env still wins over the protected store (FR-CFG-1).
    env["PRACTICEGRAPH_ORG_TOKEN"] = "tok-from-env"
    config = resolve(env)
    assert config.org_token_source == "env"
    assert resolve_org_token(env, config) == "tok-from-env"


@pytest.mark.skipif(not IS_WINDOWS, reason="DPAPI is Windows-only")
def test_token_never_plaintext_at_rest(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store_org_token(data_dir, "tok-super-secret-123")
    raw = (data_dir / ORG_TOKEN_FILE_NAME).read_bytes()
    assert b"tok-super-secret-123" not in raw


@pytest.mark.skipif(not IS_WINDOWS, reason="file ACLs are Windows-only")
def test_org_token_file_is_acl_locked_to_owner(tmp_path: Path) -> None:
    """Machine-scope DPAPI can be decrypted by ANY local process, so in a shared
    data dir (%ProgramData%) the token file must also be ACL-locked: only
    SYSTEM / Administrators / the owner may read it, never BUILTIN\\Users."""
    import subprocess

    data_dir = tmp_path / "data"
    store_org_token(data_dir, "tok-secret")
    acl = subprocess.run(
        ["icacls", str(data_dir / ORG_TOKEN_FILE_NAME)],
        capture_output=True,
        text=True,
    ).stdout
    assert "NT AUTHORITY\\SYSTEM" in acl
    assert "BUILTIN\\Users" not in acl  # other local users are shut out


def test_is_local_system_is_false_for_a_normal_user() -> None:
    """The restyle guard keys on this: it must never report the ordinary test
    user (or any non-Windows host) as LocalSystem, or restyle would wrongly be
    disabled for real users."""
    assert winsec.is_local_system() is False


@pytest.mark.skipif(not IS_WINDOWS, reason="registry bootstrap is Windows-only")
def test_registry_bootstrap_import(tmp_path: Path) -> None:
    """MSI seeds HKLM; the first tick imports it and deletes the token value.
    Tests run against HKCU via the documented seam."""
    import winreg

    def _cleanup() -> None:
        with contextlib.suppress(OSError):
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, BOOTSTRAP_REG_PATH)

    _cleanup()
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, BOOTSTRAP_REG_PATH)
    try:
        winreg.SetValueEx(key, "api_base_url", 0, winreg.REG_SZ, "https://pg.example-intranet")
        winreg.SetValueEx(key, "org_id", 0, winreg.REG_SZ, "acme-eng")
        winreg.SetValueEx(key, "org_token", 0, winreg.REG_SZ, "boot-secret-token")
        winreg.CloseKey(key)

        data_dir = tmp_path / "data"
        env = {"PRACTICEGRAPH_BOOTSTRAP_HIVE": "HKCU"}
        assert import_bootstrap(env, data_dir)

        config = json.loads((data_dir / "config.json").read_text(encoding="utf-8"))
        assert config["api_base_url"] == "https://pg.example-intranet"
        assert config["org_id"] == "acme-eng"
        assert "org_token" not in config  # token goes to the protected store only
        assert load_org_token(data_dir) == "boot-secret-token"

        read_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, BOOTSTRAP_REG_PATH)
        with read_key:
            with pytest.raises(OSError):
                winreg.QueryValueEx(read_key, "org_token")  # consumed and deleted
            value, _ = winreg.QueryValueEx(read_key, "org_id")
            assert value == "acme-eng"  # provenance values remain
    finally:
        _cleanup()


def _make_profiles(tmp_path: Path) -> Path:
    root = tmp_path / "profiles"
    (root / "alice" / ".claude" / "projects" / "proj").mkdir(parents=True)
    (root / "alice" / ".claude" / "projects" / "proj" / "a.jsonl").write_text("", encoding="utf-8")
    (root / "alice" / ".codex" / "sessions" / "2026" / "07").mkdir(parents=True)
    (root / "alice" / ".codex" / "sessions" / "2026" / "07" / "rollout-a.jsonl").write_text(
        "", encoding="utf-8"
    )
    (root / "bob" / ".config" / "claude" / "projects" / "p").mkdir(parents=True)
    (root / "bob" / ".config" / "claude" / "projects" / "p" / "b.jsonl").write_text(
        "", encoding="utf-8"
    )
    # Template/service profiles must be excluded.
    (root / "Public" / ".claude" / "projects" / "x").mkdir(parents=True)
    (root / "Public" / ".claude" / "projects" / "x" / "nope.jsonl").write_text("", encoding="utf-8")
    return root


def test_legacy_profile_scan_flag_cannot_merge_users(tmp_path: Path) -> None:
    """Service context (FR-SRC-2): LocalSystem has no home; discovery covers
    every real user profile and skips template/service profiles."""
    root = _make_profiles(tmp_path)
    env = {
        "PRACTICEGRAPH_SCAN_PROFILES": "1",
        "PRACTICEGRAPH_PROFILES_ROOT": str(root),
        "PRACTICEGRAPH_CLAUDE_HOME": str(root / "alice" / ".claude"),
        "PRACTICEGRAPH_CODEX_HOME": str(root / "alice" / ".codex"),
    }
    claude_files = [p.name for p in CLAUDE_ADAPTER.discover(env)]
    assert claude_files == ["a.jsonl"]  # only the explicitly configured user
    codex_files = [p.name for p in CODEX_ADAPTER.discover(env)]
    assert codex_files == ["rollout-a.jsonl"]


def test_profile_scan_off_by_default(tmp_path: Path) -> None:
    root = _make_profiles(tmp_path)
    env = {
        "PRACTICEGRAPH_PROFILES_ROOT": str(root),
        "PRACTICEGRAPH_CLAUDE_HOME": str(tmp_path / "absent"),
        "PRACTICEGRAPH_CODEX_HOME": str(tmp_path / "absent"),
    }
    assert CLAUDE_ADAPTER.discover(env) == []
    assert CODEX_ADAPTER.discover(env) == []


def test_cli_config_set_and_show(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PRACTICEGRAPH_ORG_TOKEN", raising=False)
    assert (
        main(["config", "set", "--api-base-url", "https://pg.internal", "--org-id", "acme-eng"])
        == 0
    )
    capsys.readouterr()
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "acme-eng" in out
    assert "org_token: not configured" in out


def test_cli_set_token_never_echoes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(data_dir))
    monkeypatch.delenv("PRACTICEGRAPH_ORG_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("tok-typed-secret\n"))
    assert main(["config", "set-token"]) == 0
    out = capsys.readouterr().out
    assert "tok-typed-secret" not in out
    assert load_org_token(data_dir) == "tok-typed-secret"
    capsys.readouterr()
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "tok-typed-secret" not in out
    assert "present (source: protected" in out


def test_sbom_marks_frontend_runtime_and_decodes_integrity() -> None:
    import importlib.util
    import re

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("sbom", root / "packaging/sbom.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    npm = module._npm_components(root)
    assert len(npm) > 50
    assert next(c for c in npm if c["name"] == "react")["scope"] == "required"
    assert any(c["scope"] == "excluded" for c in npm)
    assert all(
        re.fullmatch(r"[0-9a-f]{128}", h["content"]) for c in npm for h in c.get("hashes", [])
    )


def test_the_python_runtime_download_is_verified_not_merely_recorded() -> None:
    """The control that existed, looked like it worked, and did nothing until
    2026-07-26: the build computed the CPython embeddable's SHA-256, printed
    it, and never compared it to anything. A hash you record is not a hash you
    check — and this file is reused across builds, so a poisoned cache is the
    likelier path than a tampered download."""
    windows = Path("packaging/build_workstation.ps1").read_text(encoding="utf-8")

    assert "$expected" in windows
    assert "FAILED verification - refusing to build" in windows
    # SHA-256 is checked against the reviewed official runtime download.
    assert "sha256 =" in windows and "3.14.7" in windows
    # An unpinned version is refused rather than trusted.
    assert "no pinned hash for Python" in windows
    # A failed check does not leave the bad file cached for the next run.
    assert "Remove-Item $zipPath" in windows
