"""One-click model pinning: change what a session STARTS on, safely.

The models page can already say what the recommended floor is; this module
lets the button actually set it — in the tool's own config file, the same
place the person would edit by hand. The file is theirs, not ours, so the
protocol is deliberately paranoid:

1. **Parse before touching.** The current file must parse cleanly (JSON for
   Claude Code's settings.json, TOML for Codex's config.toml). A file we
   cannot read completely is a file we refuse to edit — refused, with the
   reason, never a best-effort rewrite.
2. **Backup beside the original.** One rolling ``<name>.practicegraph-backup``
   copy of the exact prior bytes, written before every edit.
3. **Minimal edit.** JSON: load, set the one or two keys, dump back in the
   tool's own two-space style. TOML has no stdlib writer, so the edit is a
   surgical line replacement in the top-level region only — every other
   byte, comment and section survives untouched.
4. **Atomic swap.** Temp file in the same directory, then ``os.replace``.
5. **Verify by re-reading.** The claim "pinned" is only returned after the
   rewritten file parses and resolves to the requested value.

Codex honesty rule: when config.toml activates a profile whose model would
override the top-level key, editing the top level would LOOK pinned while
changing nothing. That case is refused with its own code and the page says
so — withheld-is-explained applies to actions too.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from practicegraph.analysis.tool_defaults import (
    _EFFORT as _READER_EFFORT,
)
from practicegraph.analysis.tool_defaults import (
    _MODEL as _READER_MODEL,
)
from practicegraph.analysis.tool_defaults import (
    _claude_default,
    _codex_default,
)
from practicegraph.secureio import atomic_write, reject_links
from practicegraph.sources.claude_code import ENV_CLAUDE_HOME
from practicegraph.sources.codex import ENV_CODEX_HOME, ENV_CODEX_HOME_NATIVE

BACKUP_SUFFIX = ".practicegraph-backup"

# Closed outcome codes; the UI maps each to a sentence.
PIN_OUTCOMES: tuple[str, ...] = (
    "pinned",
    "refused_unreadable",  # existing file does not parse; not touched
    "refused_profile_active",  # codex: an active profile would override the pin
    # The requested value itself is not writable (a catalog value carrying a
    # character this writer will not put in a config file). Distinct from
    # refused_unreadable so the message never blames the person's own file.
    "refused_value",
    "verify_failed",  # rewrite landed but did not resolve to the value
    "io_error",
)

# Writer and reader share ONE definition (tool_defaults owns it): a value
# this module will write must be a value the models page can read back, or
# verification fails on a perfectly good pin. Both are supersets of what the
# served catalog can carry (model_catalog._MODEL adds "+()", _LEVEL adds
# "-"), so a legitimately published value is never refused.
_MODEL_RE = _READER_MODEL
_EFFORT_RE = _READER_EFFORT
# TOML top-level assignments this module may replace. Anchored, whole-line.
_TOML_MODEL_LINE = re.compile(r"^\s*model\s*=")
_TOML_EFFORT_LINE = re.compile(r"^\s*model_reasoning_effort\s*=")
_TOML_SECTION_LINE = re.compile(r"^\s*\[")
# A line that opens or closes a multi-line string. Section headers and
# assignments inside such a string are DATA, not structure - see
# _toml_top_region_end.
_TOML_TRIPLE_QUOTE = re.compile(r'"""|\'\'\'')


@dataclass(frozen=True, slots=True)
class PinResult:
    outcome: str  # one of PIN_OUTCOMES
    path: str  # the file the action targeted (or would have)
    backup: str | None  # the rolling backup written, when one was


def _valid_request(model: str, effort: str | None) -> bool:
    if not _MODEL_RE.fullmatch(model):
        return False
    return effort is None or bool(_EFFORT_RE.fullmatch(effort))


def _roll_back(path: Path, prior: bytes | None) -> None:
    """Undo a swap that did not verify. A failed pin must be a no-op: the
    exact prior bytes are still in hand, so leaving a file we already know
    is wrong in place (and telling the person to restore it themselves) is
    never the right answer. Best effort - the backup remains either way."""
    with contextlib.suppress(OSError):
        if prior is None:
            path.unlink(missing_ok=True)  # we created it; remove it again
        else:
            backup = path.with_name(path.name + BACKUP_SUFFIX)
            atomic_write(path, prior, permissions_from=backup)


