#!/usr/bin/python3
"""Thin GTK/WebKit shell. Analysis and record writes belong to the bundled engine."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

TOKEN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")


def data_directory(env: dict[str, str]) -> Path:
    if env.get("PRACTICEGRAPH_DATA_DIR"):
        return Path(env["PRACTICEGRAPH_DATA_DIR"]).expanduser()
    base = Path(env.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "practicegraph"


def read_endpoint(directory: Path) -> tuple[int, str] | None:
    try:
        path = directory / "ui.json"
        if path.stat().st_size > 4096:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        port, token = value["port"], value["token"]
        if (
            type(port) is int
            and 0 < port < 65536
            and isinstance(token, str)
            and TOKEN.fullmatch(token)
        ):
            return port, token
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def endpoint_live(endpoint: tuple[int, str]) -> bool:
    connection = HTTPConnection("127.0.0.1", endpoint[0], timeout=1)
    try:
        connection.request("GET", "/api/ping", headers={"X-PracticeGraph-Token": endpoint[1]})
        response = connection.getresponse()
        body = response.read(4097)
        return response.status == 200 and len(body) <= 4096 and json.loads(body).get("ok") is True
    except (OSError, ValueError, AttributeError):
        return False
    finally:
        connection.close()


def navigation_kind(uri: str, port: int) -> str:
    """Only the authenticated dashboard's exact origin may stay in the webview."""
    try:
        parsed = urlsplit(uri)
        if parsed.username or parsed.password:
            return "blocked"
        if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port == port:
            return "local"
        if parsed.scheme == "blob" and navigation_kind(uri[5:], port) == "local":
            return "download"
        if parsed.scheme == "https" and parsed.hostname:
            return "external"
        if parsed.scheme == "mailto" and parsed.path and not any(c in uri for c in "\r\n"):
            return "external"
    except ValueError:
        pass
    return "blocked"


def protocol_fragment(uri: str) -> str | None:
    return {
        "practicegraph:open": "",
        "practicegraph://open": "",
        "practicegraph:break": "#break",
        "practicegraph://break": "#break",
    }.get(uri)


