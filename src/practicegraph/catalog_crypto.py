"""Signed encrypted editions. Reader keys deter casual scraping; they are NOT secrets.

The open client can decrypt/copy editions. Only the publisher signing key is
confidential. No credentials, registration, or additional network request occur.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCHEMA = "practicegraph.encrypted-catalog/1"
# Raised 2026-09-15 for the full-matrix token-price feed (a few hundred offers
# with retained history); the envelope bound follows base64 growth plus framing.
MAX_PLAINTEXT_BYTES = 4 * 1024 * 1024
MAX_ENVELOPE_BYTES = 5_700_000
# Populated during publisher initialization. Keys are trusted locally, never
# accepted from a downloaded envelope. Values: (recoverable reader key, signing PUBLIC key).
READER_KEYS: dict[str, tuple[bytes, bytes]] = {
    "teamlandi-2026-09": (
        # Public reader material, deliberately recoverable for scraping deterrence.
        bytes.fromhex("e132cb344a1cc16a1267c1d2b48cdd827fa5bbe53c7a853b6a05edbfae5c5daa"),
        # Publisher verification key; the private signing seed never ships.
        bytes.fromhex("684f72bec547077b59cab83aa0b4501944a9183e35bda312900d0fc405d6854c"),
    ),
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _decode(value: object, size: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise ValueError("invalid encrypted catalog")
    raw = base64.b64decode(value, validate=True)
    if _b64(raw) != value or (size is not None and len(raw) != size):
        raise ValueError("invalid encrypted catalog")
    return raw


def seal(
    document: dict[str, Any], channel: str, key_id: str, signing_seed: bytes
) -> dict[str, Any]:
    """Skill-invoked authoring primitive; a vetted AEAD and independent signature."""
    from practicegraph.content import edition_version

    version = edition_version(channel, document)
    reader_key, public_key = READER_KEYS[key_id]
    signer = Ed25519PrivateKey.from_private_bytes(signing_seed)
    if signer.public_key().public_bytes_raw() != public_key:
        raise ValueError("signing key does not match the client publisher key")
    plaintext = _canonical(document)
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise ValueError("catalog exceeds plaintext limit")
    header = {"schema": SCHEMA, "channel": channel, "edition_version": version, "key_id": key_id}
    nonce = os.urandom(12)  # Never deterministic, even when resealing the same edition.
    payload = {
        **header,
        "nonce": _b64(nonce),
        "ciphertext": _b64(AESGCM(reader_key).encrypt(nonce, plaintext, _canonical(header))),
    }
    return {**payload, "signature": _b64(signer.sign(_canonical(payload)))}


def open_catalog(
    document: object, channel: str, *, require_sealed: bool = False
) -> dict[str, Any]:
    """Open a sealed edition, or pass a plain draft through when the caller allows it.

    ``require_sealed`` is the network boundary: every public pull sets it, so a
    document that reaches the client unsigned is rejected even though the
    transport was TLS. Without it the trust anchor for skills prompts, playbooks
    and one-click model pins would be control of the content host, not the
    pinned publisher key (open-source readiness review, 2026-09-16). Authoring
    and release tooling open plain drafts deliberately and leave it off.
    Encrypted-shaped failures never fall back to plain either way.
    """
    if not isinstance(document, dict):
        raise ValueError("invalid catalog")
    if document.get("schema") != SCHEMA:
        # Unknown transport versions must not be interpreted as plaintext.
        if str(document.get("schema", "")).startswith("practicegraph.encrypted-catalog/"):
            raise ValueError("unsupported encrypted catalog")
        if require_sealed:
            raise ValueError("unsigned catalog")
        return document
    try:
        if (
            set(document)
            != {
                "schema",
                "channel",
                "edition_version",
                "key_id",
                "nonce",
                "ciphertext",
                "signature",
            }
            or document["channel"] != channel
            or len(_canonical(document)) > MAX_ENVELOPE_BYTES
        ):
            raise ValueError
        key_id = document["key_id"]
        if not isinstance(key_id, str) or key_id not in READER_KEYS:
            raise ValueError
        reader_key, public_key = READER_KEYS[key_id]
        payload = {key: value for key, value in document.items() if key != "signature"}
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            _decode(document["signature"], 64),
            _canonical(payload),
        )
        header = {key: document[key] for key in ("schema", "channel", "edition_version", "key_id")}
        ciphertext = _decode(document["ciphertext"])
        if not 16 <= len(ciphertext) <= MAX_PLAINTEXT_BYTES + 16:
            raise ValueError
        plaintext = AESGCM(reader_key).decrypt(
            _decode(document["nonce"], 12),
            ciphertext,
            _canonical(header),
        )
        result = json.loads(plaintext)
        from practicegraph.content import edition_version

        if edition_version(channel, result) != document["edition_version"]:
            raise ValueError
        return dict(result)
    except (
        KeyError,
        TypeError,
        ValueError,
        binascii.Error,
        InvalidTag,
        InvalidSignature,
        RecursionError,
    ):
        raise ValueError("invalid encrypted catalog") from None
