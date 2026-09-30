"""Private local state and atomic configuration writes. Permission failures close."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path

from practicegraph import winsec


def reject_links(path: Path) -> None:
    """Do not read/write through symlinks, Windows junctions, or hard-linked files."""
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            # macOS exposes /var and /tmp through root-owned aliases. Only
            # trust ancestor aliases in a root-owned, non-writable parent.
            parent = component.parent.stat()
            if (
                os.name != "nt"
                and component != path
                and info.st_uid == 0
                and parent.st_uid == 0
                and not parent.st_mode & 0o022
            ):
                continue
            raise OSError("linked configuration path refused")
        if component == path and stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise OSError("hard-linked configuration file refused")


def private_directory(path: Path) -> None:
    reject_links(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        if not winsec.restrict_to_owner_and_admins(path):
            raise PermissionError("cannot secure private directory")
    else:
        path.chmod(0o700)


def atomic_write(path: Path, data: bytes, *, permissions_from: Path | None = None) -> None:
    """Restrict an empty staging file before writing, then atomically replace.

    Backups use the original file's ACL/mode. Fresh private files default to
    owner/admin access on Windows and 0600 on POSIX. No broad-permission window
    exposes the payload, and failed staging leaves the destination untouched.
    """
    reject_links(path)
    if permissions_from is not None:
        reject_links(permissions_from)
    descriptor, name = tempfile.mkstemp(prefix=".pg-private-", dir=path.parent)
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name == "nt":
                if not winsec.restrict_to_owner_and_admins(staging):
                    raise PermissionError("cannot secure staging file")
                if permissions_from is not None and not winsec.copy_file_permissions(
                    permissions_from, staging
                ):
                    raise PermissionError("cannot preserve file permissions")
            elif permissions_from is not None:
                staging.chmod(stat.S_IMODE(permissions_from.stat().st_mode) & 0o777)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        reject_links(path)
        os.replace(staging, path)
    finally:
        with contextlib.suppress(OSError):
            staging.unlink(missing_ok=True)


def require_personal_context(data_dir: Path, env: dict[str, str]) -> None:
    """The personal graph must never run in SYSTEM or the legacy shared store."""
    shared = Path(env.get("ProgramData") or r"C:\ProgramData") / "PracticeGraph"
    if winsec.is_local_system() or (
        os.name == "nt" and data_dir.resolve().is_relative_to(shared.resolve())
    ):
        raise PermissionError("personal data requires a per-user installation")
