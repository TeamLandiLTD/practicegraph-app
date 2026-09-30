"""Write the SBOM for a built bundle: everything that ships, and its hash.

Attached to each GitHub release next to the MSI. It answers the question you
only ask *after* something goes wrong — "was that thing in the build we shipped
in March?" — which is impossible to answer later if nobody wrote it down at the
time.

For MSI releases the file inventory is extracted from the finished installer,
including its embedded build record. Dependency metadata supplements those
hashes with Python, resolved Windows Rust, and frontend build information.

CycloneDX 1.5, because it is the format vulnerability scanners already read.
Stdlib only, like the rest of the core.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SPEC_VERSION = "1.5"

# Third-party components inside the bundle, and how to find their version from
# what is actually on disk. Anything not matched here is our own code, which
# the file inventory covers.
_DIST_INFO = re.compile(r"^([A-Za-z0-9_.-]+)-([0-9][^-]*)\.dist-info$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _python_components(bundle: Path) -> list[dict[str, object]]:
    """Third-party Python packages, read from the .dist-info actually staged."""
    components: list[dict[str, object]] = []
    app = bundle / "app"
    if not app.is_dir():
        return components
    for entry in sorted(app.iterdir()):
        matched = _DIST_INFO.match(entry.name) if entry.is_dir() else None
        if matched is None:
            continue
        name, version = matched.group(1), matched.group(2)
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name.lower()}@{version}",
                "scope": "required",
            }
        )
    return components


def _rust_components(repo: Path) -> list[dict[str, object]]:
    """Resolved target graph, excluding unused lockfile/platform entries."""
    metadata = json.loads(
        subprocess.run(
            [
                "cargo",
                "+stable-gnu",
                "metadata",
                "--format-version",
                "1",
                "--locked",
                "--filter-platform",
                "x86_64-pc-windows-gnu",
            ],
            cwd=repo / "shell",
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout
    )
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    return [
        {
            "type": "library",
            "name": p["name"],
            "version": p["version"],
            "purl": f"pkg:cargo/{p['name']}@{p['version']}",
            "scope": "required",
        }
        for p in metadata["packages"]
        if p.get("source") and p["id"] in resolved
    ]


def _npm_components(repo: Path) -> list[dict[str, object]]:
    """What went INTO the dashboard bundle. These packages do not ship as
    files, but their output does - and a compromised build dependency reaches
    the browser context that holds the session token, which makes this the
    highest-consequence list here despite shipping nothing."""
    lock = repo / "ui" / "package-lock.json"
    if not lock.is_file():
        return []
    try:
        document = json.loads(lock.read_text(encoding="utf-8"))
    except ValueError:
        return []
    components: list[dict[str, object]] = []
    for path, meta in sorted((document.get("packages") or {}).items()):
        if not path.startswith("node_modules/") or not isinstance(meta, dict):
            continue
        name = path.rsplit("node_modules/", 1)[1]
        version = meta.get("version")
        if not isinstance(version, str):
            continue
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:npm/{name}@{version}",
                "scope": "excluded" if meta.get("dev") or meta.get("devOptional") else "required",
                **(
                    {
                        "hashes": [
                            {
                                "alg": "SHA-512",
                                "content": base64.b64decode(
                                    meta["integrity"].removeprefix("sha512-"), validate=True
                                ).hex(),
                            }
                        ]
                    }
                    if isinstance(meta.get("integrity"), str)
                    and meta["integrity"].startswith("sha512-")
                    else {}
                ),
            }
        )
    return components


def _files(bundle: Path) -> list[dict[str, object]]:
    """Every shipped file with its SHA-256 — the part that is actually a bill
    of MATERIALS rather than a bill of intentions."""
    entries: list[dict[str, object]] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        entries.append(
            {
                "type": "file",
                "name": path.relative_to(bundle).as_posix(),
                "hashes": [{"alg": "SHA-256", "content": _sha256(path)}],
            }
        )
    return entries


def build_sbom(repo: Path, bundle: Path, version: str, timestamp: str) -> dict:
    components = (
        _python_components(bundle) + _rust_components(repo) + _npm_components(repo) + _files(bundle)
    )
    build_info = (bundle / "BUILD-INFO.txt").read_text(encoding="utf-8-sig")
    runtime = re.search(r"python embeddable: ([0-9.]+)", build_info)
    if runtime:
        components.append(
            {"type": "application", "name": "CPython", "version": runtime[1], "scope": "required"}
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": {
                "type": "application",
                "name": "PracticeGraph",
                "version": version,
            },
        },
        "components": components,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("dist/PracticeGraph"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--msi", type=Path, help="inventory the actual finished MSI")
    parser.add_argument("--out", type=Path, default=Path("dist/sbom.json"))
    # Passed in rather than read from the clock so two builds of the same
    # commit differ only where they genuinely differ.
    parser.add_argument("--timestamp", required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    if not args.bundle.is_dir():
        raise SystemExit(f"no staged bundle at {args.bundle}")
    if args.msi:
        from verify_msi_payload import extracted_payload

        with tempfile.TemporaryDirectory(prefix="pg-sbom-") as temporary:
            temporary = Path(temporary)
            payload = extracted_payload(args.msi, temporary / "extracted")
            materialized = temporary / "payload"
            for name, path in payload.items():
                target = materialized / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            sbom = build_sbom(repo, materialized, args.version, args.timestamp)
        sbom["metadata"]["properties"] = [
            {"name": "practicegraph:artifact", "value": args.msi.name},
            {"name": "practicegraph:artifact-sha256", "value": _sha256(args.msi)},
            {"name": "practicegraph:inventory", "value": "extracted MSI File table"},
        ]
    else:
        sbom = build_sbom(repo, args.bundle, args.version, args.timestamp)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    kinds: dict[str, int] = {}
    for component in sbom["components"]:
        kinds[str(component["type"])] = kinds.get(str(component["type"]), 0) + 1
    print(f"wrote {args.out}")
    print(f"  {kinds.get('library', 0)} components, {kinds.get('file', 0)} files")


if __name__ == "__main__":
    main()
