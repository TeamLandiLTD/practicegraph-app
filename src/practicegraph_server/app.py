"""The passive HTTP surface (FR-API-1/2/3): versioned routes, authenticated
ingest, k-anonymous aggregate queries, and the server-rendered dashboard.

The endpoint set is closed (INV-2): catalog GETs, ingest, aggregate queries,
the dashboard, and the unauthenticated ``/healthz`` liveness probe (which
carries nothing org-specific) — nothing else.

Error responses carry only {closed code, generic message, request id} plus the
stable ``X-Error-Code`` header. Logs record method, route template, status,
request id, error code — never bodies, tokens, or exception text (FR-API-3).
Access logging is disabled by default (FR-API-6).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from practicegraph.analysis.practices import practices_artifact
from practicegraph.analysis.ratecard import RATE_CARD_VERSION, rate_card_artifact
from practicegraph.transport import ERROR_CODE_HEADER
from practicegraph.wire import SUPPORTED_SCHEMA_VERSIONS, schema_version_supported, validate_emit
from practicegraph_server import SERVER_VERSION
from practicegraph_server.config import MAX_INGEST_BYTES, ServerConfig
from practicegraph_server.render import render_dashboard, render_error
from practicegraph_server.rss import NewsCatalog
from practicegraph_server.skills_source import SkillCatalog
from practicegraph_server.storage import ServerStorage
from practicegraph_server.view import build_coverage, build_summary

_LOG = logging.getLogger("practicegraph_server")

_MAX_WINDOW_DAYS = 90
_DEFAULT_WINDOW_DAYS = 14


def _catalog_content_version(*artifacts: object) -> str:
    """The coordination version as a hash of what is actually served.

    Deterministic (sorted keys, canonical separators), truncated to 16 hex —
    the same shape the endpoint's ``_VERSION_RE`` already accepts, so no
    validator changes anywhere. Any change to any served artifact changes the
    version; identical content always hashes identically, so endpoints still
    short-circuit when nothing was published. The static rate card rides on
    RATE_CARD_VERSION being included by the caller's response body already;
    it is hashed here too via bundled artifacts when passed."""
    canon = json.dumps(artifacts, sort_keys=True, separators=(",", ":"))
    return "c-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def clamp_window_days(raw: str) -> int | None:
    """Dashboard preset windows (FR-DSH-1): clamp ``?days=`` into
    [1, _MAX_WINDOW_DAYS]. None = unparseable (closed invalid_query error,
    FR-API-3). The JSON aggregate routes keep strict validation."""
    try:
        days = int(raw)
    except ValueError:
        return None
    return min(max(days, 1), _MAX_WINDOW_DAYS)


# Live-refresh cadence bounds: fast enough to feel live, floored so a client
# cannot hammer the dashboard into a tight reload loop.
_MIN_LIVE_SECONDS = 5
_MAX_LIVE_SECONDS = 300


def clamp_live_seconds(raw: str) -> int:
    """Parse ``?live=N`` into a refresh cadence, or 0 (no live mode) when absent,
    zero, or unparseable. A positive value is clamped into
    [_MIN_LIVE_SECONDS, _MAX_LIVE_SECONDS] so the meta-refresh stays sane."""
    if not raw:
        return 0
    try:
        seconds = int(raw)
    except ValueError:
        return 0
    if seconds <= 0:
        return 0
    return min(max(seconds, _MIN_LIVE_SECONDS), _MAX_LIVE_SECONDS)


class PracticeGraphServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, config: ServerConfig, storage: ServerStorage) -> None:
        super().__init__((config.bind, config.port), Handler)
        self.config = config
        self.storage = storage
        # Skill catalog: bundled, or proxied+validated from the configured
        # GitHub Pages URL. Built once; caches per its TTL.
        self.skill_catalog = SkillCatalog(config.skills_url, config.skills_ttl_s, time.monotonic)
        # News: RSS/Atom feeds polled + parsed + sanitized server-side into the
        # closed news artifact; bundled when no feeds are configured.
        self.news_catalog = NewsCatalog(config.news_feeds, config.news_ttl_s, time.monotonic)
        # Retention marker: last UTC day a prune ran (in-memory; a restart
        # simply prunes again at startup, which is idempotent).
        self._last_prune_day: str | None = None
        self.prune_expired(datetime.now(UTC).date())

    def prune_expired(self, today: date) -> None:
        """Apply retention at most once per UTC day (no-op when disabled).

        Runs at startup and is re-checked on every ingest; the DELETE is a
        deterministic day-string comparison, so racing threads at the day
        boundary can only repeat an idempotent prune.
        """
        if self.config.retention_days <= 0:
            return
        today_iso = today.isoformat()
        if self._last_prune_day == today_iso:
            return
        self._last_prune_day = today_iso
        cutoff = (today - timedelta(days=self.config.retention_days)).isoformat()
        self.storage.prune_days_before(cutoff)


class Handler(BaseHTTPRequestHandler):
    server: PracticeGraphServer  # narrowed for type checking

    # Generic identification only — no component versions on the wire.
    server_version = "PracticeGraph"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Default request logging is off (FR-API-6); closed-form logging is
        # done explicitly in _finish().
        return

    # -- plumbing --------------------------------------------------------------

    def _finish(
        self,
        status: int,
        route: str,
        error_code: str,
        request_id: str,
    ) -> None:
        if self.server.config.access_log or status >= 500:
            _LOG.info("%s %s %d %s %s", self.command, route, status, request_id, error_code)

    def _send_json(
        self,
        status: int,
        body: dict[str, Any],
        route: str,
        error_code: str,
        request_id: str,
    ) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header(ERROR_CODE_HEADER, error_code)
        self.send_header("X-Request-Id", request_id)
        self.end_headers()
        self.wfile.write(payload)
        self._finish(status, route, error_code, request_id)

    def _send_error(
        self, status: int, code: str, message: str, route: str, request_id: str
    ) -> None:
        self._send_json(
            status,
            {"code": code, "message": message, "request_id": request_id},
            route,
            code,
            request_id,
        )

    def _send_html(
        self, status: int, text: str, route: str, error_code: str, request_id: str
    ) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Request-Id", request_id)
        self.end_headers()
        self.wfile.write(payload)
        self._finish(status, route, error_code, request_id)

    def _ingest_source(self) -> str | None:
        header = self.headers.get("Authorization", "")
        config = self.server.config
        if not header.startswith("Bearer "):
            return None
        supplied = header[len("Bearer ") :].encode("utf-8")
        # A legacy shared token counts as ONE source, including after rotation.
        # Once independent sources are provisioned the legacy token is disabled.
        candidates = config.ingest_tokens or (("legacy-shared", config.org_token),)
        matched = None
        for source, expected in candidates:
            if expected and hmac.compare_digest(supplied, expected.encode("utf-8")):
                matched = source
        if matched is None:
            return None
        return hashlib.sha256(f"{config.org_id}\0{matched}".encode()).hexdigest()

    def _admin_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = self.server.config.dashboard_password
        if not expected:
            return False
        if header.startswith("Bearer "):
            return hmac.compare_digest(header[7:].encode(), expected.encode())
        return self._basic_ok()

    def _basic_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        config = self.server.config
        expected = config.dashboard_password
        if not expected or not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[len("Basic ") :], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return False
        _, _, password = decoded.partition(":")
        return hmac.compare_digest(password.encode(), expected.encode())

    def _window(self, query: dict[str, list[str]]) -> tuple[str, str] | None:
        """Resolve the [from, to] day window from query params; None = invalid."""
        try:
            if "from" in query and "to" in query:
                from_day = datetime.strptime(query["from"][0], "%Y-%m-%d").date()
                to_day = datetime.strptime(query["to"][0], "%Y-%m-%d").date()
            else:
                days = int(query.get("days", [str(_DEFAULT_WINDOW_DAYS)])[0])
                if not 1 <= days <= _MAX_WINDOW_DAYS:
                    return None
                to_day = datetime.now(UTC).date()
                from_day = to_day - timedelta(days=days - 1)
        except ValueError:
            return None
        if from_day > to_day or (to_day - from_day).days >= _MAX_WINDOW_DAYS:
            return None
        return from_day.isoformat(), to_day.isoformat()

    def _dashboard_window(self, query: dict[str, list[str]]) -> tuple[str, str] | None:
        """The dashboard's window: preset ``?days=`` links clamp into range
        (default stays the current window); explicit from/to stays strict."""
        if "from" in query and "to" in query:
            return self._window(query)
        days = clamp_window_days(query.get("days", [str(_DEFAULT_WINDOW_DAYS)])[0])
        if days is None:
            return None
        to_day = datetime.now(UTC).date()
        from_day = to_day - timedelta(days=days - 1)
        return from_day.isoformat(), to_day.isoformat()

    # -- routes ----------------------------------------------------------------

    def do_GET(self) -> None:
        request_id = str(uuid.uuid4())
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path

        if route == "/healthz":
            # Load-balancer/liveness probe: intentionally unauthenticated and
            # carries nothing org-specific (still passive per INV-2).
            self._send_json(
                200, {"ok": True, "server_version": SERVER_VERSION}, route, "ok", request_id
            )
            return
        if route == "/v1/catalog/versions":
            # The coordination version is a CONTENT HASH over the artifacts
            # this server is serving right now — never a constant. The
            # endpoint short-circuits its pull on version equality
            # (catalog.py), so a frozen string here meant publishing a new
            # skills.json or briefings feed propagated NOTHING: every
            # endpoint saw "unchanged" forever. Found 2026-08-13.
            self._send_json(
                200,
                {
                    "catalog_version": _catalog_content_version(
                        self.server.skill_catalog.artifact(),
                        self.server.news_catalog.artifact(),
                    ),
                    "rate_card_version": RATE_CARD_VERSION,
                    "supported_schema_versions": list(SUPPORTED_SCHEMA_VERSIONS),
                    "server_version": SERVER_VERSION,
                },
                route,
                "ok",
                request_id,
            )
            return
        if route == "/v1/catalog/rate-card":
            self._send_json(200, rate_card_artifact(), route, "ok", request_id)
            return
        if route == "/v1/catalog/practices":
            self._send_json(200, practices_artifact(), route, "ok", request_id)
            return
        if route == "/v1/catalog/skills":
            # The skill registry (a catalog artifact like practices): bundled, or
            # fetched + validated from the org's GitHub Pages catalog and
            # re-served here so the endpoint still talks only to this server
            # (NFR-SEC-5). Serving is passive; the endpoint matches locally, so
            # no per-user data is requested or returned (INV-2).
            self._send_json(200, self.server.skill_catalog.artifact(), route, "ok", request_id)
            return
        if route == "/v1/catalog/briefings":
            # The news feed: RSS/Atom feeds the server polled, parsed, and
            # sanitized into the closed artifact (bundled when none configured).
            # Passive serve, no per-user data (INV-2).
            self._send_json(200, self.server.news_catalog.artifact(), route, "ok", request_id)
            return
        if route in ("/v1/aggregates/summary", "/v1/aggregates/coverage"):
            if not self._admin_ok():
                self._send_error(401, "unauthorized", "authentication required", route, request_id)
                return
            window = self._window(query)
            if window is None:
                self._send_error(400, "invalid_query", "invalid window", route, request_id)
                return
            from_day, to_day = window
            grouped = self.server.storage.payloads_by_day(
                self.server.config.org_id, from_day, to_day
            )
            k = self.server.config.k_threshold
            if route.endswith("summary"):
                body = build_summary(grouped, k, from_day, to_day)
            else:
                body = build_coverage(grouped, k, from_day, to_day)
            self._send_json(200, body, route, "ok", request_id)
            return
        if route == "/dashboard" and self.server.config.serve_dashboard:
            if not self._basic_ok():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="PracticeGraph"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                self._finish(401, route, "unauthorized", request_id)
                return
            window = self._dashboard_window(query)
            if window is None:
                self._send_html(
                    400, render_error("invalid_query"), route, "invalid_query", request_id
                )
                return
            from_day, to_day = window
            # Optional live mode: ?live=N re-renders the page every N seconds via
            # a meta-refresh (script-free, NFR-SEC-2). Clamped to a sane cadence;
            # 0/absent/garbage = a static snapshot.
            refresh_seconds = clamp_live_seconds(query.get("live", [""])[0])
            try:
                grouped = self.server.storage.payloads_by_day(
                    self.server.config.org_id, from_day, to_day
                )
                summary = build_summary(grouped, self.server.config.k_threshold, from_day, to_day)
                page = render_dashboard(
                    summary,
                    self.server.config.org_id,
                    datetime.now(UTC),
                    refresh_seconds,
                )
                self._send_html(200, page, route, "ok", request_id)
            except Exception:
                self._send_html(
                    500, render_error("internal_error"), route, "internal_error", request_id
                )
            return
        self._send_error(404, "not_found", "unknown route", route, request_id)

    def do_POST(self) -> None:
        request_id = str(uuid.uuid4())
        route = urlparse(self.path).path
        if route != "/v1/ingest":
            self._send_error(404, "not_found", "unknown route", route, request_id)
            return
        source_id = self._ingest_source()
        if source_id is None:
            self._send_error(401, "unauthorized", "authentication required", route, request_id)
            return
        # Retention piggybacks on authenticated ingest: cheap in-memory day
        # marker check, at most one prune per UTC day.
        self.server.prune_expired(datetime.now(UTC).date())
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_INGEST_BYTES:
            self._send_error(413, "payload_too_large", "payload size rejected", route, request_id)
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_error(422, "invalid_payload", "payload rejected", route, request_id)
            return

        # FR-EMT-3: version gate BEFORE full validation; the response never
        # echoes the submitted value.
        if not schema_version_supported(payload):
            self._send_error(
                400,
                "schema_version_not_supported",
                "schema version not supported by this server; an agent update or a"
                " server upgrade may be required",
                route,
                request_id,
            )
            return
        if validate_emit(payload):
            self._send_error(422, "invalid_payload", "payload rejected", route, request_id)
            return
        # Org id in the payload must match the authenticated org (FR-API-2).
        if payload.get("org_id") != self.server.config.org_id:
            self._send_error(403, "forbidden", "org mismatch", route, request_id)
            return

        received_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.server.storage.put_emit(
            self.server.config.org_id,
            str(payload["emit_id"]),
            str(payload["day"]),
            payload,
            received_at,
            source_id=source_id,
        )
        self._send_json(
            202,
            {"code": "accepted", "message": "aggregate accepted", "request_id": request_id},
            route,
            "accepted",
            request_id,
        )


def make_server(config: ServerConfig, storage: ServerStorage) -> PracticeGraphServer:
    return PracticeGraphServer(config, storage)
