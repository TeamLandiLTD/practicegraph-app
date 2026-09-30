"""Outbound transport (NFR-SEC-5): connects only to the configured API base
URL, only for catalog/probe GETs and consent-gated emit POSTs. All failures
classify into the closed FR-EMT-2 error-code set; response bodies are never
surfaced beyond the stable error-code header."""

from __future__ import annotations

import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from practicegraph.tlstrust import client_context

ERROR_CODE_HEADER = "X-Error-Code"

# Closed transport error codes (FR-EMT-2).
EMIT_ERROR_CODES: tuple[str, ...] = (
    "server_unavailable",
    "timeout",
    "http_5xx",
    "http_4xx",
    "invalid_payload",
    "transport_error",
    "schema_version_not_supported",
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_opener = urllib.request.build_opener(
    _NoRedirect(), urllib.request.HTTPSHandler(context=client_context())
)


@dataclass(frozen=True, slots=True)
class EmitResult:
    ok: bool
    error_code: str | None = None


def post_emit(
    api_base_url: str, org_token: str, payload_json: str, timeout_s: float = 10.0
) -> EmitResult:
    request = urllib.request.Request(
        api_base_url.rstrip("/") + "/v1/ingest",
        data=payload_json.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {org_token}",
        },
        method="POST",
    )
    try:
        with _opener.open(request, timeout=timeout_s) as response:
            return EmitResult(
                ok=response.status == 202,
                error_code=None if response.status == 202 else "transport_error",
            )
    except urllib.error.HTTPError as error:
        code_header = error.headers.get(ERROR_CODE_HEADER, "")
        if code_header == "schema_version_not_supported":
            return EmitResult(ok=False, error_code="schema_version_not_supported")
        if code_header == "invalid_payload":
            return EmitResult(ok=False, error_code="invalid_payload")
        if error.code >= 500:
            return EmitResult(ok=False, error_code="http_5xx")
        return EmitResult(ok=False, error_code="http_4xx")
    except TimeoutError:
        return EmitResult(ok=False, error_code="timeout")
    except urllib.error.URLError as error:
        if isinstance(error.reason, socket.timeout | TimeoutError):
            return EmitResult(ok=False, error_code="timeout")
        return EmitResult(ok=False, error_code="server_unavailable")
    except OSError:
        return EmitResult(ok=False, error_code="transport_error")


def probe(api_base_url: str, timeout_s: float = 5.0) -> str:
    """Opt-in reachability probe for doctor (FR-DIA-1). Closed result codes;
    performs no store writes and sends nothing but the GET."""
    request = urllib.request.Request(
        api_base_url.rstrip("/") + "/v1/catalog/versions", method="GET"
    )
    try:
        with _opener.open(request, timeout=timeout_s):
            return "reachable"
    except urllib.error.HTTPError:
        return "http_error"
    except TimeoutError:
        return "timeout"
    except urllib.error.URLError as error:
        if isinstance(error.reason, socket.timeout | TimeoutError):
            return "timeout"
        return "server_unavailable"
    except OSError:
        return "transport_error"
