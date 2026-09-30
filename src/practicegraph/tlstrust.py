"""TLS trust for every outbound HTTPS connection: the operating system's store.

Python's ``ssl`` verifies against OpenSSL's CA file (``/etc/ssl/cert.pem`` on
macOS, the frozen interpreter's bundle elsewhere). Organizations that inspect
TLS (Zscaler, Netskope, Palo Alto and the like) deploy their inspection root
through device management into the OS store — the macOS Keychain, the Windows
certificate store — which that file never sees. Every download then failed on
a managed network with "self-signed certificate in certificate chain", while
the browser on the same machine worked.

``truststore`` verifies through the platform's own verifier (Security.framework
on macOS, CryptoAPI on Windows, the system bundle on Linux), so the engine
trusts exactly what the device trusts, including device-managed trust settings
and distrust. Only the SOURCE of trust changes: hostname checking and
certificate verification stay mandatory and still fail closed.

Trusting an inspecting proxy lets it read this traffic, as it already reads the
browser's. It cannot forge public catalog editions: those are Ed25519-signed
with a publisher key pinned in the client (``catalog_crypto``).
"""

from __future__ import annotations

import ssl

import truststore


def client_context() -> ssl.SSLContext:
    """A client TLS context that verifies against the OS trust store."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
