"""The pinned default each harness starts a session on.

The logs show which model RAN; only the tool's own local config says which
model a fresh session STARTS on — the difference between the usage and the
choice. The models page reads it the way the owner's coach dashboard does:
locally, fail-closed, values validated against a closed shape before they
reach any payload. Absence is a real state ("not pinned") and is reported
as such, never guessed.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from json import loads
from pathlib import Path

from practicegraph.sources.claude_code import ENV_CLAUDE_HOME
from practicegraph.sources.codex import ENV_CODEX_HOME, ENV_CODEX_HOME_NATIVE

# The single source of truth for what a pinned value may look like, shared
# with the WRITER (analysis.pin_model imports these). Three independent
# copies of this charset drifted apart once: the served catalog could carry
# "+()" and "-", which the writer refused and the reader then failed to read
# back, turning a legitimate pin into verify_failed (2026-08-24 review). They
# stay narrower than the file formats - no quote, backslash, newline, "[" or
# "=" reaches a config file.
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .:+()_/-]{0,79}\Z")
_EFFORT = re.compile(r"[a-z][a-z-]{0,15}\Z")


@dataclass(frozen=True, slots=True)
class ToolDefault:
    tool: str            # "claude_code" | "codex"
    model: str | None    # None = not pinned (the tool's own default applies)
    effort: str | None


def _clean(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or pattern.fullmatch(stripped) is None:
        return None
    return stripped


def _codex_default(home: Path) -> ToolDefault:
    path = home / "config.toml"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ToolDefault("codex", None, None)
    model = _clean(data.get("model"), _MODEL)
    effort = _clean(data.get("model_reasoning_effort"), _EFFORT)
    # An active profile overrides the top-level keys, the way the CLI
    # itself resolves them.
    profile = data.get("profile")
    profiles = data.get("profiles")
    if isinstance(profile, str) and isinstance(profiles, dict):
        chosen = profiles.get(profile)
        if isinstance(chosen, dict):
            model = _clean(chosen.get("model"), _MODEL) or model
            effort = _clean(chosen.get("model_reasoning_effort"), _EFFORT) or effort
    return ToolDefault("codex", model, effort)


def _claude_default(home: Path) -> ToolDefault:
    path = home / "settings.json"
    try:
        data = loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ToolDefault("claude_code", None, None)
    if not isinstance(data, dict):
        return ToolDefault("claude_code", None, None)
    # effortLevel is Claude Code's pinned reasoning dial, the counterpart
    # of Codex's model_reasoning_effort.
    return ToolDefault(
        "claude_code",
        _clean(data.get("model"), _MODEL),
        _clean(data.get("effortLevel"), _EFFORT),
    )


def read_tool_defaults(
    env: dict[str, str] | None = None,
) -> tuple[ToolDefault, ...]:
    """One entry per harness, mirroring the sources' home resolution:
    the explicit override first, then the per-user default location."""
    resolved = dict(os.environ) if env is None else env
    codex_home = resolved.get(ENV_CODEX_HOME) or resolved.get(ENV_CODEX_HOME_NATIVE)
    codex = Path(codex_home) if codex_home else Path.home() / ".codex"
    claude_home = resolved.get(ENV_CLAUDE_HOME)
    claude = Path(claude_home) if claude_home else Path.home() / ".claude"
    return (_claude_default(claude), _codex_default(codex))