def ensure_endpoint(engine: Path, directory: Path) -> tuple[int, str] | None:
    endpoint = read_endpoint(directory)
    if endpoint and endpoint_live(endpoint):
        return endpoint
    env = dict(os.environ, PRACTICEGRAPH_DATA_DIR=str(directory))
    try:
        subprocess.Popen(
            [str(engine), "ui", "serve"],
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        endpoint = read_endpoint(directory)
        if endpoint and endpoint_live(endpoint):
            return endpoint
        time.sleep(0.2)
    return None


def main() -> int:
    # Distro-owned GTK/WebKit receive security updates through the package manager.
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gio, GLib, Gtk, WebKit2
    except (ImportError, ValueError):
        print(
            "PracticeGraph needs python3-gi, gir1.2-gtk-3.0 and gir1.2-webkit2-4.1. "
            "See the Linux installation guide.",
            file=sys.stderr,
        )
        return 1

    root = Path(__file__).resolve().parent
    engine = root / "engine/practicegraph-engine"
    directory = data_directory(dict(os.environ))
    fragment = ""
    for arg in sys.argv[1:]:
        value = protocol_fragment(arg)
        if value is None:
            print("Unsupported PracticeGraph launch argument.", file=sys.stderr)
            return 2
        fragment = value

    class Desktop(Gtk.Application):
        def __init__(self):
            super().__init__(
                application_id="dev.practicegraph.Desktop", flags=Gio.ApplicationFlags.HANDLES_OPEN
            )
            self.window = None
            self.view = None
            self.port = None
            self.fragment = fragment

        def do_open(self, files, _count, _hint):
            # GApplication forwards protocol opens to the existing instance.
            for item in files:
                value = protocol_fragment(item.get_uri())
                if value is not None and self.window is None:
                    self.fragment = value
            self.activate()

        def do_activate(self):
            if self.window is not None:
                self.window.present()
                return
            self.window = Gtk.ApplicationWindow(application=self, title="PracticeGraph")
            self.window.set_default_size(1280, 900)
            self.window.set_size_request(900, 600)
            self.window.connect("destroy", self.closed)
            self.window.add(Gtk.Label(label="Opening your daily reading…"))
            self.window.show_all()
            threading.Thread(target=self.start_engine, daemon=True).start()

        def closed(self, _window):
            self.window = None
            self.view = None

        def start_engine(self):
            endpoint = ensure_endpoint(engine, directory)
            GLib.idle_add(self.attach, endpoint)

        def attach(self, endpoint):
            if self.window is None:
                return False
            if not endpoint:
                self.window.get_child().set_text(
                    "The local dashboard could not start. Reopen PracticeGraph to retry.\n"
                    "The Linux installation guide includes diagnostic commands."
                )
                return False
            self.port, token = endpoint
            web_data = directory / "webview"
            web_data.mkdir(mode=0o700, parents=True, exist_ok=True)
            manager = WebKit2.WebsiteDataManager(
                base_data_directory=str(web_data), base_cache_directory=str(web_data / "cache")
            )
            context = WebKit2.WebContext.new_with_website_data_manager(manager)
            context.connect("download-started", self.download_started)
            self.view = WebKit2.WebView.new_with_context(context)
            self.view.get_settings().set_enable_developer_extras(False)
            self.view.connect("decide-policy", self.decide_policy)
            self.view.connect("permission-request", lambda _view, request: request.deny() or True)
            self.view.connect("load-failed", self.load_failed)
            self.window.remove(self.window.get_child())
            self.window.add(self.view)
            self.window.show_all()
            self.view.load_uri(f"http://127.0.0.1:{self.port}/?token={token}{self.fragment}")
            return False

        def load_failed(self, _view, _event, _uri, _error):
            # Do not display a failing URL: the initial navigation contains a token.
            dialog = Gtk.MessageDialog(
                transient_for=self.window,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="The local dashboard could not be loaded.",
            )
            dialog.format_secondary_text("Close and reopen PracticeGraph to retry.")
            dialog.run()
            dialog.destroy()
            return True

        def decide_policy(self, _view, decision, kind):
            if kind == WebKit2.PolicyDecisionType.RESPONSE:
                if not decision.is_mime_type_supported():
                    decision.download()
                    return True
                return False
            if kind not in (
                WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION,
            ):
                decision.ignore()
                return True
            action = decision.get_navigation_action()
            uri = action.get_request().get_uri()
            disposition = navigation_kind(uri, self.port)
            if disposition == "local":
                if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                    self.view.load_uri(uri)
                    decision.ignore()
                    return True
                return False
            if disposition == "download":
                decision.download()
                return True
            decision.ignore()
            if disposition == "external" and action.is_user_gesture():
                Gio.AppInfo.launch_default_for_uri(uri, None)
            return True

        def download_started(self, _context, download):
            if navigation_kind(download.get_request().get_uri(), self.port) not in (
                "local",
                "download",
            ):
                download.cancel()
                return
            download.connect("decide-destination", self.save_download)

        def save_download(self, download, suggested):
            dialog = Gtk.FileChooserDialog(
                title="Save PracticeGraph export",
                parent=self.window,
                action=Gtk.FileChooserAction.SAVE,
            )
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT)
            dialog.set_do_overwrite_confirmation(True)
            dialog.set_current_name(Path(suggested or "practicegraph-export.json").name)
            if dialog.run() == Gtk.ResponseType.ACCEPT:
                download.set_allow_overwrite(True)
                download.set_destination(Path(dialog.get_filename()).as_uri())
            else:
                download.cancel()
            dialog.destroy()
            return True

    app = Desktop()
    # Protocol arguments have already been validated; do_open handles later requests.
    return app.run([sys.argv[0], *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
