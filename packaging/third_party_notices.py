"""Assemble redistribution notices from installed public dependency sources.

Includes the resolved Windows Rust graph and production npm packages.
The embedded Python and Python packages retain their own license files.
Run after npm ci and cargo build; fail if a dependency has no discoverable notice.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
from pathlib import Path


def notices(root: Path, toolchain: str, platform: str = "windows") -> str:
    metadata = (
        json.loads(
            subprocess.run(
                [
                    "cargo",
                    f"+{toolchain}",
                    "metadata",
                    "--format-version",
                    "1",
                    "--locked",
                    "--filter-platform",
                    "x86_64-pc-windows-gnu",
                ],
                cwd=root / "shell",
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            ).stdout
        )
        if platform == "windows"
        else {"resolve": {"nodes": []}, "packages": []}
    )
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    dependencies = [
        (
            f"Rust: {p['name']} {p['version']}",
            p.get("license") or "see license text",
            Path(p["manifest_path"]).parent,
        )
        for p in metadata["packages"]
        if p.get("source") and p["id"] in resolved
    ]
    lock = json.loads((root / "ui/package-lock.json").read_text(encoding="utf-8"))
    for name, item in lock["packages"].items():
        if not name or item.get("dev") or item.get("devOptional"):
            continue
        dependencies.append(
            (
                f"npm: {name.removeprefix('node_modules/')} {item['version']}",
                item.get("license", "see license text"),
                root / "ui" / name,
            )
        )
    output = [
        "PracticeGraph third-party notices",
        f"Resolved {platform} application dependencies.",
        "Python runtime and package license files are retained beside those components.",
        "Dependencies keep their original licenses; "
        "the root Apache-2.0 license does not replace them.",
    ]
    if platform in {"macos", "linux"}:
        # Frozen Python packages and the PyInstaller bootloader/exception must
        # retain their notices even though dist-info files are not frozen.
        for name in ("tzdata", "tzlocal", "cryptography", "cffi", "pycparser", "pyinstaller"):
            distribution = importlib.metadata.distribution(name)
            license_files = [
                distribution.locate_file(p)
                for p in distribution.files or []
                if any(
                    part.lower().startswith(("license", "copying", "notice")) for part in p.parts
                )
                and distribution.locate_file(p).is_file()
            ]
            if not license_files:
                raise ValueError(f"missing frozen dependency notices: {name}")
            output.append(f"\nPython: {name} {distribution.version}")
            for path in sorted(set(license_files)):
                output.extend((f"\n--- {path.name} ---", path.read_text(encoding="utf-8")))
    for label, expression, folder in sorted(dependencies):
        files = sorted(
            p
            for p in folder.iterdir()
            if p.is_file()
            and (p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")))
        )
        if not files and label.startswith("Rust: webview2-com"):
            vcs = json.loads((folder / ".cargo_vcs_info.json").read_text(encoding="utf-8"))
            sha = vcs["git"]["sha1"]
            supplement = root / "packaging/third-party" / f"webview2-rs-{sha}.LICENSE"
            if supplement.is_file():
                files = [supplement]
        if not files:
            raise ValueError(f"no dependency license file found for {label}")
        output.extend(("\n" + "=" * 72, label, f"Declared license: {expression}"))
        for path in files:
            output.extend((f"\n--- {path.name} ---", path.read_text(encoding="utf-8")))
    supplements = [root / "packaging/third-party/Python-3.14.7.LICENSE.txt"]
    if platform == "windows":
        provenance = json.loads(
            (root / "packaging/third-party/webview2-sdk-provenance.json").read_text(
                encoding="utf-8"
            )
        )
        folder = next(
            folder
            for label, _, folder in dependencies
            if label == "Rust: " + provenance["matched_crate"]
        )
        for name, expected in provenance["x64_sha256"].items():
            if hashlib.sha256((folder / "x64" / name).read_bytes()).hexdigest() != expected:
                raise ValueError("WebView2 SDK changed; review its license/provenance")
        supplements.extend(sorted((root / "packaging/third-party").glob("Microsoft.Web.*.txt")))
    for path in supplements:
        output.extend((f"\n--- {path.name} ---", path.read_text(encoding="utf-8")))
    return "\n".join(output).rstrip() + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--toolchain", default="stable-gnu")
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), default="windows")
    args = parser.parse_args()
    args.out.write_text(
        notices(Path(__file__).resolve().parent.parent, args.toolchain, args.platform),
        encoding="utf-8",
    )
