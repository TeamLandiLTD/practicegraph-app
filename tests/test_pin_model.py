"""The pin protocol: edit the tool's own config without ever wrecking it."""

from __future__ import annotations

import json
from pathlib import Path

from practicegraph.analysis.pin_model import (
    BACKUP_SUFFIX,
    pin_claude_model,
    pin_codex_model,
    recent_claude_project_dirs,
)


def test_claude_pin_preserves_every_other_key_and_backs_up(tmp_path) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    prior = {
        "model": "claude-haiku-4-5",
        "effortLevel": "low",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "env": {"FOO": "bar"},
    }
    (home / "settings.json").write_text(json.dumps(prior, indent=2))
    result = pin_claude_model(home, "claude-opus-5", "high")
    assert result.outcome == "pinned"
    after = json.loads((home / "settings.json").read_text())
    assert after["model"] == "claude-opus-5"
    assert after["effortLevel"] == "high"
    assert after["permissions"] == prior["permissions"]
    assert after["env"] == prior["env"]
    # The rolling backup carries the exact prior bytes.
    backup = home / ("settings.json" + BACKUP_SUFFIX)
    assert result.backup == str(backup)
    assert json.loads(backup.read_text()) == prior


def test_claude_pin_refuses_a_broken_file_untouched(tmp_path) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    broken = '{"model": "x", TRAILING'
    (home / "settings.json").write_text(broken)
    result = pin_claude_model(home, "claude-opus-5", None)
    assert result.outcome == "refused_unreadable"
    # Untouched means byte-identical, and no backup was created.
    assert (home / "settings.json").read_text() == broken
    assert result.backup is None
    assert not (home / ("settings.json" + BACKUP_SUFFIX)).exists()


def test_claude_pin_creates_the_file_when_absent(tmp_path) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    result = pin_claude_model(home, "claude-sonnet-5", None)
    assert result.outcome == "pinned"
    assert result.backup is None
    assert json.loads((home / "settings.json").read_text()) == {
        "model": "claude-sonnet-5"
    }


def test_codex_pin_is_a_surgical_line_edit(tmp_path) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    prior = (
        "# my codex config\n"
        'model = "gpt-5.6-sol"\n'
        "model_reasoning_effort = \"xhigh\"\n"
        "\n"
        "[mcp_servers.garmin]\n"
        'command = "docker"\n'
        '# a model key inside a section must never be touched\n'
        'model = "not-the-top-level"\n'
    )
    (home / "config.toml").write_text(prior)
    result = pin_codex_model(home, "gpt-5.6-terra", "medium")
    assert result.outcome == "pinned"
    after = (home / "config.toml").read_text()
    assert 'model = "gpt-5.6-terra"\n' in after
    assert 'model_reasoning_effort = "medium"\n' in after
    # Comments, the section, and the section's own model key all survive.
    assert "# my codex config" in after
    assert "[mcp_servers.garmin]" in after
    assert 'model = "not-the-top-level"' in after
    assert (home / ("config.toml" + BACKUP_SUFFIX)).read_text() == prior


def test_codex_pin_inserts_before_the_first_section(tmp_path) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text('[profiles.fast]\nmodel = "gpt-5.6-luna"\n')
    result = pin_codex_model(home, "gpt-5.6-sol", None)
    assert result.outcome == "pinned"
    text = (home / "config.toml").read_text()
    # The new top-level assignment lands ABOVE the section, where it is
    # top-level — after it, it would belong to the section.
    assert text.index('model = "gpt-5.6-sol"') < text.index("[profiles.fast]")


def test_codex_pin_refuses_when_an_active_profile_overrides(tmp_path) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    prior = (
        'profile = "fast"\n'
        'model = "gpt-5.6-sol"\n'
        "[profiles.fast]\n"
        'model = "gpt-5.6-luna"\n'
    )
    (home / "config.toml").write_text(prior)
    result = pin_codex_model(home, "gpt-5.6-terra", None)
    assert result.outcome == "refused_profile_active"
    assert (home / "config.toml").read_text() == prior


def test_codex_pin_refuses_a_broken_file_untouched(tmp_path) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    broken = "model = unclosed [\n"
    (home / "config.toml").write_text(broken)
    result = pin_codex_model(home, "gpt-5.6-sol", None)
    assert result.outcome == "refused_unreadable"
    assert (home / "config.toml").read_text() == broken


def test_hostile_values_are_refused_before_any_read(tmp_path) -> None:
    home = tmp_path / ".codex"
    home.mkdir()
    for bad in ('x"\nprofile = "evil', "a\nb", 'model"] = ["x'):
        assert pin_codex_model(home, bad, None).outcome == "refused_value"
    assert pin_claude_model(tmp_path, "ok-model", "UPPER").outcome \
        == "refused_value"


def test_project_dirs_come_from_transcript_heads_and_must_exist(tmp_path) -> None:
    home = tmp_path / "claudehome"
    proj = home / "projects" / "enc"
    proj.mkdir(parents=True)
    real = tmp_path / "work" / "Alpha"
    real.mkdir(parents=True)
    (proj / "a.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(real)}) + "\n"
    )
    (proj / "b.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(tmp_path / "gone" / "Beta")}) + "\n"
    )
    sub = proj / "a" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(tmp_path)}) + "\n"
    )
    found = recent_claude_project_dirs({"PRACTICEGRAPH_CLAUDE_HOME": str(home)})
    # Alpha resolves; Beta's directory no longer exists; the subagent
    # transcript is a part of a session, not a session.
    assert found == {"Alpha": Path(real)}


