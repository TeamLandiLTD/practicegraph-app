"""TLS trust: every outbound HTTPS path verifies against the operating
system's trust store, and verification still fails closed."""

from __future__ import annotations

import datetime
import socket
import ssl
import threading
import urllib.request
from pathlib import Path

import pytest
import truststore
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from practicegraph import catalog, nethttp, tlstrust, transport


def _https_context(opener: urllib.request.OpenerDirector) -> ssl.SSLContext | None:
    handlers = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
    assert len(handlers) == 1
    context: ssl.SSLContext | None = handlers[0]._context  # type: ignore[attr-defined]
    return context


def test_client_context_uses_the_os_trust_store() -> None:
    """A corporate TLS-inspection root lives in the OS store (Keychain,
    Windows certificate store), never in OpenSSL's bundled cert.pem."""
    context = tlstrust.client_context()
    assert isinstance(context, truststore.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_every_outbound_opener_verifies_with_the_os_store() -> None:
    assert isinstance(_https_context(nethttp._opener), truststore.SSLContext)
    assert isinstance(_https_context(transport._opener), truststore.SSLContext)


def test_enterprise_pull_verifies_with_the_os_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b"{}"

    def fake_urlopen(request: object, timeout: float, context: object = None) -> _Response:
        seen["context"] = context
        return _Response()

    monkeypatch.setattr(catalog.urllib.request, "urlopen", fake_urlopen)
    assert catalog._get_json_any("https://intranet.example/v1/x", harden=False) == {}
    assert isinstance(seen["context"], truststore.SSLContext)


def _self_signed(tmp_path: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def test_an_untrusted_certificate_is_still_refused(tmp_path: Path) -> None:
    """Widening WHERE trust comes from must not loosen verification: a
    certificate no trust store vouches for fails the handshake."""
    cert_path, key_path = _self_signed(tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve() -> None:
        conn, _ = listener.accept()
        try:
            with server_context.wrap_socket(conn, server_side=True):
                pass
        except (ssl.SSLError, OSError):
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with (
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
            pytest.raises(ssl.SSLCertVerificationError),
            tlstrust.client_context().wrap_socket(raw, server_hostname="localhost"),
        ):
            pass
    finally:
        listener.close()
        thread.join(timeout=5)


def test_truststore_is_hash_pinned_and_frozen_into_every_build() -> None:
    """It runs inside the agent, so it gets the same artifact pin as every
    other shipped dependency, and each platform's freeze must include it."""
    root = Path(__file__).resolve().parent.parent

    def read(rel: str) -> str:
        return (root / rel).read_text(encoding="utf-8")

    assert '"truststore==0.10.4"' in read("pyproject.toml")
    assert (
        "truststore==0.10.4 \\\n    --hash=sha256:"
        "adaeaecf1cbb5f4de3b1959b42d41f6fab57b2b1666adb59e89cb0b53361d981"
    ) in read("packaging/shipped-requirements.txt")
    assert "--include-package=truststore" in read("packaging/build_workstation.ps1")
    macos = read("packaging/macos/build_macos.sh")
    assert "--collect-submodules truststore" in macos
    assert "--include-package=truststore" in macos
    assert "--collect-submodules truststore" in read("packaging/linux/build_linux.sh")
    assert '"truststore"' in read("packaging/third_party_notices.py")
