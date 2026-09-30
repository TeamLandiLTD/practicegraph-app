"""Boundaries for the Linux shell: identity, navigation, and closed protocol verbs."""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("pg_linux", Path("linux/practicegraph_desktop.py"))
assert spec and spec.loader
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


@pytest.mark.parametrize("uri,expected", [
    ("http://127.0.0.1:45001/#practice", "local"),
    ("http://127.0.0.1:45002/", "blocked"),
    ("http://localhost:45001/", "blocked"),
    ("http://user@127.0.0.1:45001/", "blocked"),
    ("blob:http://127.0.0.1:45001/id", "download"),
    ("blob:https://example.com/id", "blocked"),
    ("https://example.com/docs", "external"),
    ("https://user:password@example.com/", "blocked"),
    ("file:///etc/passwd", "blocked"),
    ("javascript:alert(1)", "blocked"),
])
def test_navigation_boundary(uri: str, expected: str) -> None:
    assert desktop.navigation_kind(uri, 45001) == expected


@pytest.mark.parametrize("port,token", [(True, "a" * 43), (0, "a" * 43),
                                      (65536, "a" * 43), (45001, "a" * 40 + "&next=x")])
def test_endpoint_rejects_invalid_credentials(tmp_path: Path, port: object, token: str) -> None:
    (tmp_path / "ui.json").write_text(json.dumps({"port": port, "token": token}))
    assert desktop.read_endpoint(tmp_path) is None


def test_endpoint_requires_authenticated_ping() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/api/ping"
            supplied = self.headers.get("X-PracticeGraph-Token")
            self.send_response(200 if supplied == "a" * 43 else 403)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert desktop.endpoint_live((server.server_port, "a" * 43))
        assert not desktop.endpoint_live((server.server_port, "b" * 43))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_protocol_accepts_only_closed_open_actions() -> None:
    assert desktop.protocol_fragment("practicegraph:open") == ""
    assert desktop.protocol_fragment("practicegraph://break") == "#break"
    assert desktop.protocol_fragment("practicegraph:open?command=delete") is None
    assert desktop.protocol_fragment("https://example.com/") is None


def test_data_directory_obeys_explicit_personal_location(tmp_path: Path) -> None:
    assert desktop.data_directory({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)}) == tmp_path
    assert desktop.data_directory({"XDG_DATA_HOME": str(tmp_path)}) == tmp_path / "practicegraph"
