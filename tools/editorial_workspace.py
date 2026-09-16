"""Private working locations for maintainers; never part of the shipped app."""

from __future__ import annotations

import os
from pathlib import Path


def editorial_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    selected = os.environ.get("PRACTICEGRAPH_EDITORIAL_DIR")
    location = (Path(selected) if selected else root.parent / "practicegraph-site" / "editorial")
    location = location.resolve()
    if location == root or root in location.parents:
        raise ValueError("editorial inputs must be outside the open-source application tree")
    return location
