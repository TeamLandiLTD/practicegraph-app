"""Bounded, read-only checks of a user-selected local folder. Contents stay transient."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import tomllib
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 128 * 1024
FILES = (
    "package.json",
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "CONTRIBUTING.md",
    "docs/TESTING.md",
)
LOCKS = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb")
STATES = ("absent", "read", "unreadable", "too_large", "outside_scope", "invalid", "time_limit")
CHECKS = ("test", "lint", "typecheck", "check")


def identity_candidates(root: Path, supplied: str) -> list[str]:
    # Existing adapters hash exact spelling. Only canonical equivalents of the
    # explicitly selected directory are candidates; never map other worktrees.
    spellings = {str(root), root.as_posix(), supplied.rstrip("/\\")}
    if os.name == "nt":
        for value in tuple(spellings):
            spellings.add(value[:1].lower() + value[1:])
            spellings.add(value[:1].upper() + value[1:])
    return sorted({hashlib.sha256(value.encode()).hexdigest()[:16] for value in spellings if value})


def _read(root: Path, relative: str, deadline: float) -> tuple[str, bytes | None]:
    if time.monotonic() > deadline:
        return "time_limit", None
    target = root / relative
    try:
        # Refuse every reparse point beneath the selected root, even if its
        # current target appears inside it. Recheck the opened file's identity.
        for part in (target, *target.parents):
            if part == root:
                break
            if part.is_symlink() or part.is_junction():
                return "outside_scope", None
        if not target.resolve().is_relative_to(root):
            return "outside_scope", None
        before = target.stat()
        if not stat.S_ISREG(before.st_mode):
            return "outside_scope", None
        if before.st_size > MAX_FILE_BYTES:
            return "too_large", None
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return "outside_scope", None
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return "too_large", None
        if not target.resolve().is_relative_to(root):
            return "outside_scope", None
        return "read", raw
    except FileNotFoundError:
        return "absent", None
    except (OSError, ValueError, RuntimeError):
        return "unreadable", None


def inspect_project(path: object) -> dict[str, Any]:
    if (
        not isinstance(path, str)
        or not 1 <= len(path) <= 2048
        or path.startswith(("\\\\", "//"))
        or not Path(path).is_absolute()
    ):
        raise ValueError("invalid_project")
    try:
        root = Path(path).resolve(strict=True)
        if not root.is_dir() or root.parent == root or str(root).startswith(("\\\\", "//")):
            raise ValueError("invalid_project")
    except (OSError, RuntimeError):
        raise ValueError("invalid_project") from None
    deadline = time.monotonic() + 2
    files = []
    stacks: set[str] = set()
    checks: list[str] = []
    pytest_config = False
    documented = False
    for name in FILES:
        state, raw = _read(root, name, deadline)
        digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
        if raw is not None:
            try:
                text = raw.decode("utf-8-sig")
                if name == "package.json":
                    value = json.loads(text)
                    if not isinstance(value, dict):
                        raise ValueError()
                    scripts = value.get("scripts", {})
                    if not isinstance(scripts, dict):
                        raise ValueError()
                    stacks.add("javascript")
                    checks = [
                        key
                        for key in CHECKS
                        if isinstance(scripts.get(key), str) and bool(scripts[key].strip())
                    ]
                elif name == "pyproject.toml":
                    value = tomllib.loads(text)
                    stacks.add("python")
                    pytest_config = isinstance(value.get("tool", {}), dict) and (
                        "pytest" in value.get("tool", {})
                    )
                elif name in ("pytest.ini", "tox.ini"):
                    stacks.add("python")
                    pytest_config = pytest_config or bool(re.search(r"\[pytest\]", text))
                else:
                    documented = documented or bool(
                        re.search(
                            r"\b(pytest|npm (?:run )?test|pnpm (?:run )?test|yarn test|bun test)\b",
                            text,
                            re.IGNORECASE,
                        )
                    )
            except (UnicodeError, ValueError, TypeError, RecursionError):
                state = "invalid"
        files.append({"name": name, "state": state, "sha256": digest})
    locks = []
    for name in LOCKS:
        target = root / name
        try:
            if not target.is_symlink() and not target.is_junction() and target.is_file():
                locks.append(name)
        except OSError:
            pass
    return {
        "project_ids": identity_candidates(root, path),
        "stacks": sorted(stacks),
        "files": files,
        "declared_checks": checks,
        "pytest_config": pytest_config,
        "verification_mentioned": documented,
        "lockfiles": locks,
    }
