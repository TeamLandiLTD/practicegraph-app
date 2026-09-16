"""Encrypted publication and client reading: authentication, bounds and immutable editions."""

from __future__ import annotations

import base64
import copy
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tools.catalog_seal import read_private, seal_draft
from tools.content_release import inventory, prepare, validate_draft

from practicegraph import catalog
from practicegraph import catalog_crypto as crypto
from practicegraph.config import resolve
from practicegraph.store import Store
from test_training import NOW, edition


@pytest.fixture()
def publisher(monkeypatch):
    private = Ed25519PrivateKey.generate()
    reader = os.urandom(32)
    public = private.public_key().public_bytes_raw()
    monkeypatch.setattr(crypto, "READER_KEYS", {"synthetic": (reader, public)})
    monkeypatch.setattr("tools.catalog_seal.READER_KEYS", crypto.READER_KEYS)
    return private.private_bytes_raw()


def test_round_trip_uses_random_nonces_and_preserves_plain_compatibility(publisher):
    document = edition()
    first = crypto.seal(document, "training", "synthetic", publisher)
    second = crypto.seal(document, "training", "synthetic", publisher)
    assert first["nonce"] != second["nonce"] and first["ciphertext"] != second["ciphertext"]
    assert document["entries"][0]["title"] not in json.dumps(first)
    assert crypto.open_catalog(first, "training") == document
    # Authoring tools may open a plain draft; the client's network boundary may not.
    assert crypto.open_catalog(document, "training") == document
    with pytest.raises(ValueError, match="unsigned"):
        crypto.open_catalog(document, "training", require_sealed=True)
    assert crypto.open_catalog(first, "training", require_sealed=True) == document
    with pytest.raises(ValueError):
        crypto.open_catalog(first, "news")


@pytest.mark.parametrize(
    "field",
    ["signature", "ciphertext", "nonce", "channel", "edition_version", "key_id", "schema", "extra"],
)
def test_tampering_and_unknown_transport_are_rejected(publisher, field):
    envelope = crypto.seal(edition(), "training", "synthetic", publisher)
    envelope[field] = "practicegraph.encrypted-catalog/999" if field == "schema" else "tampered"
    with pytest.raises(ValueError):
        crypto.open_catalog(envelope, "training")


def test_reader_key_does_not_allow_publisher_forgery(publisher):
    other = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="publisher key"):
        crypto.seal(edition(), "training", "synthetic", other.private_bytes_raw())
    envelope = crypto.seal(edition(), "training", "synthetic", publisher)
    unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    envelope["signature"] = base64.b64encode(other.sign(crypto._canonical(unsigned))).decode()
    with pytest.raises(ValueError):
        crypto.open_catalog(envelope, "training")


def test_valid_signature_still_requires_matching_aead_and_inner_version(publisher):
    envelope = crypto.seal(edition(), "training", "synthetic", publisher)
    envelope["edition_version"] = "wrong-version"
    signer = Ed25519PrivateKey.from_private_bytes(publisher)
    envelope["signature"] = base64.b64encode(
        signer.sign(crypto._canonical({k: v for k, v in envelope.items() if k != "signature"}))
    ).decode()
    with pytest.raises(ValueError):
        crypto.open_catalog(envelope, "training")


def test_client_decrypts_caches_and_retains_last_good_edition(tmp_path, monkeypatch, publisher):
    store = Store.in_data_dir(tmp_path)
    store.migrate()
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    envelope = crypto.seal(edition(), "training", "synthetic", publisher)
    monkeypatch.setattr(catalog.nethttp, "fetch_bounded", lambda *a: json.dumps(envelope).encode())
    assert catalog.pull_public_training(store, config, NOW) == "pulled"
    assert catalog.load_training(tmp_path).entries[0]["title"] == "Synthetic verification lab"
    receipt = catalog.feed_status(tmp_path, "training", now=NOW)
    assert receipt["state"] == "current"
    envelope["signature"] = "bad"
    from datetime import timedelta

    assert (
        catalog.pull_public_training(store, config, NOW + timedelta(hours=1)) == "invalid_artifact"
    )
    assert catalog.load_training(tmp_path).version == edition()["training_version"]


def test_skill_seals_idempotently_and_release_gate_validates_encrypted_bytes(tmp_path, publisher):
    private = tmp_path / "private"
    private.mkdir()
    draft, key = private / "draft.json", private / "signing.key"
    draft.write_text(json.dumps(edition()))
    key.write_text(publisher.hex())
    site = tmp_path / "site"
    site.mkdir()
    (site / "CONTENT_LICENSE.txt").write_text("Synthetic terms")
    (site / ".vercelignore").write_text(
        "/*\n!CONTENT_LICENSE.txt\n!catalog-manifest.json\n!training.json\n"
    )
    out = site / "training.json"
    result = seal_draft("training", draft, out, key, "synthetic")
    first = out.read_bytes()
    assert result["encrypted"] and not result["reused"]
    assert seal_draft("training", draft, out, key, "synthetic")["reused"]
    assert out.read_bytes() == first
    assert validate_draft("training", out)["channel"] == "training"
    release = prepare(site, tmp_path / "archive")
    assert inventory(site) == release
    assert (
        tmp_path / "archive/training" / f"{edition()['training_version']}.json"
    ).read_bytes() == first
    edited = copy.deepcopy(edition())
    edited["entries"][0]["summary"] = "Changed without a new edition."
    draft.write_text(json.dumps(edited))
    with pytest.raises(ValueError):
        seal_draft("training", draft, out, key, "synthetic")
    assert out.read_bytes() == first


def test_bounds_and_untrusted_key_urls_cannot_trigger_key_fetches(monkeypatch, publisher):
    envelope = crypto.seal(edition(), "training", "synthetic", publisher)
    envelope["key_url"] = "https://127.0.0.1/secrets"
    with pytest.raises(ValueError):
        crypto.open_catalog(envelope, "training")
    document = {**edition(), "padding": "x" * crypto.MAX_ENVELOPE_BYTES}
    monkeypatch.setattr(catalog.nethttp, "fetch_bounded", lambda *a: json.dumps(document).encode())
    assert (
        catalog._get_catalog_json("https://example.org/training.json", "training")
        == "invalid_artifact"
    )


def test_skill_reads_back_into_private_workspace_only(tmp_path, publisher):
    site = tmp_path / "site"
    site.mkdir()
    source = site / "training.json"
    source.write_text(json.dumps(crypto.seal(edition(), "training", "synthetic", publisher)))
    with pytest.raises(ValueError, match="separate private workspace"):
        read_private("training", source, site / "plaintext.json")
    assert not (site / "plaintext.json").exists()
    output = tmp_path / "private" / "draft.json"
    assert read_private("training", source, output)["private_copy"]
    assert json.loads(output.read_text()) == edition()
