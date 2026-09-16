"""Legacy local license storage; free access requires no license state.

Token and marker helpers remain for old records and migration tests. The current
resolve_entitlement returns FREE without reading tokens or creating identifiers.
No background trial or purchase flow uses these helpers."""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import date
from pathlib import Path

from practicegraph import winsec
from practicegraph.analysis.license import (
    EntitlementState,
    LicenseToken,
    parse_license_artifact,
)
from practicegraph.store import Store

LICENSE_FILE_NAME = "license.bin"  # DPAPI-protected token, beside org_token.bin

_META_MACHINE_ID = "machine_id:v1"
_META_TRIAL_STARTED = "trial_started:v1"

_HEX16 = re.compile(r"[0-9a-f]{16}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


# ---- machine id (seat binding) -------------------------------------------
# A per-install RANDOM 16-hex id — deliberately NOT a hardware serial or the
# Windows MachineGuid: privacy-first (our own opaque value, never a real
# machine identifier), stable across restarts, and reset-on-wipe (a wiped
# machine re-activating consumes a seat — the seat-enforcement mechanism).

def machine_id(store: Store) -> str | None:
    """The persisted machine id, or None when not yet seeded. Pure read —
    never writes (safe from a read-only per-user context)."""
    value = store.meta_get(_META_MACHINE_ID)
    return value if value and _HEX16.fullmatch(value) else None


def ensure_machine_id(store: Store) -> str:
    """Return the machine id, generating and persisting one on first call.
    Write path — the service tick owns it (writable context)."""
    existing = machine_id(store)
    if existing is not None:
        return existing
    fresh = secrets.token_hex(8)  # 16 hex chars
    store.meta_set(_META_MACHINE_ID, fresh)
    return fresh


# ---- trial marker --------------------------------------------------------

def trial_started(store: Store) -> date | None:
    """The recorded trial-start date, or None. Pure read."""
    value = store.meta_get(_META_TRIAL_STARTED)
    if not value or _DATE.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def ensure_trial_started(store: Store, today: date) -> date:
    """Record today as the trial start if none is set yet; return the start.
    Write path — first service tick / init. Idempotent."""
    existing = trial_started(store)
    if existing is not None:
        return existing
    store.meta_set(_META_TRIAL_STARTED, today.isoformat())
    return today


# ---- protected token store (mirror store_org_token) ----------------------

def store_license_token(data_dir: Path, document: dict[str, object]) -> None:
    """Persist the signed license document DPAPI-protected + owner-locked, the
    exact discipline as the org token (NFR-SEC-1): machine-scope DPAPI plus a
    DACL/0600 so a second local user in a shared data dir can't lift it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LICENSE_FILE_NAME
    payload = json.dumps(document, sort_keys=True).encode("utf-8")
    path.write_bytes(winsec.protect(payload))
    if os.name == "nt":
        winsec.restrict_to_owner_and_admins(path)
    else:
        path.chmod(0o600)


def load_license_token(
    data_dir: Path, public_key: bytes | None
) -> LicenseToken | None:
    """Load + DPAPI-unprotect + strictly verify the stored token. None when
    absent, unreadable, tampered, or when no pinned key is configured (fails
    closed — an unconfigured build trusts no token)."""
    if public_key is None:
        return None
    path = data_dir / LICENSE_FILE_NAME
    if not path.is_file():
        return None
    try:
        raw = winsec.unprotect(path.read_bytes())
        document = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return parse_license_artifact(document, public_key)


# ---- resolution ----------------------------------------------------------

def resolve_entitlement(
    data_dir: Path,
    store: Store,
    today: date,
    *,
    has_org_token: bool,
    public_key: bytes | None = None,
) -> EntitlementState:
    """Free access without reading credentials or creating a trial marker."""
    return EntitlementState.FREE
