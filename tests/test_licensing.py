"""License local state + entitlement resolution (src/practicegraph/licensing.py,
LICENSING_PLAN Phase 2): the DPAPI-protected token store, the per-install
machine id and trial marker (read/write split), and resolve_entitlement tying
it to the pure core."""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from practicegraph.analysis.license import EntitlementState, canonical_body
from practicegraph.licensing import (
    ensure_machine_id,
    ensure_trial_started,
    load_license_token,
    machine_id,
    resolve_entitlement,
    store_license_token,
    trial_started,
)
from practicegraph.store import Store

_PRIV = Ed25519PrivateKey.generate()
_PUB = _PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def _license_doc(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "schema": "practicegraph.license/1",
        "license_id": "lic-abc123",
        "email": "buyer@example.com",
        "plan": "personal_annual",
        "seats": 1,
        "issued_on": "2026-07-21",
        "paid_through": "2027-07-21",
        "grace_days": 7,
        "machine_binding": "",
    }
    fields.update(overrides)
    fields["signature"] = base64.b64encode(_PRIV.sign(canonical_body(fields))).decode()
    return fields


# ---- machine id ----------------------------------------------------------

def test_machine_id_read_write_split(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert machine_id(store) is None  # pure read: nothing seeded, no write
    seeded = ensure_machine_id(store)
    assert len(seeded) == 16 and int(seeded, 16) >= 0  # 16-hex
    assert machine_id(store) == seeded
    assert ensure_machine_id(store) == seeded  # idempotent, stable


# ---- trial marker --------------------------------------------------------

def test_trial_marker_read_write_split(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert trial_started(store) is None
    start = ensure_trial_started(store, date(2026, 7, 21))
    assert start == date(2026, 7, 21)
    assert trial_started(store) == date(2026, 7, 21)
    # Idempotent: a later call keeps the original start (trial can't be reset
    # by re-entering the function).
    assert ensure_trial_started(store, date(2026, 8, 1)) == date(2026, 7, 21)


# ---- protected token store ----------------------------------------------

def test_token_store_round_trips_through_dpapi(tmp_path: Path) -> None:
    store_license_token(tmp_path, _license_doc())
    assert (tmp_path / "license.bin").is_file()
    token = load_license_token(tmp_path, _PUB)
    assert token is not None
    assert token.license_id == "lic-abc123"
    assert token.paid_through == date(2027, 7, 21)


def test_load_fails_closed_without_a_key(tmp_path: Path) -> None:
    store_license_token(tmp_path, _license_doc())
    # No pinned key -> trust nothing, even with a genuine file present.
    assert load_license_token(tmp_path, None) is None


def test_load_rejects_a_tampered_stored_token(tmp_path: Path) -> None:
    doc = _license_doc()
    doc["paid_through"] = "2099-01-01"  # extend after signing
    store_license_token(tmp_path, doc)
    assert load_license_token(tmp_path, _PUB) is None


def test_load_absent_token_is_none(tmp_path: Path) -> None:
    assert load_license_token(tmp_path, _PUB) is None


# ---- resolution ----------------------------------------------------------

def test_resolve_active_from_a_stored_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store_license_token(tmp_path, _license_doc())
    state = resolve_entitlement(
        tmp_path, store, date(2026, 8, 1), has_org_token=False, public_key=_PUB
    )
    assert state is EntitlementState.FREE


def test_resolve_trial_then_unlicensed_without_a_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ensure_trial_started(store, date(2026, 7, 1))
    assert resolve_entitlement(
        tmp_path, store, date(2026, 7, 10), has_org_token=False, public_key=_PUB
    ) is EntitlementState.FREE
    assert resolve_entitlement(
        tmp_path, store, date(2026, 8, 1), has_org_token=False, public_key=_PUB
    ) is EntitlementState.FREE


def test_resolve_enterprise_wins(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert resolve_entitlement(
        tmp_path, store, date(2026, 7, 1), has_org_token=True, public_key=_PUB
    ) is EntitlementState.FREE


def test_resolve_binds_to_this_machine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    mid = ensure_machine_id(store)
    # A token bound to THIS machine resolves; one bound elsewhere is invalid.
    store_license_token(tmp_path, _license_doc(machine_binding=mid))
    assert resolve_entitlement(
        tmp_path, store, date(2026, 8, 1), has_org_token=False, public_key=_PUB
    ) is EntitlementState.FREE

    store_license_token(tmp_path, _license_doc(machine_binding="0000000000000000"))
    assert resolve_entitlement(
        tmp_path, store, date(2026, 8, 1), has_org_token=False, public_key=_PUB
    ) is EntitlementState.FREE
