"""The shipped dashboard bundle must be complete in git, not just on disk.

Vite writes content-hashed asset names, so every `npm run build` produces a
NEW filename and orphans the previous one. `git commit -a` stages the
modified index.html and the deletion of the old assets but never stages the
new (untracked) ones — which ships an index.html whose script and stylesheet
do not exist in the repository, and a packaged app that opens to a blank
window with no error (2026-08-24 review; same class as the 2026-07-19 MSI
payload incident).

This is the cheapest possible gate: whatever index.html asks for must exist
on disk AND be tracked by git.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEBUI = REPO_ROOT / "webui"
_ASSET_REF = re.compile(r'(?:src|href)="\./(assets/[^"]+)"')


def _referenced_assets() -> list[str]:
    index = (WEBUI / "index.html").read_text(encoding="utf-8")
    return _ASSET_REF.findall(index)


def test_index_html_references_at_least_its_script_and_styles() -> None:
    refs = _referenced_assets()
    assert any(ref.endswith(".js") for ref in refs), refs
    assert any(ref.endswith(".css") for ref in refs), refs


def test_every_referenced_asset_exists_on_disk() -> None:
    missing = [ref for ref in _referenced_assets() if not (WEBUI / ref).is_file()]
    assert missing == [], (
        f"webui/index.html references {missing} which are not on disk - "
        "run 'npm run build' in ui/ before packaging"
    )


def test_every_referenced_asset_is_tracked_by_git() -> None:
    """The one that catches the real footgun: built, on disk, never staged."""
    refs = _referenced_assets()
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--", "webui/assets"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return  # not a git checkout (a build sandbox); the disk test still ran
    known = {Path(entry).name for entry in tracked}
    untracked = [ref for ref in refs if Path(ref).name not in known]
    assert untracked == [], (
        f"webui/index.html references {untracked}, which git does not track - "
        "'git add webui/assets' before committing, or the shipped bundle 404s"
    )
