"""Windows-native secret protection (NFR-SEC-1).

On Windows, secrets are protected at rest with DPAPI in machine scope so no
plaintext credential ever touches disk, and only processes on this machine
can recover it. On other platforms (the portable core, NFR-CMP-1) the
fallback is the caller restricting file permissions — documented seam.

Machine-scope DPAPI keeps a blob off-machine-readable, but any *local* process
can decrypt it, so a secret file in a shared data directory (%ProgramData%)
must ALSO carry a restrictive DACL — otherwise a second local user simply reads
the blob and decrypts it. ``restrict_to_owner_and_admins`` sets that DACL: the
file becomes readable only by SYSTEM, Administrators, and the user that wrote
it. It is the Windows counterpart to the POSIX ``chmod(0o600)`` seam.
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = (
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        )

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01
    _CRYPTPROTECT_LOCAL_MACHINE = 0x04
    _FLAGS = _CRYPTPROTECT_UI_FORBIDDEN | _CRYPTPROTECT_LOCAL_MACHINE

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    # Explicit signatures: without these, ctypes assumes 32-bit int args/returns
    # and truncates 64-bit pointers/handles, which silently corrupts the ACL
    # calls on a 64-bit build.
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.LocalFree.argtypes = (wintypes.LPVOID,)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.ConvertSidToStringSidW.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.ULONG),
    )
    _advapi32.GetSecurityDescriptorDacl.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    )
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    )

    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SDDL_REVISION_1 = 1

    def copy_file_permissions(source: Path, destination: Path) -> bool:
        """Copy the source's DACL without inheriting a broader parent DACL."""
        descriptor = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        result = _advapi32.GetNamedSecurityInfoW(
            str(source),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result != 0:
            return False
        try:
            # A NULL DACL means unrestricted access; never propagate it to a backup.
            if not dacl:
                return restrict_to_owner_and_admins(destination)
            return bool(
                _advapi32.SetNamedSecurityInfoW(
                    str(destination),
                    _SE_FILE_OBJECT,
                    _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    dacl,
                    None,
                )
                == 0
            )
        finally:
            _kernel32.LocalFree(descriptor)

    def _current_user_sid() -> str | None:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            return None
        try:
            size = wintypes.DWORD(0)
            _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
            if size.value == 0:
                return None
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, size, ctypes.byref(size)
            ):
                return None
            # TOKEN_USER begins with SID_AND_ATTRIBUTES whose first member is the
            # PSID — read that pointer directly.
            sid_ptr = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID)).contents
            string_sid = wintypes.LPWSTR()
            if not _advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(string_sid)):
                return None
            try:
                return string_sid.value
            finally:
                _kernel32.LocalFree(string_sid)
        finally:
            _kernel32.CloseHandle(token)

    def is_local_system() -> bool:
        """True when this process runs as LocalSystem (S-1-5-18). The service
        uses it to refuse per-user, PATH-resolved subprocess spawns (restyle),
        which under SYSTEM would be a planted-binary privilege escalation."""
        return _current_user_sid() == "S-1-5-18"

    def restrict_to_owner_and_admins(path: Path) -> bool:
        """Lock a file/dir to SYSTEM + Administrators + the current user, with
        inheritance removed. Best-effort: returns True on success, False on any
        failure (the caller treats it as defense-in-depth over the installer's
        directory ACL, never a hard dependency)."""
        aces = ["(A;OICI;FA;;;SY)", "(A;OICI;FA;;;BA)"]
        sid = _current_user_sid()
        if sid:
            aces.append(f"(A;OICI;FA;;;{sid})")
        sddl = "D:PAI" + "".join(aces)
        descriptor = wintypes.LPVOID()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            return False
        try:
            present = wintypes.BOOL()
            dacl = wintypes.LPVOID()
            defaulted = wintypes.BOOL()
            if not _advapi32.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            ):
                return False
            status = _advapi32.SetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            return bool(status == 0)  # ERROR_SUCCESS; ctypes result is Any
        finally:
            _kernel32.LocalFree(descriptor)

    def _consume_blob(blob: _DataBlob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(blob.pbData)

    def protect(data: bytes) -> bytes:
        """Encrypt with the machine DPAPI key. Raises OSError on failure."""
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, _FLAGS, ctypes.byref(blob_out)
        )
        if not ok:
            raise OSError("dpapi_protect_failed")
        return _consume_blob(blob_out)

    def unprotect(data: bytes) -> bytes:
        """Decrypt a machine-scope DPAPI blob. Raises OSError on failure."""
        buffer = ctypes.create_string_buffer(data, len(data))
        blob_in = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError("dpapi_unprotect_failed")
        return _consume_blob(blob_out)

else:

    def copy_file_permissions(source: Path, destination: Path) -> bool:
        """POSIX callers use chmod instead."""
        return False

    def protect(data: bytes) -> bytes:
        return data

    def unprotect(data: bytes) -> bytes:
        return data

    def is_local_system() -> bool:
        """No LocalSystem account off Windows."""
        return False

    def restrict_to_owner_and_admins(path: Path) -> bool:
        """No-op off Windows: callers restrict POSIX files with chmod(0o600)."""
        return False
