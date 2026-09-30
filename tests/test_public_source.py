"""Publication boundaries: explicit inventory, immutable source, and no private history."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from tools.public_source import INVENTORY, export, public_files


def test_export_uses_committed_bytes_and_omits_private_material(tmp_path: Path) -> None:
    names = [INVENTORY, "src/example.py", "tests/test_example.py"]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# public example\n", encoding="utf-8")
    (tmp_path / INVENTORY).write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    (tmp_path / "private-editorial.json").write_text('{"draft": "private"}', encoding="utf-8")
    for args in (
        ["init", "--quiet"],
        ["config", "user.email", "synthetic@example.com"],
        ["config", "user.name", "Synthetic Test"],
        ["add", "."],
        ["commit", "--quiet", "-m", "synthetic snapshot"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "src/example.py").write_text("# uncommitted draft\n", encoding="utf-8")
    archive = tmp_path / "output/source.zip"
    record = export(tmp_path, "HEAD", archive)
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == set(names)
        assert bundle.read("src/example.py") == b"# public example\n"
        assert not any(".git/" in name or "editorial" in name for name in bundle.namelist())
    assert json.loads(archive.with_suffix(".provenance.json").read_text()) == record
    (tmp_path / INVENTORY).write_text("\n".join(sorted(names)) + "\n# changed\n")
    with pytest.raises(ValueError, match="commit the reviewed"):
        export(tmp_path, "HEAD", tmp_path / "changed.zip")


@pytest.mark.parametrize(
    "name", ["../secret.txt", "docs/research/private.md", "tests/legacy_list.py",
             ".agents/skills/curator/SKILL.md", ".claude/skills/curator/SKILL.md",
             "tests/maintainer/test_release.py", "tools/curate_news.py",
             "tools/content_release.py", "docs/EDITORIAL_WORKFLOWS.md"]
)
def test_public_inventory_rejects_private_or_escaping_paths(tmp_path: Path, name: str) -> None:
    path = tmp_path / INVENTORY
    path.parent.mkdir(parents=True)
    path.write_text(name + "\n")
    if not name.startswith(".."):
        item = tmp_path / name
        item.parent.mkdir(parents=True, exist_ok=True)
        item.touch()
    with pytest.raises(ValueError):
        public_files(tmp_path)


def test_new_production_files_require_inventory_review(tmp_path: Path) -> None:
    path = tmp_path / INVENTORY
    path.parent.mkdir(parents=True)
    path.write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/new_module.py").touch()
    with pytest.raises(ValueError, match="unlisted application"):
        public_files(tmp_path)


def test_maintainer_tests_stay_private_without_inventory_entries(tmp_path: Path) -> None:
    path = tmp_path / INVENTORY
    path.parent.mkdir(parents=True)
    path.write_text("tests/test_public.py\n")
    (tmp_path / "tests/maintainer").mkdir(parents=True)
    (tmp_path / "tests/test_public.py").touch()
    (tmp_path / "tests/maintainer/test_release.py").touch()
    assert public_files(tmp_path) == ["tests/test_public.py"]


@pytest.mark.parametrize(
    "name", ["shell/src/new_module.rs", "macos/Sources/PracticeGraphShell/New.swift",
             "macos/Tests/PracticeGraphShellTests/NewTests.swift", "ui/src/New.jsx",
             "linux/new_helper.py", "packaging/new_step.sh"]
)
def test_native_shell_ui_and_packaging_files_require_inventory_review(
    tmp_path: Path, name: str
) -> None:
    """The completeness check once covered only src/ and tests/, so a Rust
    module the Windows shell declares (inputwatch.rs) stayed out of the
    inventory and the published shell could not build."""
    path = tmp_path / INVENTORY
    path.parent.mkdir(parents=True)
    path.write_text("")
    item = tmp_path / name
    item.parent.mkdir(parents=True, exist_ok=True)
    item.touch()
    with pytest.raises(ValueError, match="unlisted application"):
        public_files(tmp_path)


def test_build_caches_never_count_as_unlisted_files(tmp_path: Path) -> None:
    path = tmp_path / INVENTORY
    path.parent.mkdir(parents=True)
    path.write_text("")
    for name in ("linux/__pycache__/x.cpython-314.pyc", "shell/target/debug/x",
                 "macos/Sources/.DS_Store", "ui/src/.DS_Store"):
        item = tmp_path / name
        item.parent.mkdir(parents=True, exist_ok=True)
        item.touch()
    assert public_files(tmp_path) == []


def test_git_archives_keep_every_published_file() -> None:
    """GitHub's "Source code" archive honours export-ignore. An allowlist there
    once dropped ten published docs and the README screenshots from it; the
    inventory is the only boundary, so no rule may drop files."""
    attributes = (Path(__file__).resolve().parents[1] / ".gitattributes").read_text(
        encoding="utf-8"
    )
    rules = [line for line in attributes.splitlines() if line and not line.startswith("#")]
    assert not [rule for rule in rules if "export-ignore" in rule]


def test_ci_names_only_the_public_repository() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    for repository in (line.split("github.repository ==")[1].strip().strip("'\"")
                       for line in workflow.splitlines() if "github.repository ==" in line):
        assert repository == "TeamLandiLTD/practicegraph-app"
