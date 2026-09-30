"""Record the source and actual bundled file identities without collecting user data."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def _git(root: Path, *args: str) -> str | None:
    """Run git in the source tree; None when it is not a checkout (e.g. a tarball)."""
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def record(bundle: Path, target: str, root: Path | None = None) -> dict[str, object]:
    root = root or Path(__file__).resolve().parents[1]
    # Outside a git checkout the commit and dirtiness are unknown, not "clean":
    # record null rather than failing the build or claiming a false identity.
    commit = _git(root, "rev-parse", "HEAD")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no") if commit else None
    files = {
        p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bundle.rglob("*"))
        if p.is_file() and not p.is_symlink() and p.name != "BUILD-INFO.json"
    }
    return {
        "source_commit": commit,
        "tracked_source_dirty": None if dirty is None else bool(dirty),
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