def test_every_value_the_catalog_can_publish_is_writable(tmp_path) -> None:
    """The writer's charsets must be a superset of the catalog's, or a
    legitimately published value is refused - and the refusal used to blame
    the reader's own file (2026-08-24 review). "+()" come from the catalog's
    model pattern, "-" from its effort levels."""
    from practicegraph.analysis.model_catalog import _LEVEL, _MODEL

    for value in ("gpt-5.6-sol (preview)", "claude-opus-4+", "Claude Opus 4.5"):
        assert _MODEL.fullmatch(value), value      # the catalog would accept it
        result = pin_claude_model(tmp_path / "c", value, None)
        assert result.outcome == "pinned", (value, result.outcome)
    for level in ("xhigh", "x-high"):
        assert _LEVEL.fullmatch(level), level
        result = pin_claude_model(tmp_path / "e", "opus", level)
        assert result.outcome == "pinned", (level, result.outcome)


def test_a_section_header_inside_a_string_is_not_structure(tmp_path) -> None:
    """The TOML editor reads lines, so a "[x]" line inside a multi-line
    string used to end the top-level region - and the effort assignment was
    inserted INSIDE somebody's string literal (2026-08-24 review)."""
    import tomllib

    home = tmp_path / ".codex"
    home.mkdir()
    prior = (
        'model = "gpt-5.6-sol"\n'
        'doc = """\n'
        "[x]\n"
        "not a section, just text\n"
        '"""\n'
        'model_reasoning_effort = "xhigh"\n'
    )
    (home / "config.toml").write_text(prior, encoding="utf-8")
    result = pin_codex_model(home, "gpt-5.6-terra", "medium")
    assert result.outcome == "pinned"
    after_text = (home / "config.toml").read_text(encoding="utf-8")
    after = tomllib.loads(after_text)
    assert after["model"] == "gpt-5.6-terra"
    assert after["model_reasoning_effort"] == "medium"
    # The string survived: nothing was inserted into it.
    assert after["doc"] == "[x]\nnot a section, just text\n"
    # ...and the real top-level key was replaced, not duplicated.
    assert after_text.count("model_reasoning_effort") == 1


def test_an_assignment_inside_a_string_is_never_the_one_replaced(tmp_path) -> None:
    import tomllib

    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text(
        'doc = """\n'
        'model = "not the real one"\n'
        '"""\n'
        'model = "gpt-5.6-sol"\n',
        encoding="utf-8",
    )
    assert pin_codex_model(home, "gpt-5.6-terra", None).outcome == "pinned"
    after = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    assert after["model"] == "gpt-5.6-terra"
    assert after["doc"] == 'model = "not the real one"\n'


def test_a_failed_verification_rolls_the_file_back(tmp_path, monkeypatch) -> None:
    """A pin that cannot be verified must be a no-op. Leaving the file we
    already know is wrong in place - and telling the person to restore it
    themselves - was the old behaviour (2026-08-24 review)."""
    from practicegraph.analysis import pin_model as pin_module
    from practicegraph.analysis.tool_defaults import ToolDefault

    home = tmp_path / ".claude"
    home.mkdir()
    prior = '{\n  "model": "haiku",\n  "permissions": {"allow": []}\n}\n'
    (home / "settings.json").write_text(prior, encoding="utf-8")
    monkeypatch.setattr(
        pin_module, "_claude_default",
        lambda _dir: ToolDefault("claude_code", "something-else", None),
    )
    result = pin_claude_model(home, "opus", None)
    assert result.outcome == "verify_failed"
    # The original bytes are back, and the backup is still there to inspect.
    assert (home / "settings.json").read_text(encoding="utf-8") == prior
    assert result.backup is not None and Path(result.backup).exists()


def test_a_rolled_back_creation_leaves_no_file_behind(tmp_path, monkeypatch) -> None:
    from practicegraph.analysis import pin_model as pin_module
    from practicegraph.analysis.tool_defaults import ToolDefault

    home = tmp_path / ".claude"
    monkeypatch.setattr(
        pin_module, "_claude_default",
        lambda _dir: ToolDefault("claude_code", None, None),
    )
    result = pin_claude_model(home, "opus", None)
    assert result.outcome == "verify_failed"
    # We created the file; undoing means removing it, not leaving a stub.
    assert not (home / "settings.json").exists()


def test_a_non_utf8_codex_config_is_refused_not_raised(tmp_path) -> None:
    """UnicodeDecodeError is a ValueError, so it escaped both this module's
    OSError handler and the endpoint's (2026-08-24 review): a cp1252 comment
    crashed the request instead of returning a refusal."""
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_bytes(
        b'# note \x93smart quotes\x94\nmodel = "gpt-5.6-sol"\n'
    )
    result = pin_codex_model(home, "gpt-5.6-terra", None)
    assert result.outcome == "refused_unreadable"
    assert result.backup is None
