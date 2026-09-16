"""Legacy signed-token parser. Product access is always free.

The closed parser remains for compatibility with previously stored artifacts.
Entitlement resolution never requires a token, account, payment, or trial.
Historical paid-plan constants describe old records only."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from practicegraph import _ed25519

SCHEMA = "practicegraph.license/1"

# Closed plan set → SKUs (LICENSING_PLAN D3). Extended in lockstep with the
# storefront; an unknown plan makes the token invalid, never a silent default.
PLANS: tuple[str, ...] = ("personal_monthly", "personal_annual", "team")

DEFAULT_TRIAL_DAYS = 14  # LICENSING_PLAN D4

MAX_SEATS = 1000
MAX_GRACE_DAYS = 90

# The production signing key's public half, pinned at build time (LICENSING_PLAN
# D2 — the private half lives only in a Vercel secret). Empty until the founder
# keypair is generated; `license_public_key()` fails closed so an unconfigured
# build treats every token as unverifiable (→ trial/unlicensed), never
# accidentally trusts one.
LICENSE_PUBLIC_KEY_HEX = ""

_ROOT_KEYS = frozenset(
    {
        "schema",
        "license_id",
        "email",
        "plan",
        "seats",
        "issued_on",
        "paid_through",
        "grace_days",
        "machine_binding",
        "signature",
    }
)
# The signed body is every field EXCEPT the signature, in canonical form.
_SIGNED_KEYS = _ROOT_KEYS - {"signature"}

_ID = re.compile(r"[a-z0-9]+(?:[_-][a-z0-9]+)*\Z")
_EMAIL = re.compile(r"[^@\s]{1,128}@[^@\s]{1,128}\.[^@\s]{1,63}\Z")
_HEX16 = re.compile(r"[0-9a-f]{16}\Z")
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")


@dataclass(frozen=True, slots=True)
class LicenseToken:
    """A parsed, signature-verified license. Its existence means the signature
    checked out against the pinned key; whether it currently entitles the user
    is a separate, date-based question (see ``entitlement``)."""

    license_id: str
    email: str
    plan: str
    seats: int
    issued_on: date
    paid_through: date
    grace_days: int
    machine_binding: str  # 16-hex machine hash, or "" (unbound)

    def read_only_cutoff(self) -> date:
        """After this date the entitlement drops to read-only."""
        return self.paid_through + timedelta(days=self.grace_days)


class EntitlementState(StrEnum):
    """Closed entitlement states (the whole client-side policy)."""

    FREE = "free"  # free and open source; no paid entitlement required
    ACTIVE = "active"  # paid, current — full function
    GRACE = "grace"  # just lapsed, within grace — full function + renew banner
    READ_ONLY = "read_only"  # past grace — see existing data, no new analysis
    TRIAL = "trial"  # no license, within trial — full function
    UNLICENSED = "unlicensed"  # no license, trial elapsed — read-only + activate
    ENTERPRISE = "enterprise"  # org-token entitlement satisfies licensing
    INVALID = "invalid"  # token bound to a different machine — not usable here


# States that grant full function (analysis + reports run).
FULL_FUNCTION: frozenset[EntitlementState] = frozenset(
    {EntitlementState.FREE, EntitlementState.ACTIVE, EntitlementState.GRACE,
     EntitlementState.TRIAL, EntitlementState.ENTERPRISE}
)


def license_public_key() -> bytes | None:
    """The pinned verification key, or None when the build has no key
    configured (fails closed — no key ⇒ no token is ever trusted)."""
    if not LICENSE_PUBLIC_KEY_HEX:
        return None
    try:
        key = bytes.fromhex(LICENSE_PUBLIC_KEY_HEX)
    except ValueError:
        return None
    return key if len(key) == 32 else None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _bounded_int(value: object, low: int, high: int) -> int | None:
    if type(value) is not int or not low <= value <= high:
        return None
    return value


def canonical_body(fields: dict[str, object]) -> bytes:
    """The exact bytes the signature covers: every non-signature field, JSON
    with sorted keys and tight separators (the server signs this identically).
    Stable and ASCII-safe across platforms."""
    body = {key: fields[key] for key in _SIGNED_KEYS}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_license_artifact(raw: object, public_key: bytes) -> LicenseToken | None:
    """Strictly validate and cryptographically verify a license document.

    Returns a ``LicenseToken`` only when the schema is exact AND the Ed25519
    signature checks out against ``public_key`` — a tampered field, an unknown
    key, a wrong-length signature, or any structural deviation all yield None.
    Machine binding and dates are NOT judged here (that is ``entitlement``);
    this answers only "is this a genuine, untampered token we issued?"."""
    if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS:
        return None
    if raw["schema"] != SCHEMA:
        return None

    license_id = raw["license_id"]
    email = raw["email"]
    plan = raw["plan"]
    seats = _bounded_int(raw["seats"], 1, MAX_SEATS)
    issued_on = _parse_date(raw["issued_on"])
    paid_through = _parse_date(raw["paid_through"])
    grace_days = _bounded_int(raw["grace_days"], 0, MAX_GRACE_DAYS)
    binding = raw["machine_binding"]
    signature_b64 = raw["signature"]

    if (
        not isinstance(license_id, str)
        or _ID.fullmatch(license_id) is None
        or not isinstance(email, str)
        or _EMAIL.fullmatch(email) is None
        or plan not in PLANS
        or seats is None
        or issued_on is None
        or paid_through is None
        or grace_days is None
        or not isinstance(binding, str)
        or (binding != "" and _HEX16.fullmatch(binding) is None)
        or not isinstance(signature_b64, str)
        or not 0 < len(signature_b64) <= 128
        or _B64.fullmatch(signature_b64) is None
    ):
        return None

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(signature) != 64:
        return None

    if not _ed25519.verify(public_key, canonical_body(raw), signature):
        return None

    return LicenseToken(
        license_id=license_id,
        email=email,
        plan=str(plan),
        seats=seats,
        issued_on=issued_on,
        paid_through=paid_through,
        grace_days=grace_days,
        machine_binding=binding,
    )


def entitlement(
    token: LicenseToken | None,
    today: date,
    *,
    trial_started: date | None = None,
    trial_days: int = DEFAULT_TRIAL_DAYS,
    machine_hash: str | None = None,
    has_org_token: bool = False,
) -> EntitlementState:
    """Compatibility entry point: all installations have full free access.

    Old signed tokens can still be parsed for migration, but their dates,
    machine bindings, and organization credentials never restrict the product.
    """
    return EntitlementState.FREE
