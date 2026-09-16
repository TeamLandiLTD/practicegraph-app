"""Run the actual installed GTK shell under Xvfb with disposable local records."""

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gio, GLib  # noqa: E402 -- select GI versions before importing

application = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("pg_desktop_smoke", application)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
outcome = {"passed": False, "checking": False}
deadline = time.monotonic() + 60


def inspected(view, result, _data):
    try:
        outcome["passed"] = view.run_javascript_finish(result).get_js_value().to_boolean()
    except Exception:
        outcome["passed"] = False
    outcome["checking"] = False
    if outcome["passed"]:
        Gio.Application.get_default().quit()


def inspect():
    app = Gio.Application.get_default()
    if time.monotonic() >= deadline:
        if app:
            app.quit()
        return False
    view = getattr(app, "view", None)
    if view is not None and not outcome["checking"]:
        outcome["checking"] = True
        view.run_javascript(
            "document.querySelector('nav[aria-label=\"Main navigation\"]') !== null "
            "&& document.querySelector('main[aria-label=\"Usage\"]') !== null "
            "&& document.querySelector('nav a[href=\"#spend\"][aria-current=\"page\"]') !== null "
            "&& document.querySelector('nav a[href=\"#news\"]') !== null",
            None,
            inspected,
            None,
        )
    return True


with tempfile.TemporaryDirectory(prefix="practicegraph-gtk-smoke-") as temporary:
    for key in list(os.environ):
        if key.startswith("PRACTICEGRAPH_"):
            del os.environ[key]
    os.environ["PRACTICEGRAPH_DATA_DIR"] = temporary
    os.environ["PRACTICEGRAPH_CLAUDE_HOME"] = temporary + "/empty-claude"
    os.environ["PRACTICEGRAPH_CODEX_HOME"] = temporary + "/empty-codex"
    os.environ["PRACTICEGRAPH_CLAUDE_DESKTOP"] = temporary + "/empty-desktop"
    for key in (
        "API_BASE",
        "CONTENT_BASE",
        "SKILLS",
        "NEWS",
        "MODELS",
        "ADVISOR",
        "RATECARD",
        "LICENSE",
        "UPDATE",
        "DOCS",
        "MODEL_CATALOG",
        "BUILD_IDEAS",
        "COMMUNITY",
        "HARNESS_PLAYBOOKS",
        "TRAINING",
    ):
        os.environ[f"PRACTICEGRAPH_{key}_URL"] = ""
    # These test processes run only on a disposable CI host. Bound the server
    # lifetime by tracking the Popen handle rather than killing by name/PID file.
    children = []
    original_popen = module.subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process

    module.subprocess.Popen = tracked_popen
    sys.argv = [str(application)]
    GLib.timeout_add(500, inspect)
    try:
        module.main()
    finally:
        for process in children:
            process.terminate()
            try:
                process.wait(timeout=5)
            except module.subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
assert outcome["passed"], "GTK shell did not render the shared category navigation"
print("Native GTK smoke passed: actual shell rendered Usage from its bundled engine")
