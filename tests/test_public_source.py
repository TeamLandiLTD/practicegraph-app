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
             ".agents/skills/curator/SKILL.md", ".claude/skills/curator/SKILL.md"]
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
