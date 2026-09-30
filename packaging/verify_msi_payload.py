"""Verify actual MSI payload paths and bytes; uses Windows APIs, not removed msilib."""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

UNPACKAGED = {"install.ps1", "uninstall.ps1"}


def msi_files(path: Path) -> dict[str, tuple[str, int]]:
    api = ctypes.WinDLL("msi", use_last_error=True)
    H = wintypes.UINT
    api.MsiOpenDatabaseW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(H))
    api.MsiDatabaseOpenViewW.argtypes = (H, wintypes.LPCWSTR, ctypes.POINTER(H))
    api.MsiViewExecute.argtypes = (H, H)
    api.MsiViewFetch.argtypes = (H, ctypes.POINTER(H))
    api.MsiRecordGetStringW.argtypes = (
        H,
        wintypes.UINT,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    api.MsiCloseHandle.argtypes = (H,)
    database = H()
    if api.MsiOpenDatabaseW(str(path.resolve()), None, ctypes.byref(database)):
        raise ValueError("cannot open MSI")

    def query(sql, columns):
        view = H()
        if api.MsiDatabaseOpenViewW(database, sql, ctypes.byref(view)):
            raise ValueError("invalid MSI table")
        rows = []
        try:
            if api.MsiViewExecute(view, 0):
                raise ValueError("cannot query MSI")
            while True:
                record = H()
                status = api.MsiViewFetch(view, ctypes.byref(record))
                if status == 259:
                    break
                if status:
                    raise ValueError("cannot read MSI record")
                try:
                    row = []
                    for column in range(1, columns + 1):
                        size = wintypes.DWORD()
                        api.MsiRecordGetStringW(record, column, None, ctypes.byref(size))
                        size.value += 1
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if api.MsiRecordGetStringW(record, column, buffer, ctypes.byref(size)):
                            raise ValueError("cannot read MSI field")
                        row.append(buffer.value)
                    rows.append(row)
                finally:
                    api.MsiCloseHandle(record)
        finally:
            api.MsiCloseHandle(view)
        return rows

    try:
        directories = {
            key: (parent, name)
            for key, parent, name in query(
                "SELECT `Directory`,`Directory_Parent`,`DefaultDir` FROM `Directory`", 3
            )
        }
        components = dict(query("SELECT `Component`,`Directory_` FROM `Component`", 2))

        def relative_directory(key):
            parts, visited = [], set()
            while key != "INSTALLFOLDER":
                if key in visited or key not in directories:
                    raise ValueError("MSI file is outside INSTALLFOLDER")
                visited.add(key)
                parent, name = directories[key]
                name = name.split(":")[0].split("|")[-1]
                if name != ".":
                    parts.append(name)
                key = parent
            return Path(*reversed(parts))

        files = {}
        for key, name, size, component in query(
            "SELECT `File`,`FileName`,`FileSize`,`Component_` FROM `File`", 4
        ):
            name = (relative_directory(components[component]) / name.split("|")[-1]).as_posix()
            if name in files:
                raise ValueError("duplicate MSI destination")
            files[name] = (key, int(size))
        return files
    finally:
        api.MsiCloseHandle(database)


def extracted_payload(msi: Path, destination: Path) -> dict[str, Path]:
    files = msi_files(msi)
    subprocess.run(
        [
            "wix",
            "msi",
            "decompile",
            str(msi),
            "-x",
            str(destination / "files"),
            "-o",
            str(destination / "package.wxs"),
        ],
        check=True,
        capture_output=True,
    )
    return {name: destination / "files/File" / key for name, (key, _) in files.items()}


def verify(msi: Path, bundle: Path) -> None:
    expected = {
        p.relative_to(bundle).as_posix(): p
        for p in bundle.rglob("*")
        if p.is_file() and p.name not in UNPACKAGED and "__pycache__" not in p.parts
    }
    with tempfile.TemporaryDirectory(prefix="pg-msi-verify-") as temporary:
        actual = extracted_payload(msi, Path(temporary))
        if set(expected) != set(actual):
            raise ValueError(f"MSI file mismatch: {sorted(set(expected) ^ set(actual))}")
        for name, path in actual.items():
            if (
                hashlib.sha256(path.read_bytes()).digest()
                != hashlib.sha256(expected[name].read_bytes()).digest()
            ):
                raise ValueError(f"MSI payload differs from staged bytes: {name}")
    print(f"payload verified: {len(expected)} exact paths and file hashes")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
