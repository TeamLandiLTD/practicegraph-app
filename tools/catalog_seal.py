"""Skill-invoked catalog encryption. Validates, signs, encrypts and verifies; never deploys."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from tools.content_release import _write, validate_draft

from practicegraph.catalog_crypto import MAX_ENVELOPE_BYTES, READER_KEYS, open_catalog, seal
from practicegraph.content import CHANNELS, canonical_bytes, edition_version
from practicegraph.secureio import atomic_write, private_directory, reject_links


def seal_draft(
    channel: str, draft: Path, output: Path, key_path: Path, key_id: str
) -> dict[str, object]:
    app = Path(__file__).resolve().parents[1]
    if draft.resolve() == output.resolve():
        raise ValueError("draft and encrypted output must be different files")
    # Signing seeds must never be placed in application or public output trees.
    for public_root in (app, output.parent.resolve(), app.parent / "practicegraph.dev"):
        if key_path.resolve() == public_root or public_root in key_path.resolve().parents:
            raise ValueError("signing key must stay outside application and public output trees")
    reject_links(key_path)
    if key_id not in READER_KEYS:
        raise ValueError("publisher key is not installed in this client")
    previous = output if output.exists() else None
    review = validate_draft(channel, draft, previous)
    document = json.loads(draft.read_bytes())
    # Immutable editions: retrying a skill does not create fresh ciphertext under
    # an already-used version, and a changed document cannot reuse that version.
    if previous:
        with previous.open("rb") as stream:
            old_raw = stream.read(MAX_ENVELOPE_BYTES + 1)
        if len(old_raw) > MAX_ENVELOPE_BYTES:
            raise ValueError("existing catalog is too large")
        prior = json.loads(old_raw)
        if prior.get("schema") == "practicegraph.encrypted-catalog/1" and (
            open_catalog(prior, channel) == document and prior["key_id"] == key_id
        ):
            return {
                **review,
                "encrypted": True,
                "reused": True,
                "sha256": hashlib.sha256(old_raw).hexdigest(),
            }
        if edition_version(channel, open_catalog(prior, channel)) == edition_version(
            channel, document
        ):
            raise ValueError(
                "changing delivery format or reader key requires a new edition version"
            )
    with key_path.open("rb") as stream:
        encoded_seed = stream.read(129)
    if len(encoded_seed.strip()) != 64:
        raise ValueError("invalid private signing-key file")
    try:
        seed = bytes.fromhex(encoded_seed.decode("ascii").strip())
    except ValueError:
        raise ValueError("invalid private signing-key file") from None
    encrypted = seal(document, channel, key_id, seed)
    if open_catalog(encrypted, channel) != document:
        raise ValueError("client round-trip verification failed")
    raw = canonical_bytes(encrypted)
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("encrypted catalog is too large")
    output.parent.mkdir(parents=True, exist_ok=True)
    _write(output, raw)
    return {**review, "encrypted": True, "reused": False, "sha256": hashlib.sha256(raw).hexdigest()}


def read_private(channel: str, source: Path, output: Path) -> dict[str, object]:
    """Recover a reviewed edition for curation without making a plaintext side feed."""
    app = Path(__file__).resolve().parents[1]
    for public_root in (app, app.parent / "practicegraph.dev", source.parent.resolve()):
        if output.resolve() == public_root or public_root in output.resolve().parents:
            raise ValueError("read output must be in a separate private workspace")
    validate_draft(channel, source)
    with source.open("rb") as stream:
        raw = stream.read(MAX_ENVELOPE_BYTES + 1)
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise ValueError("catalog is too large")
    document = open_catalog(json.loads(raw), channel)
    private_directory(output.parent)
    atomic_write(output, canonical_bytes(document))
    return {"channel": channel, "version": edition_version(channel, document), "private_copy": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=tuple(CHANNELS), required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--draft", type=Path)
    source.add_argument("--read", type=Path, help="Read an existing edition into a private draft")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--signing-key",
        type=Path,
        default=Path.home() / ".practicegraph-publisher/catalog-signing-2026-09.key",
    )
    parser.add_argument("--key-id", choices=tuple(READER_KEYS), default="teamlandi-2026-09")
    args = parser.parse_args()
    try:
        result = (
            read_private(args.channel, args.read, args.out)
            if args.read
            else seal_draft(
                args.channel,
                args.draft,
                args.out,
                args.signing_key,
                args.key_id,
            )
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # No raw input, credentials or private key contents enter diagnostic output.
        print(
            "Catalog operation refused. Check the draft, edition version, output and signing key."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
