"""What the harnesses ARE: versions, connectors, installed skills.

The logs say what the harnesses DID and the pinned defaults say what they
START on; this module reads what they are — the installed version (from
their own session logs, no subprocess), the MCP servers and plugins each
config declares, and the skills installed on disk. Local reads only,
fail-closed everywhere, and names-not-content: a connector or skill NAME
is configuration, the same class as a model name — and a skill's declared
frontmatter description is the one line it publishes about itself, read
so the page can say what each skill IS. Bodies, commands and paths never
leave this module.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from practicegraph.sources.claude_code import ENV_CLAUDE_HOME
from practicegraph.sources.codex import ENV_CODEX_HOME, ENV_CODEX_HOME_NATIVE

_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}[0-9A-Za-z.+-]{0,24}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._@-]{0,63}\Z")
_PROBE_LINES = 40
# Per-probe consumption cap. Transcript lines can be enormous (an active
# session's records carry whole tool results), so the probe must read WHOLE
# lines — a byte-capped read that truncates mid-line parses nothing and
# reported "no version stamp" on a machine whose logs carry one.
_PROBE_BYTES = 8_000_000


@dataclass(frozen=True, slots=True)
class ToolVersion:
    tool: str
    installed: str | None  # None = no readable stamp in the newest log


@dataclass(frozen=True, slots=True)
class Connector:
    name: str
    kind: str        # "mcp" | "plugin"
    enabled: bool
    scope: str       # "global" | "project" | "user"
    detail: str | None  # a plugin's version; never a command or a path


@dataclass(frozen=True, slots=True)
class HarnessConnectors:
    tool: str
    connectors: tuple[Connector, ...]


@dataclass(frozen=True, slots=True)
class SkillInfo:
    name: str
    about: str | None  # the author's own description from SKILL.md; None
    #                    when the skill ships no frontmatter description


@dataclass(frozen=True, slots=True)
class HarnessSkills:
    tool: str
    skills: tuple[SkillInfo, ...]


def _homes(env: dict[str, str] | None) -> tuple[Path, Path]:
    resolved = dict(os.environ) if env is None else env
    claude_home = resolved.get(ENV_CLAUDE_HOME)
    claude = Path(claude_home) if claude_home else Path.home() / ".claude"
    codex_home = resolved.get(ENV_CODEX_HOME) or resolved.get(ENV_CODEX_HOME_NATIVE)
    codex = Path(codex_home) if codex_home else Path.home() / ".codex"
    return claude, codex


def _clean_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if _VERSION.fullmatch(stripped) is None:
        return None
    return stripped


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or _NAME.fullmatch(stripped) is None:
        return None
    return stripped


# How many of the newest logs the version probe reads. One is not enough:
# several long-running sessions interleave appends, so "the newest file"
# flips between them minute to minute — after an update, a session started
# on the OLD binary keeps re-winning the mtime race and the page flapped
# between versions (field report 2026-08-22). The installed version is the
# HIGHEST stamp across the recent files: a newer stamp proves the newer
# binary ran, while an old long-runner can never un-prove it.
_PROBE_NEWEST = 12


def _newest_files(root: Path, pattern: str) -> list[Path]:
    stamped: list[tuple[float, Path]] = []
    try:
        for path in root.rglob(pattern):
            try:
                stamped.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError:
        return []
    stamped.sort(key=lambda item: -item[0])
    return [path for _, path in stamped[:_PROBE_NEWEST]]


def _probe_lines(path: Path) -> list[str]:
    lines: list[str] = []
    consumed = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                lines.append(line)
                consumed += len(line)
                if len(lines) >= _PROBE_LINES or consumed >= _PROBE_BYTES:
                    break
    except OSError:
        return []
    return lines


def _best_version(candidates: list[str]) -> str | None:
    if not candidates:
        return None
    return max(candidates, key=version_tuple)


def installed_versions(env: dict[str, str] | None = None) -> tuple[ToolVersion, ...]:
    """The version each harness runs, read from its own recent session logs
    without spawning anything: the highest stamp across the newest few
    files, so a long-running session started on an older binary cannot
    mask an update (see _PROBE_NEWEST)."""
    claude, codex = _homes(env)
    claude_found: list[str] = []
    for path in _newest_files(claude / "projects", "*.jsonl"):
        for line in _probe_lines(path):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                stamp = _clean_version(record.get("version"))
                if stamp is not None:
                    claude_found.append(stamp)
                    break
    codex_found: list[str] = []
    for path in _newest_files(codex / "sessions", "*.jsonl"):
        for line in _probe_lines(path):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload")
            source = payload if isinstance(payload, dict) else record
            stamp = _clean_version(source.get("cli_version"))
            if stamp is not None:
                codex_found.append(stamp)
                break
    return (
        ToolVersion("claude_code", _best_version(claude_found)),
        ToolVersion("codex", _best_version(codex_found)),
    )


def _claude_connectors(home: Path) -> tuple[Connector, ...]:
    found: list[Connector] = []
    # MCP servers: the global map plus per-project declarations, from the
    # tool's own root config file next to the home directory.
    config_path = home.parent / ".claude.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
    if isinstance(config, dict):
        servers = config.get("mcpServers")
        if isinstance(servers, dict):
            for raw in servers:
                name = _clean_name(raw)
                if name is not None:
                    found.append(Connector(name, "mcp", True, "global", None))
        projects = config.get("projects")
        if isinstance(projects, dict):
            seen: set[str] = set()
            for project in projects.values():
                if not isinstance(project, dict):
                    continue
                scoped = project.get("mcpServers")
                if not isinstance(scoped, dict):
                    continue
                for raw in scoped:
                    name = _clean_name(raw)
                    if name is not None and name not in seen:
                        seen.add(name)
                        found.append(Connector(name, "mcp", True, "project", None))
    # Plugins: the manifest the plugin manager maintains. Only the name,
    # scope and version travel; install paths stay here.
    manifest_path = home / "plugins" / "installed_plugins.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    plugins = manifest.get("plugins") if isinstance(manifest, dict) else None
    if isinstance(plugins, dict):
        for raw, installs in plugins.items():
            name = _clean_name(raw)
            if name is None or not isinstance(installs, list):
                continue
            first = installs[0] if installs and isinstance(installs[0], dict) else {}
            scope = first.get("scope")
            version = _clean_version(first.get("version"))
            found.append(Connector(
                name, "plugin", True,
                scope if scope in ("user", "project") else "user",
                version,
            ))
    return tuple(found)


def _codex_connectors(home: Path) -> tuple[Connector, ...]:
    try:
        with (home / "config.toml").open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return ()
    found: list[Connector] = []
    for raw, table in servers.items():
        name = _clean_name(raw)
        if name is None:
            continue
        enabled = True
        if isinstance(table, dict) and table.get("enabled") is False:
            enabled = False
        found.append(Connector(name, "mcp", enabled, "global", None))
    return tuple(found)


def read_connectors(env: dict[str, str] | None = None) -> tuple[HarnessConnectors, ...]:
    claude, codex = _homes(env)
    return (
        HarnessConnectors("claude_code", _claude_connectors(claude)),
        HarnessConnectors("codex", _codex_connectors(codex)),
    )


_ABOUT_MAX = 240


def _skill_about(skill_dir: Path) -> str | None:
    """The description the skill states about itself: the `description:`
    value in SKILL.md's frontmatter — the same line every harness reads to
    decide when to load it. Only that one declared line travels (trimmed
    to a sentence); block scalars and everything past the frontmatter stay
    on disk. None when the skill ships no readable description."""
    try:
        text = (skill_dir / "SKILL.md").read_text(
            encoding="utf-8", errors="ignore")[:16_384]
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    value: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            value = line[len("description:"):].strip().strip("\"'")
            break
    if not value or value in ("|", ">") or value.startswith(("|", ">")):
        return None
    value = " ".join(value.split())
    if len(value) <= _ABOUT_MAX:
        return value
    # Prefer ending on the first sentence; otherwise cut at a word.
    period = value.find(". ", 40, _ABOUT_MAX)
    if period != -1:
        return value[: period + 1]
    return value[:_ABOUT_MAX].rsplit(" ", 1)[0] + " …"


def _skill_dirs(root: Path) -> tuple[SkillInfo, ...]:
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return ()
    found: list[SkillInfo] = []
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        name = _clean_name(entry.name)
        if name is not None:
            found.append(SkillInfo(name, _skill_about(entry)))
    return tuple(found)


def read_skills(env: dict[str, str] | None = None) -> tuple[HarnessSkills, ...]:
    claude, codex = _homes(env)
    return (
        HarnessSkills("claude_code", _skill_dirs(claude / "skills")),
        HarnessSkills("codex", _skill_dirs(codex / "skills")),
    )


def version_tuple(value: str | None) -> tuple[int, ...]:
    """Numeric groups only, for the honest newer-than comparison."""
    if not value:
        return ()
    return tuple(int(group) for group in re.findall(r"\d+", value)[:4])


def is_newer(latest: str | None, installed: str | None) -> bool:
    """True only when both sides parse and latest is numerically ahead.
    Different version families (a desktop build vs a CLI tag) compare
    numerically too — when that is wrong it is wrong toward silence."""
    left = version_tuple(latest)
    right = version_tuple(installed)
    return bool(left) and bool(right) and left > right