def _backup_then_swap(path: Path, prior: bytes | None, text: str) -> str | None:
    """Write the rolling backup (when the file existed), then atomically
    replace the file with ``text``. Returns the backup path or None."""
    backup: str | None = None
    if prior is not None:
        backup_path = path.with_name(path.name + BACKUP_SUFFIX)
        atomic_write(backup_path, prior, permissions_from=path)
        backup = str(backup_path)
    # Bytes, never text mode: text mode rewrites the file's own line endings
    # (\r\n becomes \r\r\n on Windows). The verify step catches that, but the
    # right move is to not corrupt in the first place.
    atomic_write(path, text.encode("utf-8"), permissions_from=path if prior is not None else None)
    return backup


def pin_claude_model(config_dir: Path, model: str, effort: str | None) -> PinResult:
    """Pin the model (and optionally effortLevel) in ``config_dir``'s
    settings.json — the user home's ``.claude`` for the machine scope, a
    project's ``.claude`` for the project scope. Creates the file when the
    directory is real and the file is absent."""
    path = config_dir / "settings.json"
    if not _valid_request(model, effort):
        return PinResult("refused_value", str(path), None)
    try:
        reject_links(path)
        prior: bytes | None
        try:
            prior = path.read_bytes()
        except FileNotFoundError:
            prior = None
        if prior is None:
            data: dict[str, object] = {}
        else:
            try:
                loaded = json.loads(prior.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return PinResult("refused_unreadable", str(path), None)
            if not isinstance(loaded, dict):
                return PinResult("refused_unreadable", str(path), None)
            data = loaded
        data["model"] = model
        if effort is not None:
            data["effortLevel"] = effort
        config_dir.mkdir(parents=True, exist_ok=True)
        backup = _backup_then_swap(
            path, prior, json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        )
        # The claim is earned by re-reading, through the same reader the
        # models page uses.
        landed = _claude_default(config_dir)
        if landed.model != model or (effort is not None and landed.effort != effort):
            _roll_back(path, prior)
            return PinResult("verify_failed", str(path), backup)
        return PinResult("pinned", str(path), backup)
    except (OSError, ValueError):
        # ValueError covers the Unicode errors a hostile-looking config can
        # raise while being read or re-encoded; the original is untouched
        # at every point one can fire.
        return PinResult("io_error", str(path), None)


def _toml_inside_string(lines: list[str]) -> list[bool]:
    """Per line: is this line the CONTENT of a multi-line string?

    A ``[x]`` or ``model = …`` line inside a ``\"\"\"…\"\"\"`` block is data, not
    structure. Reading it as structure truncated the top-level region and
    let an inserted assignment land inside somebody's string literal, and
    let a replacement rewrite a string's contents (2026-08-24 review). An
    odd number of triple quotes on a line toggles the state, so a one-line
    ``a = \"\"\"x\"\"\"`` opens and closes correctly. Files that never balance
    are rejected by tomllib before this runs."""
    states: list[bool] = []
    in_string = False
    for line in lines:
        states.append(in_string)
        if len(_TOML_TRIPLE_QUOTE.findall(line)) % 2 == 1:
            in_string = not in_string
    return states


def _toml_top_region_end(lines: list[str], inside: list[bool]) -> int:
    """Index of the first real section header — the end of the top-level
    region. Headers that are string content are skipped."""
    for index, line in enumerate(lines):
        if not inside[index] and _TOML_SECTION_LINE.match(line):
            return index
    return len(lines)


def pin_codex_model(home: Path, model: str, effort: str | None) -> PinResult:
    """Pin ``model`` (and optionally ``model_reasoning_effort``) at the top
    level of ``~/.codex/config.toml`` by surgical line replacement."""
    path = home / "config.toml"
    if not _valid_request(model, effort):
        return PinResult("refused_value", str(path), None)
    try:
        reject_links(path)
        prior: bytes | None
        try:
            prior = path.read_bytes()
        except FileNotFoundError:
            prior = None
        if prior is None:
            text = ""
            parsed: dict[str, object] = {}
        else:
            try:
                # Both live inside the guard: a config saved in a non-UTF-8
                # encoding raises UnicodeDecodeError (a ValueError), which
                # escaped every handler up to do_POST before 2026-08-24.
                text = prior.decode("utf-8", errors="strict")
                parsed = tomllib.loads(text)
            except (tomllib.TOMLDecodeError, ValueError):
                return PinResult("refused_unreadable", str(path), None)

        # A pin that an active profile silently overrides is a lie on disk.
        profile = parsed.get("profile")
        profiles = parsed.get("profiles")
        if isinstance(profile, str) and isinstance(profiles, dict):
            chosen = profiles.get(profile)
            if isinstance(chosen, dict) and (
                "model" in chosen or (effort is not None and "model_reasoning_effort" in chosen)
            ):
                return PinResult("refused_profile_active", str(path), None)

        lines = text.splitlines(keepends=True)
        inside = _toml_inside_string(lines)
        top_end = _toml_top_region_end(lines, inside)
        # Inserted lines follow the file's own newline convention.
        eol = "\r\n" if "\r\n" in text else "\n"

        def set_line(pattern: re.Pattern[str], assignment: str) -> None:
            nonlocal lines, top_end
            for index in range(top_end):
                # A matching line inside a multi-line string is that
                # string's text - replacing it would silently edit data.
                if not inside[index] and pattern.match(lines[index]):
                    lines[index] = assignment
                    return
            lines.insert(top_end, assignment)
            inside.insert(top_end, False)
            top_end += 1

        # json.dumps produces a valid TOML basic string for these values
        # (both are pre-validated against character allowlists above).
        set_line(_TOML_MODEL_LINE, f"model = {json.dumps(model)}{eol}")
        if effort is not None:
            set_line(
                _TOML_EFFORT_LINE,
                f"model_reasoning_effort = {json.dumps(effort)}{eol}",
            )
        new_text = "".join(lines)
        # The rewritten file must parse before it may exist.
        try:
            tomllib.loads(new_text)
        except tomllib.TOMLDecodeError:
            return PinResult("verify_failed", str(path), None)
        home.mkdir(parents=True, exist_ok=True)
        backup = _backup_then_swap(path, prior, new_text)
        landed = _codex_default(home)
        if landed.model != model or (effort is not None and landed.effort != effort):
            _roll_back(path, prior)
            return PinResult("verify_failed", str(path), backup)
        return PinResult("pinned", str(path), backup)
    except (OSError, ValueError):
        # ValueError covers the Unicode errors a hostile-looking config can
        # raise while being read or re-encoded; the original is untouched
        # at every point one can fire.
        return PinResult("io_error", str(path), None)


def claude_home(env: dict[str, str]) -> Path:
    override = env.get(ENV_CLAUDE_HOME)
    return Path(override) if override else Path.home() / ".claude"


def codex_home(env: dict[str, str]) -> Path:
    override = env.get(ENV_CODEX_HOME) or env.get(ENV_CODEX_HOME_NATIVE)
    return Path(override) if override else Path.home() / ".codex"


def recent_claude_project_dirs(
    env: dict[str, str], window_days: int = 30, head_lines: int = 5
) -> dict[str, Path]:
    """leaf -> working directory for recent Claude Code sessions, read from
    the head of each transcript (the cwd rides every record). The server
    resolves a project pin against THIS map — a posted path is never
    trusted. Newest session wins a leaf; only existing directories qualify."""
    projects_root = claude_home(env) / "projects"
    cutoff = time.time() - window_days * 86400
    candidates: list[tuple[float, Path]] = []
    try:
        for transcript in projects_root.rglob("*.jsonl"):
            if "subagents" in transcript.parts[:-1]:
                continue
            try:
                mtime = transcript.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                candidates.append((mtime, transcript))
    except OSError:
        return {}
    resolved: dict[str, Path] = {}
    # The newest few hundred transcripts carry every folder a person worked
    # in recently; opening more buys nothing and costs view latency.
    for _mtime, transcript in sorted(candidates, reverse=True)[:300]:
        try:
            with transcript.open("r", encoding="utf-8", errors="ignore") as handle:
                for _ in range(head_lines):
                    line = handle.readline()
                    if not line:
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    cwd = record.get("cwd") if isinstance(record, dict) else None
                    if not isinstance(cwd, str) or not cwd.strip():
                        continue
                    directory = Path(cwd)
                    leaf = directory.name
                    if not leaf or leaf in resolved:
                        break
                    if directory.is_dir():
                        resolved[leaf] = directory
                    break
        except OSError:
            continue
    return resolved
