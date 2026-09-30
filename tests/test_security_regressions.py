"""Synthetic reproductions of the September OSS security audit findings."""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import stat
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from practicegraph import winsec
from practicegraph.analysis.pin_model import pin_claude_model, pin_codex_model
from practicegraph.secureio import atomic_write, private_directory
from practicegraph.transport import post_emit
from practicegraph_server.app import make_server
from practicegraph_server.config import load_config
from practicegraph_server.storage import MemoryStorage, SqliteStorage
from test_server import DASHBOARD_PASSWORD, ORG, TOKEN, _payload, _request, _server_config


@contextmanager
def running(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("persistent", [False, True])
def test_one_authenticated_source_cannot_inflate_a_cohort(tmp_path, persistent):
    storage = SqliteStorage(str(tmp_path / "fleet.db")) if persistent else MemoryStorage()
    sources = tuple((f"source-{i}", f"synthetic-source-{i}-" + "x" * 32) for i in range(5))
    config = dataclasses.replace(_server_config(5), ingest_tokens=sources)
    server = make_server(config, storage)
    with running(server) as base:
        url = base + "/v1/aggregates/summary?from=2026-07-02&to=2026-07-02"
        for _ in range(6):
            assert (
                _request(
                    "POST", base + "/v1/ingest", _payload(str(uuid.uuid4())), bearer=sources[0][1]
                )[0]
                == 202
            )
        status, _, body = _request("GET", url, bearer=DASHBOARD_PASSWORD)
        assert status == 200
        assert json.loads(body)["days"][0]["status"] == "suppressed"
        for _, secret in sources[1:]:
            assert (
                _request("POST", base + "/v1/ingest", _payload(str(uuid.uuid4())), bearer=secret)[0]
                == 202
            )
        # Rotation of an existing source credential must not add a contributor.
        rotated = "synthetic-rotated-" + "x" * 32
        server.config = dataclasses.replace(
            config, ingest_tokens=((sources[0][0], rotated), *sources[1:])
        )
        _request("POST", base + "/v1/ingest", _payload(str(uuid.uuid4())), bearer=rotated)
        _, _, body = _request("GET", url, bearer=DASHBOARD_PASSWORD)
        assert json.loads(body)["days"][0]["contributors"] == 5
        assert b"source_id" not in body and rotated.encode() not in body


@pytest.mark.parametrize("dashboard", [True, False])
def test_read_and_ingest_roles_never_cross(dashboard):
    config = dataclasses.replace(_server_config(5), serve_dashboard=dashboard)
    with running(make_server(config, MemoryStorage())) as base:
        for route in ("summary", "coverage"):
            url = base + "/v1/aggregates/" + route
            assert _request("GET", url, bearer=TOKEN)[0] == 401
            assert _request("GET", url, basic=TOKEN)[0] == 401
            assert _request("GET", url, bearer=DASHBOARD_PASSWORD)[0] == 200
            assert _request("GET", url, basic=DASHBOARD_PASSWORD)[0] == 200
            assert _request("GET", url, basic="non-ascii-\u00e9")[0] == 401
        assert (
            _request(
                "POST", base + "/v1/ingest", _payload(str(uuid.uuid4())), bearer=DASHBOARD_PASSWORD
            )[0]
            == 401
        )


def test_legacy_token_is_one_source_and_never_an_admin():
    config = dataclasses.replace(_server_config(5), ingest_tokens=(), dashboard_password="")
    storage = MemoryStorage()
    with running(make_server(config, storage)) as base:
        for _ in range(5):
            assert (
                _request("POST", base + "/v1/ingest", _payload(str(uuid.uuid4())), bearer=TOKEN)[0]
                == 202
            )
        assert len(storage.payloads_by_day(ORG, "2026-07-02", "2026-07-02")["2026-07-02"]) == 1
        assert _request("GET", base + "/dashboard", basic=TOKEN)[0] == 401
        assert _request("GET", base + "/v1/aggregates/summary", bearer=TOKEN)[0] == 401


def test_legacy_database_rows_stay_preserved_but_unattributed(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE emits(emit_id TEXT PRIMARY KEY, org_id TEXT NOT NULL,
            day TEXT NOT NULL, received_at TEXT NOT NULL, payload_json TEXT NOT NULL);
            PRAGMA user_version=1;""")
        db.execute("INSERT INTO emits VALUES(?,?,?,?,?)", ("old", ORG, "2026-07-02", "t", "{}"))
    storage = SqliteStorage(str(path))
    assert storage.payloads_by_day(ORG, "2026-07-01", "2026-07-03") == {}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM emits").fetchone()[0] == 1


@pytest.mark.parametrize("persistent", [False, True])
def test_simultaneous_retries_cannot_duplicate_a_source(tmp_path, persistent):
    storage = SqliteStorage(str(tmp_path / "concurrent.db")) if persistent else MemoryStorage()

    def submit(_):
        return storage.put_emit(ORG, str(uuid.uuid4()), "2026-07-02", {}, "t", source_id="one")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(submit, range(16))) == 1


@pytest.mark.parametrize("raw", ['{"a":"short"}', '{"a":1}', "{}", '{"a":"x","a":"y"}', "not-json"])
def test_bad_credential_configuration_fails_closed(raw):
    with pytest.raises(ValueError):
        load_config({"PG_SERVER_INGEST_TOKENS": raw})


def test_credential_roles_cannot_share_a_secret():
    with pytest.raises(ValueError):
        dataclasses.replace(_server_config(5), dashboard_password=TOKEN)
    assert load_config({"PG_SERVER_K_THRESHOLD": "1"}).k_threshold == 5


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_ingest_never_follows_redirects(code):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(code)
            self.send_header("Location", "/unexpected")
            self.end_headers()

        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

    with running(ThreadingHTTPServer(("127.0.0.1", 0), Handler)) as base:
        assert not post_emit(base, "synthetic-secret", "{}").ok
    assert received == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_private_state_and_pin_modes(tmp_path):
    private_directory(tmp_path / "state")
    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700
    path = tmp_path / "settings.json"
    path.write_text('{"model":"sonnet"}', encoding="utf-8")
    path.chmod(0o600)
    assert pin_claude_model(tmp_path, "opus", None).outcome == "pinned"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_name(path.name + ".practicegraph-backup").stat().st_mode) == 0o600


def test_linked_configs_and_backups_are_refused(tmp_path):
    source = tmp_path / "original"
    source.write_text('{"model":"sonnet"}', encoding="utf-8")
    linked = tmp_path / "settings.json"
    os.link(source, linked)
    assert pin_claude_model(tmp_path, "opus", None).outcome == "io_error"
    assert source.read_text() == '{"model":"sonnet"}'
    linked.unlink()
    linked.write_text('{"model":"sonnet"}', encoding="utf-8")
    os.link(source, tmp_path / "settings.json.practicegraph-backup")
    assert pin_claude_model(tmp_path, "opus", None).outcome == "io_error"
    assert source.read_text() == '{"model":"sonnet"}'


def test_permission_failure_never_writes_payload(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows ACL failure")
    monkeypatch.setattr(winsec, "restrict_to_owner_and_admins", lambda _: False)
    with pytest.raises(PermissionError):
        atomic_write(tmp_path / "secret", b"synthetic secret")
    assert list(tmp_path.iterdir()) == []


def test_codex_hard_link_is_refused(tmp_path):
    source = tmp_path / "other.toml"
    source.write_text('model = "example"', encoding="utf-8")
    os.link(source, tmp_path / "config.toml")
    assert pin_codex_model(tmp_path, "example-two", None).outcome == "io_error"


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL regression")
def test_pinning_preserves_private_acl_in_a_readable_directory(tmp_path):
    system = Path(os.environ["SYSTEMROOT"]) / "System32"
    subprocess.run(
        [str(system / "icacls.exe"), str(tmp_path), "/grant", "*S-1-5-32-545:(OI)(CI)RX"],
        check=True,
        capture_output=True,
    )
    path = tmp_path / "settings.json"
    path.write_text('{"model":"sonnet","env":{"EXAMPLE":"synthetic"}}', encoding="utf-8")
    assert winsec.restrict_to_owner_and_admins(path)

    def users_read(target):
        import ctypes
        from ctypes import wintypes

        descriptor = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        api = winsec._advapi32
        assert (
            api.GetNamedSecurityInfoW(
                str(target), 1, 4, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
            )
            == 0
        )
        convert = api.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = (
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            wintypes.LPVOID,
        )
        output = wintypes.LPWSTR()
        try:
            assert convert(descriptor, 1, 4, ctypes.byref(output), None)
            return ";;;BU)" in output.value or ";;;S-1-5-32-545)" in output.value
        finally:
            if output:
                winsec._kernel32.LocalFree(output)
            winsec._kernel32.LocalFree(descriptor)

    assert not users_read(path)
    assert pin_claude_model(tmp_path, "opus", None).outcome == "pinned"
    assert not users_read(path)
    assert not users_read(tmp_path / "settings.json.practicegraph-backup")
