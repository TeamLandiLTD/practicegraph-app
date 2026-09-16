"""Record the source and actual bundled file identities without collecting user data."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def record(bundle: Path, target: str) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    files = {
        p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle.rglob("*"))
        if p.is_file() and not p.is_symlink() and p.name != "BUILD-INFO.json"
    }
    return {
        "source_commit": commit,
        "tracked_source_dirty": bool(dirty),
        "platform": target,
        "identity_stage": "before-code-signing"
        if target.startswith("macos")
        else "packaged-payload",
        "python": platform.python_version(),
        "files_sha256": files,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()
    (args.bundle / "BUILD-INFO.json").write_text(
        json.dumps(record(args.bundle, args.platform), indent=2) + "\n", encoding="utf-8"
    )
