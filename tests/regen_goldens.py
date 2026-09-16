"""Regenerate the golden files — the intentional, audited flow that FR-RPT-3
requires. Run ``python tests/regen_goldens.py`` and review the golden diff
like an API change before committing.
"""

from __future__ import annotations

from conftest import (
    FIXTURE_GENERATED_AT,
    GOLDENS,
    build_dashboard_summary,
    build_fixture_extras,
    build_fixture_snapshot,
    build_shell_privacy_status,
)
from practicegraph import __version__
from practicegraph.analysis.ratecard import RATE_CARD_VERSION
from practicegraph.report.html import render_html
from practicegraph.report.shell import render_shell
from practicegraph.report.text import render_text
from practicegraph_server.render import render_dashboard


def regenerate() -> list[str]:
    GOLDENS.mkdir(exist_ok=True)
    snapshot = build_fixture_snapshot()
    extras = build_fixture_extras()
    written = []

    text = render_text(
        snapshot, FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION, extras=extras
    )
    (GOLDENS / "daily_report.txt").write_bytes(text.encode("utf-8"))
    written.append("daily_report.txt")

    html = render_html(
        snapshot, FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION, extras=extras
    )
    (GOLDENS / "daily_report.html").write_bytes(html.encode("utf-8"))
    written.append("daily_report.html")

    shell = render_shell(
        snapshot,
        build_shell_privacy_status(),
        FIXTURE_GENERATED_AT,
        __version__,
        RATE_CARD_VERSION,
        extras=extras,
    )
    (GOLDENS / "shell_report.html").write_bytes(shell.encode("utf-8"))
    written.append("shell_report.html")

    dashboard = render_dashboard(build_dashboard_summary(), "acme-eng", FIXTURE_GENERATED_AT)
    (GOLDENS / "dashboard.html").write_bytes(dashboard.encode("utf-8"))
    written.append("dashboard.html")

    return written


if __name__ == "__main__":
    for name in regenerate():
        print(f"regenerated tests/goldens/{name}")
    print("review the golden diff before committing (FR-RPT-3)")
