"""Offline signed licensing core (src/practicegraph/analysis/license.py):
strict parse + signature verification, and the entitlement state machine.

Tokens are signed with a reference signer (cryptography, test-only) over the
SAME canonical body the parser verifies, so the tests exercise the real
crypto path end to end — a tampered field or a wrong key must fail to parse."""

from __future__ import annotations

import base64
import copy
from datetime import date

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from practicegraph.analysis.license import (
    EntitlementState,
    LicenseToken,
    canonical_body,
    entitlement,
    parse_license_artifact,
)

_PRIV = Ed25519PrivateKey.generate()
_PUB = _PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _signed(**overrides: object) -> dict[str, object]:
    """A valid license document, signed over its canonical body."""
    fields: dict[str, object] = {
        "schema": "practicegraph.license/1",
        "license_id": "lic-9f3c2a",
        "email": "user@example.com",
        "plan": "personal_annual",
        "seats": 1,
        "issued_on": "2026-07-21",
        "paid_through": "2027-07-21",
        "grace_days": 7,
        "machine_binding": "",
    }
    fields.update(overrides)
    signature = _PRIV.sign(canonical_body(fields))
    fields["signature"] = base64.b64encode(signature).decode("ascii")
    return fields


def test_round_trips_a_genuine_token() -> None:
    token = parse_license_artifact(_signed(), _PUB)
    assert token is not None
    assert token == LicenseToken(
        license_id="lic-9f3c2a",
        email="user@example.com",
        plan="personal_annual",
        seats=1,
        issued_on=date(2026, 7, 21),
        paid_through=date(2027, 7, 21),
        grace_days=7,
        machine_binding="",
    )
    assert token.read_only_cutoff() == date(2027, 7, 28)


def test_a_tampered_field_fails_verification() -> None:
    doc = _signed()
    doc["paid_through"] = "2099-01-01"  # extend the sub after signing
    assert parse_license_artifact(doc, _PUB) is None


def test_a_different_key_rejects_the_token() -> None:
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw
    )
    assert parse_license_artifact(_signed(), other) is None


def test_structural_deviations_are_rejected() -> None:
    extra = _signed()
    extra["surprise"] = 1
    assert parse_license_artifact(extra, _PUB) is None

    missing = _signed()
    del missing["seats"]
    assert parse_license_artifact(missing, _PUB) is None

    assert parse_license_artifact(_signed(schema="practicegraph.license/2"), _PUB) is None
    assert parse_license_artifact(_signed(plan="enterprise_unlimited"), _PUB) is None
    assert parse_license_artifact(_signed(email="not-an-email"), _PUB) is None
    assert parse_license_artifact(_signed(seats=0), _PUB) is None
    assert parse_license_artifact(_signed(grace_days=999), _PUB) is None
    assert parse_license_artifact(_signed(issued_on="2026-7-1"), _PUB) is None
    assert parse_license_artifact(_signed(machine_binding="ZZZZ"), _PUB) is None
    assert parse_license_artifact("not a dict", _PUB) is None


def test_machine_binding_accepts_16_hex() -> None:
    token = parse_license_artifact(_signed(machine_binding="a1b2c3d4e5f6a7b8"), _PUB)
    assert token is not None and token.machine_binding == "a1b2c3d4e5f6a7b8"


# ---- entitlement state machine -------------------------------------------

def _token(paid_through: str, grace_days: int = 7, binding: str = "") -> LicenseToken:
    return LicenseToken(
        license_id="lic-x", email="u@e.com", plan="personal_annual", seats=1,
        issued_on=date(2026, 1, 1), paid_through=date.fromisoformat(paid_through),
        grace_days=grace_days, machine_binding=binding,
    )


def test_active_grace_read_only_boundaries() -> None:
    tok = _token("2026-07-21", grace_days=7)  # cutoff 2026-07-28
    assert entitlement(tok, date(2026, 7, 21)) is EntitlementState.FREE  # == paid_through
    assert entitlement(tok, date(2026, 7, 20)) is EntitlementState.FREE
    assert entitlement(tok, date(2026, 7, 22)) is EntitlementState.FREE
    assert entitlement(tok, date(2026, 7, 28)) is EntitlementState.FREE  # == cutoff
    assert entitlement(tok, date(2026, 7, 29)) is EntitlementState.FREE


def test_old_trial_dates_never_restrict_free_access() -> None:
    start = date(2026, 7, 1)
    assert entitlement(None, date(2026, 7, 1), trial_started=start) is EntitlementState.FREE
    assert entitlement(None, date(2026, 7, 15), trial_started=start) is EntitlementState.FREE
    assert entitlement(None, date(2026, 7, 16), trial_started=start) is EntitlementState.FREE
    # No trial marker at all → unlicensed (the caller records first run in P2).
    assert entitlement(None, date(2026, 7, 1)) is EntitlementState.FREE


def test_enterprise_org_token_satisfies_licensing() -> None:
    # Wins even with no token, and even over an expired one.
    assert entitlement(None, date(2026, 7, 1), has_org_token=True) is EntitlementState.FREE
    expired = _token("2020-01-01")
    assert entitlement(expired, date(2026, 7, 1), has_org_token=True) is EntitlementState.FREE


def test_machine_binding_mismatch_is_invalid_but_defers_without_a_hash() -> None:
    bound = _token("2027-01-01", binding="a1b2c3d4e5f6a7b8")
    day = date(2026, 7, 1)
    assert entitlement(bound, day, machine_hash="a1b2c3d4e5f6a7b8") is EntitlementState.FREE
    assert entitlement(bound, day, machine_hash="ffffffffffffffff") is EntitlementState.FREE
    # No hash supplied → cannot check → defer (do not lock out).
    assert entitlement(bound, day) is EntitlementState.FREE


def test_determinism_same_inputs_same_state() -> None:
    tok = _token("2026-07-21")
    day = date(2026, 7, 25)
    assert entitlement(tok, day) == entitlement(copy.deepcopy(tok), day)
