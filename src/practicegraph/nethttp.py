"""Hardened outbound HTTPS fetch (SSRF defense for public catalog/feed pulls).

The endpoint and the admin server pull *public* artifacts (news, skills, model
guidance, RSS feeds) from attacker-influenceable URLs over HTTPS. A plain
``urlopen`` enforces the scheme only on the first hop and then follows any 3xx
redirect — including a downgrade to ``http://`` or a jump to a loopback / link-
local / private address (``127.0.0.1``, ``169.254.169.254``, ``10.x`` …). This
module wraps those fetches so EVERY hop is re-checked: the scheme must stay
https and the host must resolve to a public address. A redirect that violates
either is refused instead of followed.

Scope: this is for the public-artifact fetchers only. The admin-configured
enterprise ``api_base_url`` is a trusted origin (and may legitimately be an
http intranet host), so it keeps its own permissive fetch and does not route
through here.

Connections use the checked socket addresses directly, while TLS verifies the
original hostname. Ambient proxies are disabled for public artifacts: otherwise
the proxy could resolve a different target outside this address policy.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class UnsafeRequest(urllib.error.URLError):
    """A hop that is not public https: a scheme downgrade or a non-public host.

    Subclasses URLError so the existing ``except urllib.error.URLError`` guards
    in the callers already map it to their closed failure code.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)


def _public_addresses(host: str, port: int = 443) -> list[Any]:
    """True only if every address the host resolves to is a public, routable
    unicast address. Any loopback/private/link-local/reserved answer fails
    closed."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise UnsafeRequest("unresolved_host") from None
    if not infos:
        raise UnsafeRequest("unresolved_host")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise UnsafeRequest("non_public_host") from None
        if not ip.is_global or ip.is_multicast:
            raise UnsafeRequest("non_public_host")
    return infos


def _host_is_public(host: str) -> bool:
    try:
        _public_addresses(host)
        return True
    except UnsafeRequest:
        return False


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    _tunnel_host: str | None
    _context: ssl.SSLContext

    def connect(self) -> None:
        if self._tunnel_host:
            raise UnsafeRequest("proxy_tunnel_refused")
        addresses = _public_addresses(self.host, self.port)
        last_error: OSError | None = None
        for family, kind, proto, _, address in addresses:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(self.timeout)
                sock.connect(address)  # Already checked; no second DNS resolution.
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as error:
                sock.close()
                last_error = error
        raise last_error or UnsafeRequest("connection_failed")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    _context: ssl.SSLContext | None

    def https_open(self, req: urllib.request.Request) -> Any:
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)


def _assert_safe(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https":
        raise UnsafeRequest("non_https")
    host = parts.hostname or ""
    if not host:
        raise UnsafeRequest("no_host")
    if not _host_is_public(host):
        raise UnsafeRequest("non_public_host")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the safety check on the redirect target before following it."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _assert_safe(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _SafeRedirectHandler(), _PinnedHTTPSHandler()
)


def fetch_bounded(
    url: str,
    max_bytes: int,
    timeout_s: float,
    *,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET a public-https ``url``, reading up to ``max_bytes + 1`` bytes (the
    caller enforces the cap so it can distinguish oversize from valid). Raises
    ``UnsafeRequest`` / ``URLError`` / ``OSError`` on any failure or policy
    breach; every redirect hop is re-checked for https + a public host."""
    _assert_safe(url)
    request = urllib.request.Request(url, method="GET", headers=headers or {})
    with _opener.open(request, timeout=timeout_s) as response:
        data: bytes = response.read(max_bytes + 1)
    return data
