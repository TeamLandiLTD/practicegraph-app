"""Export an explicit public file inventory from one commit, without Git history."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "packaging/public-source-files.txt"


def public_files(root: Path) -> list[str]:
    names = [
        line
        for line in (root / INVENTORY).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    if len(names) != len(set(names)) or names != sorted(names):
        raise ValueError("public inventory must be unique and sorted")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("invalid public path")
        if not (root / name).is_file() or (root / name).is_symlink():
            raise ValueError(f"missing public file: {name}")
        if any(
            part in {".git", "drafts", "rejected", "research", "superpowers"} for part in path.parts
        ) or name.startswith(("tests/legacy_", ".agents/skills/", ".claude/skills/")):
            raise ValueError(f"private material in public inventory: {name}")
    # Every production module and test must be present, including newly added
    # security helpers. Other new files remain private until deliberately listed.
    required = {
        p.relative_to(root).as_posix()
        for folder in ("src", "tests")
        for p in (root / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        and not any(part.endswith(".egg-info") for part in p.parts)
    }
    if required - set(names):
        raise ValueError(f"unlisted application/test files: {sorted(required - set(names))}")
    return names


def export(root: Path, revision: str, destination: Path) -> dict[str, object]:
    names = public_files(root)
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # Export committed bytes only. Local drafts/edits are never incorporated.
    committed_inventory = subprocess.run(
        ["git", "show", f"{commit}:{INVENTORY}"], cwd=root, capture_output=True, check=True
    ).stdout
    if committed_inventory.replace(b"\r\n", b"\n") != (root / INVENTORY).read_bytes().replace(
        b"\r\n", b"\n"
    ):
        raise ValueError("commit the reviewed public inventory before exporting")
    # Key by path; git ls-tree emits mode first.
    modes = {
        path: mode
        for mode, path in (
            line.split(" ", 1)
            for line in subprocess.run(
                ["git", "ls-tree", "-r", "--format=%(objectmode) %(path)", commit],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
        )
    }
    blobs: dict[str, bytes] = {}
    for name in names:
        blob = subprocess.run(
            ["git", "show", f"{commit}:{name}"], cwd=root, capture_output=True, check=True
        ).stdout
        if modes.get(name) not in {"100644", "100755"}:
            raise ValueError(f"public file is not a regular committed file: {name}")
        blobs[name] = blob
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in blobs.items():
            info = zipfile.ZipInfo(name, (2026, 9, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = int(modes[name], 8) << 16
            archive.writestr(info, body)
    record = {
        "source_commit": commit,
        "files": len(blobs),
        "archive_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "file_sha256": {name: hashlib.sha256(body).hexdigest() for name, body in blobs.items()},
    }
    destination.with_suffix(".provenance.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.check:
        print(f"public inventory: {len(public_files(ROOT))} files verified")
    elif args.out:
        record = export(ROOT, args.revision, args.out)
        print(f"exported {record['files']} files from {record['source_commit']}")
    else:
        parser.error("choose --check or --out")


if __name__ == "__main__":
    main()
