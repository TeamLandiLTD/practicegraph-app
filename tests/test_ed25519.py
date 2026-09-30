"""The vendored pure-Python Ed25519 verifier (src/practicegraph/_ed25519.py),
cross-checked against a reference implementation (cryptography, test-only).

This is the crypto the signed update check rests on, so it is proven against
real keypairs and real signatures — accept the genuine, reject every tamper —
rather than trusted by inspection."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from practicegraph import _ed25519


def _keypair() -> tuple[Ed25519PrivateKey, bytes]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, pub


def test_accepts_genuine_signatures_from_a_reference_signer() -> None:
    for _ in range(25):
        priv, pub = _keypair()
        message = os.urandom(os.urandom(1)[0])  # 0..255 random bytes
        signature = priv.sign(message)
        assert _ed25519.verify(pub, message, signature) is True


def test_rejects_a_tampered_message() -> None:
    priv, pub = _keypair()
    message = b"license body v1"
    signature = priv.sign(message)
    assert _ed25519.verify(pub, message, signature) is True
    assert _ed25519.verify(pub, b"license body v2", signature) is False


def test_rejects_a_tampered_signature() -> None:
    priv, pub = _keypair()
    message = b"the signed token"
    signature = bytearray(priv.sign(message))
    signature[0] ^= 0x01  # flip one bit
    assert _ed25519.verify(pub, message, bytes(signature)) is False


def test_rejects_a_signature_under_a_different_key() -> None:
    priv, _pub = _keypair()
    _other_priv, other_pub = _keypair()
    message = b"who signed this?"
    signature = priv.sign(message)
    assert _ed25519.verify(other_pub, message, signature) is False


@pytest.mark.parametrize(
    "pub_len,sig_len",
    [(31, 64), (33, 64), (32, 63), (32, 65), (0, 64), (32, 0)],
)
def test_rejects_wrong_length_inputs(pub_len: int, sig_len: int) -> None:
    assert _ed25519.verify(b"\x00" * pub_len, b"m", b"\x00" * sig_len) is False


def test_rejects_garbage_never_raises() -> None:
    # A 32/64-byte pair that is not a valid key/point must return False, not err.
    assert _ed25519.verify(b"\xff" * 32, b"m", b"\x00" * 64) is False
    assert _ed25519.verify(os.urandom(32), b"m", os.urandom(64)) in (True, False)


def test_base_point_is_the_rfc8032_generator() -> None:
    # y = 4/5 mod p is the canonical Ed25519 base point (0x6666...6658).
    assert _ed25519._B[1] == (4 * _ed25519._inv(5)) % _ed25519._P
