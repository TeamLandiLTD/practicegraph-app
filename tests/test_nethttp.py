"""Hardened outbound fetch (SSRF defense): https-only every hop, public host
only, and redirect targets re-checked before they are followed."""

from __future__ import annotations

import socket

import pytest

from practicegraph import nethttp


def test_rejects_non_https_scheme() -> None:
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp.fetch_bounded("http://example.com/news.json", 1000, 1.0)


def test_rejects_loopback_host() -> None:
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp.fetch_bounded("https://127.0.0.1/news.json", 1000, 1.0)
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp.fetch_bounded("https://localhost/news.json", 1000, 1.0)


def test_rejects_link_local_metadata_host() -> None:
    # The classic cloud-metadata SSRF target must never be fetched.
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp.fetch_bounded("https://169.254.169.254/latest/meta-data/", 1000, 1.0)


def test_rejects_private_range_host() -> None:
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp.fetch_bounded("https://10.0.0.5/news.json", 1000, 1.0)
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp.fetch_bounded("https://192.168.1.1/admin", 1000, 1.0)


def test_redirect_to_private_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # A public origin that 302s to an internal address must NOT be followed:
    # the redirect handler re-checks the target and raises.
    request = object()
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp._SafeRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "http://169.254.169.254/"
        )
    with pytest.raises(nethttp.UnsafeRequest):
        nethttp._SafeRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://127.0.0.1/"
        )


def test_connection_uses_checked_ip_and_keeps_original_tls_hostname(monkeypatch):
    calls = []

    def resolve(host, port, **kwargs):
        calls.append(("dns", host))
        # A later answer would be private; the connection must not ask again.
        ip = "93.184.216.34" if len(calls) == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    class Socket:
        def settimeout(self, value):
            pass

        def connect(self, address):
            calls.append(("connect", address))

        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, server_hostname):
            calls.append(("tls", server_hostname))
            return sock

    monkeypatch.setattr(nethttp.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(nethttp.socket, "socket", lambda *_: Socket())
    connection = nethttp._PinnedHTTPSConnection("example.com", timeout=1)
    connection._context = Context()
    connection.connect()
    assert calls == [
        ("dns", "example.com"),
        ("connect", ("93.184.216.34", 443)),
        ("tls", "example.com"),
    ]


def test_mixed_and_non_global_dns_answers_are_refused(monkeypatch):
    for addresses in (("93.184.216.34", "127.0.0.1"), ("100.64.0.1",)):
        monkeypatch.setattr(
            nethttp.socket,
            "getaddrinfo",
            lambda *args, addresses=addresses, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
                for ip in addresses
            ],
        )
        with pytest.raises(nethttp.UnsafeRequest):
            nethttp._PinnedHTTPSConnection("example.com", timeout=1).connect()
