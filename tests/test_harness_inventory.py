"""The harness inventory: versions, connectors, skills — local, fail-closed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import practicegraph.catalog as catalog_module
from practicegraph.analysis.harness_inventory import (
    Connector,
    installed_versions,
    is_newer,
    read_connectors,
    read_skills,
)
from practicegraph.catalog import (
    load_harness_releases,
    pull_public_releases,
)
from practicegraph.config import resolve
from practicegraph.store import Store

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _homes(tmp_path: Path) -> dict[str, str]:
    claude = tmp_path / "claudehome"
    codex = tmp_path / "codexhome"
    (claude / "projects" / "proj").mkdir(parents=True)
    (codex / "sessions").mkdir(parents=True)
    return {
        "PRACTICEGRAPH_CLAUDE_HOME": str(claude),
        "PRACTICEGRAPH_CODEX_HOME": str(codex),
    }


def test_versions_come_from_the_newest_logs(tmp_path: Path) -> None:
    env = _homes(tmp_path)
    claude = Path(env["PRACTICEGRAPH_CLAUDE_HOME"])
    codex = Path(env["PRACTICEGRAPH_CODEX_HOME"])
    (claude / "projects" / "proj" / "a.jsonl").write_text(
        json.dumps({"type": "user", "version": "2.1.234"}) + "\n",
        encoding="utf-8",
    )
    (codex / "sessions" / "s.jsonl").write_text(
        json.dumps({"type": "session_meta",
                    "payload": {"cli_version": "0.148.0-alpha.15"}}) + "\n",
        encoding="utf-8",
    )
    claude_v, codex_v = installed_versions(env)
    assert claude_v.installed == "2.1.234"
    assert codex_v.installed == "0.148.0-alpha.15"


def test_version_survives_the_long_running_session_race(tmp_path: Path) -> None:
    """Field report 2026-08-22: after a Claude Code update the page first
    showed the new version, then flipped back. The newest-by-mtime file was
    a long-running session started on the OLD binary, re-winning the race
    with every append. The installed version is the HIGHEST stamp across
    the newest few logs — an old long-runner can never mask an update."""
    import os
    import time

    env = _homes(tmp_path)
    proj = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "projects" / "proj"
    updated = proj / "updated-session.jsonl"
    updated.write_text(
        json.dumps({"type": "user", "version": "2.1.238"}) + "\n",
        encoding="utf-8",
    )
    old_runner = proj / "long-runner.jsonl"
    old_runner.write_text(
        json.dumps({"type": "user", "version": "2.1.234"}) + "\n",
        encoding="utf-8",
    )
    # The old-version session appended most recently.
    behind = time.time() - 3600
    os.utime(updated, (behind, behind))
    claude_v, _ = installed_versions(env)
    assert claude_v.installed == "2.1.238"


def test_version_survives_an_enormous_first_line(tmp_path: Path) -> None:
    """The bug the first real render found: the newest Claude log is the
    ACTIVE session, and an active session's early records can each run far
    past 64KB (whole tool results travel inline). The old byte-capped read
    truncated mid-line, parsed nothing, and the page said "no version stamp
    in the logs yet" on a machine whose logs carry one. The probe must read
    whole lines."""
    env = _homes(tmp_path)
    claude = Path(env["PRACTICEGRAPH_CLAUDE_HOME"])
    huge = json.dumps({"type": "user", "version": "2.1.234",
                       "message": "x" * 200_000})
    (claude / "projects" / "proj" / "a.jsonl").write_text(
        huge + "\n", encoding="utf-8",
    )
    claude_v, _ = installed_versions(env)
    assert claude_v.installed == "2.1.234"


def test_versions_fail_closed_on_empty_or_garbage(tmp_path: Path) -> None:
    env = _homes(tmp_path)
    (Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "sessions" / "s.jsonl").write_text(
        "not json\n{\"payload\": {\"cli_version\": \"<script>\"}}\n",
        encoding="utf-8",
    )
    claude_v, codex_v = installed_versions(env)
    assert claude_v.installed is None
    assert codex_v.installed is None


def test_connectors_enumerate_names_never_commands(tmp_path: Path) -> None:
    env = _homes(tmp_path)
    claude = Path(env["PRACTICEGRAPH_CLAUDE_HOME"])
    codex = Path(env["PRACTICEGRAPH_CODEX_HOME"])
    # ~/.claude.json sits NEXT TO the home dir, as in the real layout.
    (claude.parent / ".claude.json").write_text(json.dumps({
        "mcpServers": {"garmin": {"command": "secret.exe"}},
        "projects": {
            "C:/work/repo": {"mcpServers": {"filesys": {"command": "x"}}},
        },
    }), encoding="utf-8")
    (claude / "plugins").mkdir()
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"vercel@claude-plugins-official": [
            {"scope": "user", "installPath": "C:/x", "version": "0.45.1"},
        ]},
    }), encoding="utf-8")
    (codex / "config.toml").write_text(
        '[mcp_servers.node_repl]\ncommand = "node"\n'
        '[mcp_servers.xcode]\nenabled = false\n',
        encoding="utf-8",
    )
    claude_c, codex_c = read_connectors(env)
    assert Connector("garmin", "mcp", True, "global", None) in claude_c.connectors
    assert Connector("filesys", "mcp", True, "project", None) in claude_c.connectors
    assert Connector(
        "vercel@claude-plugins-official", "plugin", True, "user", "0.45.1"
    ) in claude_c.connectors
    assert Connector("node_repl", "mcp", True, "global", None) in codex_c.connectors
    assert Connector("xcode", "mcp", False, "global", None) in codex_c.connectors
    # Commands and paths never travel.
    for harness in (claude_c, codex_c):
        for item in harness.connectors:
            assert "exe" not in (item.detail or "")
            assert "/" not in (item.detail or "")


def test_skills_carry_their_own_stated_description(tmp_path: Path) -> None:
    """Design decision 2026-08-21: skills render like features — the name plus
    text saying what it is. The text is the skill's OWN frontmatter
    description, the line the harness itself reads; a skill without one
    reads as exactly that, never a guess."""
    env = _homes(tmp_path)
    claude_skills = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "skills"
    for name in ("ai-advisor", ".hidden", "frontend-design"):
        (claude_skills / name).mkdir(parents=True)
    (claude_skills / "ai-advisor" / "SKILL.md").write_text(
        "---\nname: ai-advisor\ndescription: Industry intelligence "
        "grounded in a local knowledge base. Everything after the first "
        "sentence stays on disk when the line runs long.\n---\n# Body "
        "never travels\n",
        encoding="utf-8",
    )
    # frontend-design ships no SKILL.md at all -> about is None.
    (Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "skills" / "codex-cost").mkdir(
        parents=True
    )
    (Path(env["PRACTICEGRAPH_CODEX_HOME"]) / "skills" / "codex-cost"
     / "SKILL.md").write_text(
        "---\ndescription: " + "spend analysis " * 40 + "\n---\n",
        encoding="utf-8",
    )
    claude_s, codex_s = read_skills(env)
    assert [s.name for s in claude_s.skills] == ["ai-advisor",
                                                 "frontend-design"]
    about = claude_s.skills[0].about
    assert about is not None
    assert about.startswith("Industry intelligence grounded")
    assert claude_s.skills[1].about is None
    # A very long single-line description is trimmed at a word, never
    # dumped whole onto the page.
    codex_about = codex_s.skills[0].about
    assert codex_about is not None
    assert len(codex_about) <= 245
    assert codex_about.endswith("…")


def test_skill_description_block_scalars_fail_closed(tmp_path: Path) -> None:
    env = _homes(tmp_path)
    skill = Path(env["PRACTICEGRAPH_CLAUDE_HOME"]) / "skills" / "odd"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\ndescription: |\n  a block scalar body\n---\n", encoding="utf-8",
    )
    claude_s, _ = read_skills(env)
    assert claude_s.skills[0].about is None


def test_is_newer_is_quiet_when_unsure() -> None:
    assert is_newer("2.1.240", "2.1.234")
    assert not is_newer("2.1.234", "2.1.234")
    assert not is_newer(None, "2.1.234")
    assert not is_newer("2.1.240", None)
    # A CLI tag numerically behind a desktop build stays silent.
    assert not is_newer("0.55.0", "0.148.0-alpha.15")


def test_release_pull_keeps_only_the_tag_and_a_pinned_url(
    tmp_path: Path, monkeypatch: object
) -> None:
    store = Store(tmp_path / "state.db")
    store.migrate()
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})

    def fake(url: str, timeout_s: float = 10.0, *, harden: bool = True,
             max_bytes: int | None = None, allow_list: bool = False) -> object:
        assert allow_list, "the releases pull reads a JSON array"
        if "anthropics/claude-code" in url:
            return [
                {
                    "tag_name": "v2.1.240",
                    "prerelease": False,
                    "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.240",
                    "body": "ignored free text",
                },
            ]
        # Codex publishes its actual work as prereleases; the last stable
        # sits behind them in the list — exactly the real repo's shape.
        return [
            {
                "tag_name": "rust-v0.150.0-alpha.6",
                "prerelease": True,
                "html_url": "https://evil.example.com/not-github",
            },
            {
                "tag_name": "rust-v0.149.0",
                "prerelease": False,
                "html_url": "https://github.com/openai/codex/releases/tag/rust-v0.149.0",
            },
        ]

    monkeypatch.setattr(catalog_module, "_get_json_any", fake)
    assert pull_public_releases(store, config, NOW) == "pulled"
    releases = load_harness_releases(store)
    assert releases["claude_code"] == (
        "2.1.240",
        "https://github.com/anthropics/claude-code/releases/tag/v2.1.240",
        "2.1.240",
        "https://github.com/anthropics/claude-code/releases/tag/v2.1.240",
    )
    # Both channels survive: the stable anchor AND the newest prerelease —
    # whose bad html_url falls back to the pinned releases page, never
    # elsewhere.
    assert releases["codex"] == (
        "0.149.0",
        "https://github.com/openai/codex/releases/tag/rust-v0.149.0",
        "0.150.0-alpha.6",
        "https://github.com/openai/codex/releases/latest",
    )
    assert pull_public_releases(store, config, NOW) == "skipped_already_today"
