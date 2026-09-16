"""Local dashboard server (INTERACTIVE_UI_PLAN step 2).

`practicegraph ui serve` hosts the interactive app for THIS machine only:
bound to 127.0.0.1 on an ephemeral port, every request gated by a
per-session token (query string or header). It serves the bundled web app
when present (webui/), a plain placeholder otherwise, and one closed API:

    GET  /api/ping     liveness
    GET  /api/view     the shared view-model (report/viewmodel.py)
    POST /api/checkin  {"rating": 1..5}
    POST /api/dismiss  {"suggestion_id": ...} or {"tip_id": ...}

Writes go through the same closed paths the CLI commands use — this server
*is* a CLI mode, so C-4 (the CLI is the only writer) holds literally. While
building a fresh view, it can make the same configured, HTTPS-only, bounded
public-news request as the background agent; it never fetches news-item links.
It never logs request lines (tokens stay out of logs), and nothing it renders
leaves the machine.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
from datetime import UTC, date, datetime
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from practicegraph import __version__, news_notifications
from practicegraph.analysis import practice_time, setup_improvements, token_prices, training
from practicegraph.analysis.advisor_receipts import gather_receipts
from practicegraph.analysis.build_ideas import IDEA_API_LABELS
from practicegraph.analysis.calibration import record_estimate
from practicegraph.analysis.capability import CAPABILITY_RULES, accept_practice
from practicegraph.analysis.capability_ledger import (
    CAPABILITY_FEEDBACK_IDS,
    CAPABILITY_OUTCOME_IDS,
    CAPABILITY_SKIP_KEY,
    clear_coach_history,
    finish_practice,
    pending_outcome_unit,
    read_outcomes,
    record_outcome,
)
from practicegraph.analysis.capability_units import closed_capability_units
from practicegraph.analysis.dayclose import record_day_close, record_pieces_probe
from practicegraph.analysis.drain import record_drain
from practicegraph.analysis.economy import record_economy_intro_seen
from practicegraph.analysis.focus import TIP_IDS, record_block_event
from practicegraph.analysis.insights import dismiss_suggestion
from practicegraph.analysis.perception import record_checkin
from practicegraph.analysis.pin_model import (
    claude_home,
    codex_home,
    pin_claude_model,
    pin_codex_model,
    recent_claude_project_dirs,
)
from practicegraph.analysis.ratecard import activate_rate_card_from, active_rate_card
from practicegraph.analysis.schedule import (
    ScheduleUpdate,
    WeekendMode,
    local_day,
    read_schedule_profile,
    save_schedule_profile,
)
from practicegraph.analysis.skill_ledger import record_skill_target
from practicegraph.analysis.skills import Skill, record_skill_copy
from practicegraph.analysis.vocabulary import record_vocabulary_seen
from practicegraph.catalog import (
    load_skills,
    load_token_prices,
    pull_public_build_ideas,
    pull_public_community,
    pull_public_news,
    pull_public_playbooks,
    pull_public_token_prices,
    pull_public_training,
)
from practicegraph.config import (
    BILLING_MODES,
    BILLING_TOOLS,
    CAPABILITY_PATHS,
    PROFILES,
    Config,
    read_prefs,
    resolve,
    save_capability_paths,
    write_config_updates,
)
from practicegraph.history import ingest, snapshot_for_day
from practicegraph.report.shell import gather_privacy_status, gather_shell_extras
from practicegraph.report.viewmodel import view_model
from practicegraph.store import Store

UI_STATE_FILE = "ui.json"

# How often a running server checks whether a newer instance has taken over
# the published endpoint (the supersession watchdog in serve()).
SUPERSESSION_CHECK_S = 30
VIEW_CACHE_TTL_S = 30
_FEED_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BODY_BYTES = 4096

# Presence: "working" = any AI-tool log touched within the last 10 minutes.
# The same early-exit mtime walk the tray uses — no parsing, no content.
PRESENCE_FRESH_S = 10 * 60
PRESENCE_MAX_DEPTH = 6


def _any_fresh(root: Path, cutoff: float, depth: int) -> bool:
    if depth == 0:
        return False
    try:
        entries = list(root.iterdir())
    except OSError:
        return False
    for entry in entries:
        try:
            if entry.is_file():
                if entry.stat().st_mtime >= cutoff:
                    return True
            elif entry.is_dir() and _any_fresh(entry, cutoff, depth - 1):
                return True
        except OSError:
            continue
    return False


def presence_active(env: dict[str, str]) -> bool:
    cutoff = datetime.now(UTC).timestamp() - PRESENCE_FRESH_S
    home = Path(env.get("USERPROFILE") or Path.home())
    roots = [home / ".claude" / "projects", home / ".codex" / "sessions"]
    codex_home = env.get("CODEX_HOME", "")
    if codex_home:
        roots.append(Path(codex_home) / "sessions")
    return any(_any_fresh(root, cutoff, PRESENCE_MAX_DEPTH) for root in roots)


_PLACEHOLDER = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>PracticeGraph dashboard</title></head>
<body style="font-family:system-ui;background:#fcfbf8;color:#1f2a27;
             max-width:640px;margin:80px auto;line-height:1.6;">
<h1 style="font-size:20px;">PracticeGraph dashboard</h1>
<p>The interactive app ships here next. The API is already live for this
session &mdash; everything stays on this machine.</p>
</body></html>
"""


class UiServer(ThreadingHTTPServer):
    """127.0.0.1-only server carrying the session context."""

    daemon_threads = True
    # DO NOT reuse the address. The default (SO_REUSEADDR) lets a second server
    # bind a port a first one already holds — on Windows both then "succeed" and
    # requests split between them. We want the opposite: an exclusive bind, so a
    # second `ui serve` on the same port fails fast and reuses the first instead
    # of racing it. bind() sets SO_EXCLUSIVEADDRUSE below.
    allow_reuse_address = False

    def __init__(
        self,
        handler: type[BaseHTTPRequestHandler],
        env: dict[str, str],
        config: Config,
        store: Store,
        webui_dir: Path | None,
        port: int = 0,
    ) -> None:
        self._bind_port = port
        super().__init__(("127.0.0.1", port), handler)
        self.env = env
        self.config = config
        self.store = store
        self.webui_dir = webui_dir
        self.token = secrets.token_urlsafe(32)
        self._view_cache: tuple[float, bytes] | None = None
        self._refresh_lock = threading.Lock()
        self._refresh_running = False
        self._last_health: list[Any] = []
        self._setup_versions: dict[str, str | None] = {}

    def server_bind(self) -> None:
        # Exclusive bind on Windows: two servers can never hold the same port,
        # so "is the port free?" and "did I get it?" are the same question and
        # the double-serve race is closed at the socket layer.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            with contextlib.suppress(OSError):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def view_json(self) -> bytes:
        now = datetime.now(UTC)
        if self._view_cache is not None:
            cached_at, payload = self._view_cache
            if now.timestamp() - cached_at < VIEW_CACHE_TTL_S:
                return payload
        # NOTHING that can block on I/O of unknown duration runs in a view
        # request. Field incidents 2026-07-19 (a domain VM and a corporate
        # laptop, both reported as "stuck on Reading your local record"):
        # the request did a full log crawl AND a public news fetch inline.
        # On a managed network the fetch outlasts its own timeout (DNS and
        # proxy negotiation are not covered by the socket deadline), so the
        # app sat on its loading screen for minutes on machines with almost
        # no data. Both now run in one single-flight background worker; the
        # view always answers from the persisted store, and a finished
        # refresh invalidates the cache so the next poll paints fresh data.
        self._refresh_async()
        activate_rate_card_from(self.config.data_dir)
        prefs = read_prefs(self.config.data_dir)
        schedule = read_schedule_profile(self.config.data_dir)
        source_health = list(self._last_health)
        day = now.date()
        snapshot = snapshot_for_day(self.store, day, source_health)
        local_today = local_day(now, schedule)
        extras = gather_shell_extras(
            self.store,
            snapshot,
            prefs,
            day,
            schedule,
            local_today,
            env=self.env,
            generated_at=now,
            include_static_details=False,
        )
        self._setup_versions = {item.tool: item.installed for item in extras.harness_versions}
        model = view_model(
            snapshot,
            extras,
            now,
            __version__,
            active_rate_card().version,
            schedule=schedule,
            local_day=local_today,
            privacy=gather_privacy_status(self.config, self.store),
            wording_provider=prefs.reflection_style,
        )
        payload = json.dumps(model, sort_keys=True).encode("utf-8")
        self._view_cache = (now.timestamp(), payload)
        return payload

    def invalidate_view(self) -> None:
        self._view_cache = None

    def _refresh_async(self) -> None:
        """Kick one background refresh (news pull + log ingest) if none is
        running. Both are unbounded-I/O steps — a managed network can stall
        the fetch past its timeout, and a large or redirected log tree can
        stall the crawl — so neither may ever run inside a view request.
        Fail-open per step: a store this process cannot write (machine-store
        ACL) or a stalled fetch affects only this worker. Completion stores
        the fresh source health and invalidates the view cache."""
        with self._refresh_lock:
            if self._refresh_running:
                return
            self._refresh_running = True

        def run() -> None:
            try:
                with contextlib.suppress(Exception):
                    pull_public_news(self.store, self.config, datetime.now(UTC))
                with contextlib.suppress(Exception):
                    pull_public_build_ideas(self.store, self.config, datetime.now(UTC))
                with contextlib.suppress(Exception):
                    pull_public_community(self.store, self.config, datetime.now(UTC))
                with contextlib.suppress(Exception):
                    self._last_health = list(ingest(self.env, self.store, datetime.now(UTC)))
                with contextlib.suppress(Exception):
                    setup_improvements.refresh_followups(
                        self.store, datetime.now(UTC), self._setup_versions,
                    )
                with contextlib.suppress(Exception):
                    pull_public_playbooks(self.store, self.config, datetime.now(UTC))
                with contextlib.suppress(Exception):
                    pull_public_training(self.store, self.config, datetime.now(UTC))
                with contextlib.suppress(Exception):
                    pull_public_token_prices(self.store, self.config, datetime.now(UTC))
                self.invalidate_view()
            finally:
                with self._refresh_lock:
                    self._refresh_running = False

        threading.Thread(target=run, name="pg-view-refresh", daemon=True).start()


# Only genuine loopback names are honored in the Host header. A page on the web
# that resolves its own hostname to 127.0.0.1 (DNS rebinding) would send that
# hostname here; rejecting anything but a loopback Host closes that vector
# structurally, so the token is no longer the *only* thing standing between a
# malicious site and this server.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class UiHandler(BaseHTTPRequestHandler):
    server: UiServer

    # Tokens ride the query string: request lines must never hit a log.
    def log_message(self, format: str, *args: object) -> None:
        return

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        if not host:
            return True  # HTTP/1.0 / no Host: the loopback bind already localizes it
        # Strip the port; a bracketed IPv6 literal ([::1]:49321) keeps its colons.
        hostname = host[1:].split("]", 1)[0] if host.startswith("[") else host.rsplit(":", 1)[0]
        return hostname in _ALLOWED_HOSTS

    def _authorized(self) -> bool:
        parts = urlsplit(self.path)
        supplied = self.headers.get("X-PracticeGraph-Token", "")
        if not supplied:
            supplied = parse_qs(parts.query).get("token", [""])[0]
        # Compare as bytes: a non-ASCII query token would raise from
        # compare_digest on str inputs; encoding first keeps it total (and a
        # non-ASCII value simply never equals the URL-safe ASCII token).
        return hmac.compare_digest(supplied.encode("utf-8"), self.server.token.encode("utf-8"))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The app is self-contained; nothing may load from or talk to the web.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        self._send(status, json.dumps(value).encode("utf-8"), "application/json")

    def _track_skill_outcome(self, skill_id: str, day: date) -> None:
        """Start the outcome ledger for a just-copied skill.

        Best-effort by design: the copy itself already succeeded, and a skill
        whose findings map to no closed measure records nothing rather than
        being paired with a number that does not test it."""
        store = self.server.store
        entry = next(
            (
                skill
                for skill in load_skills(store.path.parent)
                if isinstance(skill, Skill) and skill.skill_id == skill_id
            ),
            None,
        )
        if entry is None:
            return
        record_skill_target(
            store,
            entry.skill_id,
            entry.title,
            tuple(entry.findings),
            gather_receipts(store, day),
            day,
        )

    def _token_prices_view(self) -> dict[str, Any]:
        from practicegraph.catalog import feed_status
        from practicegraph.content import content_digest

        result = token_prices.reading(
            load_token_prices(self.server.config.data_dir), datetime.now(UTC),
        )
        source_key = content_digest(self.server.config.token_prices_source_url.strip())[:16]
        result["download"] = {
            "feed": feed_status(self.server.config.data_dir, "token-prices"),
            "scheduled_attempt": self.server.store.meta_get(
                f"content_attempt_token_prices_{source_key}",
            ),
            "last_attempt": self.server.store.meta_get("token_prices_manual_attempt"),
            "result": self.server.store.meta_get("token_prices_manual_result"),
        }
        return result

    def do_GET(self) -> None:
        if not self._host_ok():
            self._send_json(403, {"error": "forbidden"})
            return
        path = urlsplit(self.path).path
        # The token gates the DATA (and every action). The app shell and its
        # assets carry nothing personal — the browser fetches them without
        # the token, and the app reads the token from its opening URL.
        if path.startswith("/api/") and not self._authorized():
            self._send_json(403, {"error": "forbidden"})
            return
        if path == "/api/ping":
            self._send_json(200, {"ok": True, "app_version": __version__})
            return
        if path == "/api/presence":
            self._send_json(200, {"working": presence_active(self.server.env)})
            return
        if path == "/api/token-prices":
            self._send_json(200, self._token_prices_view())
            return
        if path == "/api/news":
            self._send_json(200, news_notifications.reading(
                self.server.store, self.server.config.data_dir, datetime.now(UTC),
            ))
            return
        if path in ("/api/training", "/api/training/export"):
            try:
                if path.endswith("/export"):
                    result = training.export_history(self.server.store)
                else:
                    result = training.reading(
                        self.server.store, self.server.config.data_dir, datetime.now(UTC),
                    )
                self._send_json(200, result)
            except (ValueError, TypeError, KeyError, RecursionError):
                self._send_json(400, {"error": "training_state_invalid"})
            return
        if path in ("/api/setup-improvements", "/api/setup-improvements/export"):
            if path.endswith("/export"):
                result = setup_improvements.export_history(self.server.store)
            else:
                result = setup_improvements.reading(
                    self.server.store, self.server.config.data_dir, datetime.now(UTC),
                    self.server._setup_versions,
                )
            self._send_json(200, result)
            return
        if path in ("/api/practice-time", "/api/practice-time/export"):
            now = datetime.now(UTC)
            if path.endswith("/export"):
                result = practice_time.export_history(self.server.store, now)
            else:
                result = practice_time.reading(
                    self.server.store, now, read_schedule_profile(self.server.config.data_dir)
                )
            self._send_json(200, result)
            return
        if path == "/api/view":
            self._send(200, self.server.view_json(), "application/json")
            return
        if path == "/api/usage":
            from practicegraph.analysis.usage import reading
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if set(params) - {"token", "period", "page", "session"} or any(
                len(values) != 1 for values in params.values()
            ):
                self._send_json(400, {"error": "invalid_usage_request"})
                return
            try:
                usage_result = reading(self.server.store, datetime.now(UTC).date(),
                                       params.get("period", ["7d"])[0],
                                       int(params.get("page", ["0"])[0]),
                                       params.get("session", [""])[0])
                self._send_json(200, usage_result)
            except ValueError:
                self._send_json(400, {"error": "invalid_usage_request"})
            except KeyError:
                self._send_json(404, {"error": "session_not_in_period"})
            return
        if path == "/api/feed-day":
            # One archived day of an editorial shelf (news or build ideas).
            # Closed channel enum, closed day shape, strict re-parse on read
            # - the shelf is our own cache and still untrusted.
            from practicegraph.catalog import (
                load_build_ideas_for_day,
                load_news_for_day,
            )

            params = parse_qs(urlsplit(self.path).query)
            channel = (params.get("channel") or [""])[0]
            day = (params.get("day") or [""])[0]
            bad_channel = channel not in ("news", "build-ideas")
            if bad_channel or not _FEED_DAY_RE.match(day):
                self._send_json(400, {"error": "invalid_request"})
                return
            data_dir = self.server.config.data_dir
            if channel == "news":
                items: list[dict[str, object]] = [
                    {
                        "id": item.news_id,
                        "kind": item.kind,
                        "title": item.title,
                        "hook": item.hook,
                        "summary": item.summary,
                        "why": item.why,
                        "url": item.url,
                        "source": item.source,
                    }
                    for item in load_news_for_day(data_dir, day)
                ]
            else:
                edition = load_build_ideas_for_day(data_dir, day)
                items = [
                    {
                        "id": idea.idea_id,
                        "api": idea.api,
                        "api_label": IDEA_API_LABELS.get(idea.api, idea.api),
                        "feature": idea.feature,
                        "title": idea.title,
                        "hook": idea.hook,
                        "summary": idea.summary,
                        "steps": list(idea.steps),
                        "why": idea.why,
                        "url": idea.url,
                        "source": idea.source,
                        "repo": idea.repo,
                    }
                    for idea in edition.ideas
                ]
                repos = [
                    {
                        "id": pick.repo_id,
                        "name": pick.name,
                        "url": pick.url,
                        "what": pick.what,
                        "why": pick.why,
                        "caveat": pick.caveat,
                    }
                    for pick in edition.repos
                ]
                self._send_json(
                    200,
                    {"channel": channel, "day": day, "items": items, "repos": repos},
                )
                return
            self._send_json(200, {"channel": channel, "day": day, "items": items})
            return
        if path.startswith("/api/"):
            self._send_json(404, {"error": "not_found"})
            return
        if path in ("/", "/index.html"):
            index = (
                self.server.webui_dir / "index.html" if self.server.webui_dir is not None else None
            )
            if index is not None and index.is_file():
                self._send(200, index.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(200, _PLACEHOLDER.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith("/assets/") and self.server.webui_dir is not None:
            root = self.server.webui_dir.resolve()
            target = (root / path.lstrip("/")).resolve()
            if target.is_file() and target.is_relative_to(root):
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self._send(200, target.read_bytes(), content_type)
                return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._host_ok():
            self._send_json(403, {"error": "forbidden"})
            return
        if not self._authorized():
            self._send_json(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self._send_json(400, {"error": "invalid_length"})
            return
        limit = (
            16 * 1024 * 1024 if urlsplit(self.path).path in (
                "/api/practice-time/restore", "/api/setup-improvements/restore"
            )
            else MAX_BODY_BYTES
        )
        if length < 0 or length > limit:
            self._send_json(400, {"error": "body_too_large"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError, RecursionError):
            self._send_json(400, {"error": "invalid_json"})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"error": "invalid_json"})
            return
        # Machine-store installs (NFR-SEC-1 ACL): a per-user server cannot
        # write the service-owned store — interactive writes need a writable
        # context. Fail soft with a closed code, never reset the connection.
        try:
            self._dispatch_post(body)
        except (sqlite3.OperationalError, OSError):
            with contextlib.suppress(Exception):
                self._send_json(503, {"error": "store_readonly"})

    def _dispatch_post(self, body: dict[str, object]) -> None:
        path = urlsplit(self.path).path
        now = datetime.now(UTC)
        if path == "/api/token-prices/refresh":
            if body:
                self._send_json(400, {"error": "invalid_refresh"})
                return
            pull_result = pull_public_token_prices(
                self.server.store, self.server.config, now, manual=True,
            )
            if pull_result != "skipped_recently":
                self.server.store.meta_set("token_prices_manual_attempt", now.isoformat())
                self.server.store.meta_set("token_prices_manual_result", pull_result)
            response = self._token_prices_view()
            response["refresh_result"] = pull_result
            self._send_json(200, response)
            return
        if path == "/api/news":
            try:
                news_notifications.change(self.server.store, self.server.config.data_dir, body, now)
            except ValueError:
                self._send_json(400, {"error": "invalid_news_action"})
                return
            self._send_json(200, news_notifications.reading(
                self.server.store, self.server.config.data_dir, now,
            ))
            return
        schedule = read_schedule_profile(self.server.config.data_dir)
        local_today_date = local_day(now, schedule)
        local_today = local_today_date.isoformat()
        if path == "/api/training":
            try:
                training.change(self.server.store, self.server.config.data_dir, body, now)
                self._send_json(200, training.reading(
                    self.server.store, self.server.config.data_dir, now,
                ))
            except (ValueError, TypeError, KeyError, RecursionError) as error:
                code = str(error) if isinstance(error, ValueError) else "invalid_training_request"
                self._send_json(400, {"error": code})
            return
        if path in ("/api/setup-improvements", "/api/setup-improvements/restore"):
            if path.endswith("/restore") and body.get("action") != "restore":
                self._send_json(400, {"error": "invalid_request"})
                return
            try:
                handoff = setup_improvements.change(
                    self.server.store, self.server.config.data_dir, body, now,
                    self.server._setup_versions,
                )
            except (ValueError, TypeError, KeyError, RecursionError) as error:
                code = str(error) if isinstance(error, ValueError) else "invalid_request"
                self._send_json(400, {"error": code})
                return
            self._send_json(200, handoff or setup_improvements.reading(
                self.server.store, self.server.config.data_dir, now, self.server._setup_versions,
            ))
            return
        if path in ("/api/practice-time", "/api/practice-time/restore"):
            if path.endswith("/restore") and body.get("action") != "restore":
                self._send_json(400, {"error": "invalid_request"})
                return
            try:
                practice_time.change(self.server.store, body, now, schedule)
            except (ValueError, TypeError, KeyError) as error:
                code = str(error) if isinstance(error, ValueError) else "invalid_request"
                self._send_json(400, {"error": code})
                return
            self._send_json(200, practice_time.reading(self.server.store, now, schedule))
            return
        if path == "/api/schedule":
            expected = {
                "timezone_name",
                "working_days",
                "work_start",
                "work_end",
                "quiet_start",
                "quiet_end",
                "weekend_mode",
            }
            strings = expected - {"working_days"}
            working_days = body.get("working_days")
            texts = {key: value for key in strings if isinstance(value := body.get(key), str)}
            if (
                set(body) != expected
                or len(texts) != len(strings)
                or not isinstance(working_days, list)
                or not all(type(day) is int for day in working_days)
            ):
                self._send_json(400, {"error": "invalid_schedule"})
                return
            try:
                profile = save_schedule_profile(
                    self.server.config.data_dir,
                    ScheduleUpdate(
                        timezone_name=texts["timezone_name"],
                        working_days=tuple(working_days),
                        work_start=texts["work_start"],
                        work_end=texts["work_end"],
                        quiet_start=texts["quiet_start"],
                        quiet_end=texts["quiet_end"],
                        # save_schedule_profile is the validator (ValueError on
                        # anything outside the pair) — same contract as the
                        # profile loader's own cast.
                        weekend_mode=cast(WeekendMode, texts["weekend_mode"]),
                    ),
                )
            except (TypeError, ValueError):
                self._send_json(400, {"error": "invalid_schedule"})
                return
            self.server.invalidate_view()
            schedule_json = dataclasses.asdict(profile)
            schedule_json["working_days"] = list(profile.working_days)
            self._send_json(200, {"ok": True, "schedule": schedule_json})
            return
        if path == "/api/capability-paths":
            paths = body.get("paths")
            if (
                set(body) != {"paths"}
                or not isinstance(paths, list)
                or not paths
                or any(not isinstance(value, str) for value in paths)
                or len(paths) != len(set(paths))
                or set(paths) - set(CAPABILITY_PATHS)
            ):
                self._send_json(400, {"error": "invalid_capability_paths"})
                return
            save_capability_paths(self.server.config.data_dir, tuple(paths))
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/capability-outcome":
            outcome = body.get("outcome")
            if (
                set(body) != {"outcome"}
                or not isinstance(outcome, str)
                or outcome not in (*CAPABILITY_OUTCOME_IDS, "skip")
            ):
                self._send_json(400, {"error": "invalid_capability_outcome"})
                return
            if outcome == "skip":
                self.server.store.meta_set(CAPABILITY_SKIP_KEY, local_today_date.isoformat())
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
                return
            units = closed_capability_units(
                self.server.store, local_today_date, now, schedule=schedule
            )
            current = pending_outcome_unit(
                units, read_outcomes(self.server.store), local_today_date
            )
            if current is None:
                self._send_json(409, {"error": "no_pending_capability_outcome"})
                return
            try:
                record_outcome(self.server.store, current, outcome, local_today_date)
            except ValueError:
                self._send_json(409, {"error": "no_pending_capability_outcome"})
                return
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/capability-clear":
            if set(body) != {"confirm"} or body["confirm"] is not True:
                self._send_json(400, {"error": "confirmation_required"})
                return
            clear_coach_history(self.server.store)
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/capability-review":
            feedback = body.get("feedback")
            if (
                set(body) != {"feedback"}
                or not isinstance(feedback, str)
                or feedback not in CAPABILITY_FEEDBACK_IDS
            ):
                self._send_json(400, {"error": "invalid_capability_feedback"})
                return
            try:
                finish_practice(self.server.store, feedback, local_today_date)
            except ValueError:
                self._send_json(409, {"error": "no_active_capability_practice"})
                return
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/capability-practice":
            practice_id = body.get("practice_id")
            valid_practice_ids = {rule.practice_id for rule in CAPABILITY_RULES}
            if (
                set(body) != {"practice_id"}
                or not isinstance(practice_id, str)
                or practice_id not in valid_practice_ids
            ):
                self._send_json(400, {"error": "invalid_capability_practice"})
                return
            prefs = read_prefs(self.server.config.data_dir)
            units = closed_capability_units(
                self.server.store, local_today_date, now, schedule=schedule
            )
            try:
                accept_practice(
                    self.server.store,
                    practice_id,
                    units,
                    read_outcomes(self.server.store),
                    prefs.capability_paths,
                    local_today_date,
                    accepted_at=now,
                )
            except ValueError:
                self._send_json(409, {"error": "no_current_capability_practice"})
                return
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/checkin":
            rating = body.get("rating")
            if isinstance(rating, int) and record_checkin(self.server.store, local_today, rating):
                self.server.invalidate_view()
                self._send_json(200, {"ok": True, "rating": rating})
            else:
                self._send_json(400, {"error": "rating_out_of_range"})
            return
        if path == "/api/dismiss":
            suggestion_id = body.get("suggestion_id")
            tip_id = body.get("tip_id")
            if isinstance(suggestion_id, str) and dismiss_suggestion(
                self.server.store, suggestion_id, never=False, now=now
            ):
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
                return
            if isinstance(tip_id, str) and tip_id in TIP_IDS:
                self.server.store.dismiss_tip(tip_id, now)
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
                return
            self._send_json(400, {"error": "unknown_id"})
            return
        if path == "/api/skill-copied":
            # Copy ledger: a copied skill is delivered goods and retires from
            # the shelf for a few weeks (analysis/skills.py). Local meta only,
            # never on the wire (NFR-PRV-6).
            skill_id = body.get("skill_id")
            if isinstance(skill_id, str) and record_skill_copy(
                self.server.store, skill_id, local_day(now, schedule)
            ):
                # ...and the outcome ledger: record the measure this skill is
                # meant to move, so weeks from now the shelf can report what
                # actually happened instead of only that advice was given.
                self._track_skill_outcome(skill_id, local_day(now, schedule))
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "unknown_id"})
            return
        if path == "/api/block":
            # In-page focus timer: the dashboard records block/break lifecycle
            # events here instead of hopping out through the practicegraph:
            # protocol. Same local counters, never on the wire (NFR-PRV-6).
            event = body.get("event")
            if isinstance(event, str) and record_block_event(self.server.store, local_today, event):
                if event in ("break-started", "break-completed"):
                    practice_time.record_break(
                        self.server.store, now, starting=event == "break-started"
                    )
                self.server.invalidate_view()
                self._send_json(200, {"ok": True, "event": event})
            else:
                self._send_json(400, {"error": "unknown_event"})
            return
        if path == "/api/day-close":
            # Close the day (A2): mark today closed. The day is passed in and
            # validated (format + real date); the flag lives in meta, never on
            # the wire (NFR-PRV-6). Idempotent — closing twice is a no-op 200.
            #
            # W4 probe rider: an optional integer "pieces" journals the
            # felt-vs-measured pair. The measured side is computed server-side
            # at record time and NEVER returned — the no-peek rule. A refused
            # probe (bad value, already answered) does not fail the close.
            day = body.get("day")
            if isinstance(day, str) and record_day_close(self.server.store, day):
                if "pieces" in body:
                    record_pieces_probe(self.server.store, day, body.get("pieces"))
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "invalid_day"})
            return
        if path == "/api/calibration":
            # The Calibration Mirror: the client sends only the estimate. The
            # session it belongs to is resolved HERE, which is why no session
            # identity — and no actual length — ever reaches the browser
            # before the answer is given (NFR-PRV-6 and the no-peek rule).
            felt = body.get("felt")
            prefs = read_prefs(self.server.config.data_dir)
            if isinstance(felt, str) and record_estimate(
                self.server.store,
                local_day(now, schedule),
                felt,
                prefs.focus_coaching,
            ):
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "invalid_estimate"})
            return
        if path == "/api/drain":
            # The felt-drain probe (STRAIN_READING_PLAN S2): a closed bracket
            # or a skip, once per local day, recorded before the components
            # are ever sent — the no-peek rule in the other direction.
            felt = body.get("felt")
            prefs = read_prefs(self.server.config.data_dir)
            if (
                isinstance(felt, str)
                and prefs.focus_coaching
                and record_drain(self.server.store, local_day(now, schedule), felt, now)
            ):
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "invalid_drain"})
            return
        if path == "/api/economy-intro":
            # First-open retrospective dismissal (W1.1/AM-3): permanent,
            # idempotent, meta-table only — never on the wire (NFR-PRV-6).
            record_economy_intro_seen(self.server.store)
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/vocabulary":
            # The teaching card, put away for good. Same contract as the
            # economy intro: permanent, idempotent, meta-table only, and
            # never on the wire (NFR-PRV-6).
            record_vocabulary_seen(self.server.store)
            self.server.invalidate_view()
            self._send_json(200, {"ok": True})
            return
        if path == "/api/profile":
            # The audience switch (PRODUCTIVITY_PROFILE plan): a closed
            # enum into config.json, exactly the billing-lens shape. The
            # profile changes what the page says, never what is collected.
            chosen = body.get("profile")
            if isinstance(chosen, str) and chosen in PROFILES:
                write_config_updates(self.server.store.path.parent, {"profile": chosen})
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "invalid_profile"})
            return
        if path == "/api/pin-model":
            # The one-click default (models page): edit the TOOL'S OWN config
            # through the pin protocol. Everything is validated against
            # server-side truth: the model and effort against the served
            # catalog's closed ladder, a project against the sessions' own
            # recorded working directories - a posted path is never trusted.
            tool = body.get("tool")
            model = body.get("model")
            effort = body.get("effort")
            scope = body.get("scope")
            project = body.get("project")
            prefs = read_prefs(self.server.config.data_dir)
            from practicegraph.catalog import load_model_catalog

            catalog = load_model_catalog(self.server.config.data_dir, prefs.profile)
            entry = next((t for t in catalog.tools if t.tool == tool), None)
            row = None
            if entry is not None and isinstance(model, str):
                row = next((r for r in entry.models if r.model == model), None)
            valid = (
                entry is not None
                and row is not None
                and (
                    effort is None
                    or (isinstance(effort, str) and any(e.level == effort for e in entry.efforts))
                )
                and scope in ("machine", "project")
            )
            if not valid or row is None or row.pin is None:
                # No explicit pin value in the catalog = no write. A display
                # name guessed into a config file would be worse than a 400.
                self._send_json(400, {"error": "invalid_pin"})
                return
            # The catalog names the model for the page; row.pin is the value
            # the tool's own config takes ("opus", not "Claude Opus").
            model = row.pin
            effort_value = effort if isinstance(effort, str) else None
            env = self.server.env
            if scope == "project":
                if tool != "claude_code" or not isinstance(project, str):
                    self._send_json(400, {"error": "invalid_pin"})
                    return
                known = recent_claude_project_dirs(env)
                target = known.get(project)
                if target is None:
                    self._send_json(400, {"error": "unknown_project"})
                    return
                result = pin_claude_model(target / ".claude", model, effort_value)
            elif tool == "claude_code":
                result = pin_claude_model(claude_home(env), model, effort_value)
            else:
                result = pin_codex_model(codex_home(env), model, effort_value)
            self.server.invalidate_view()
            payload = {
                "ok": result.outcome == "pinned",
                "outcome": result.outcome,
                "path": result.path,
                "backup": result.backup,
            }
            self._send_json(200 if result.outcome == "pinned" else 409, payload)
            return
        if path == "/api/billing-mode":
            # The confirm-once billing lens (W1.1/AM-1): a closed enum into
            # config.json. Unknown values are refused, never coerced.
            mode = body.get("mode")
            tool = body.get("tool")
            if (isinstance(mode, str) and mode in BILLING_MODES
                    and (tool is None or (isinstance(tool, str) and tool in BILLING_TOOLS))):
                data_dir = self.server.store.path.parent
                billing_updates: dict[str, object]
                if tool is None:
                    billing_updates = {"billing_mode": mode}
                else:
                    modes = dict(read_prefs(data_dir).billing_by_tool)
                    modes[tool] = mode
                    billing_updates = {"billing_by_tool": modes}
                write_config_updates(data_dir, billing_updates)
                self.server.invalidate_view()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "invalid_mode"})
            return
        self._send_json(404, {"error": "not_found"})


def _webui_dir() -> Path | None:
    """The bundled web app, wherever this build keeps it: three levels above
    this file (source checkout and embedded-runtime bundles alike), or next
    to the engine executable in compiled builds, where ``__file__`` points
    at a per-run unpack directory instead of the install dir."""
    anchors = [Path(__file__).resolve().parent.parent.parent]
    with contextlib.suppress(OSError):
        anchors.append(Path(sys.executable).resolve().parent)
    if sys.argv and sys.argv[0]:
        with contextlib.suppress(OSError):
            anchors.append(Path(sys.argv[0]).resolve().parent)
    for anchor in anchors:
        candidate = anchor / "webui"
        if candidate.is_dir():
            return candidate
    return None


# A stable preferred port, derived deterministically from the data dir so it is
# the same across restarts for a given install but differs between installs (and
# from other apps). The app window can therefore keep a consistent origin; if the
# port is ever taken by something else, we fall back to an ephemeral port and the
# window re-resolves from ui.json. Range chosen in the high dynamic/registered
# band, away from common dev ports.
_PREFERRED_PORT_BASE = 49000
_PREFERRED_PORT_SPAN = 2000


def _preferred_port(data_dir: Path) -> int:
    seed = hashlib.sha256(str(data_dir.resolve()).encode("utf-8")).digest()
    return _PREFERRED_PORT_BASE + (int.from_bytes(seed[:2], "big") % _PREFERRED_PORT_SPAN)


def create_server(env: dict[str, str]) -> tuple[UiServer, str] | None:
    """Build the server, bound to the stable preferred port when free, else an
    ephemeral one. Returns (server, url-with-token); None when the store is
    missing. Raises OSError only if even the ephemeral bind fails."""
    config = resolve(env)
    from practicegraph.secureio import require_personal_context

    require_personal_context(config.data_dir, env)
    store = Store.in_data_dir(config.data_dir)
    if not store.exists():
        return None
    # Opening the server is a migration point (field report 2026-08-21, macOS
    # VM). Migrations used to run only on the ingest path, but both shells spawn
    # `ui serve` on window-open, so an upgraded install answered reads from
    # whatever schema its last tick left behind: a July-era store under the
    # 0.1.27 engine failed every /api/view with `no such column:
    # git_commit_attempts` until an `agent run --once` happened along. Migrating
    # here means a store is never READ at an older shape than the code reading
    # it. Fail-open, the _refresh_async discipline: on a hardened machine store
    # a per-user shell cannot write, and a read-only serve of a store some
    # writable context already migrated must still come up.
    with contextlib.suppress(sqlite3.Error, OSError):
        store.migrate()
    preferred = _preferred_port(config.data_dir)
    try:
        server = UiServer(UiHandler, env, config, store, _webui_dir(), port=preferred)
    except OSError:
        # Preferred port taken (our own prior instance, or anything else). Fall
        # back to an ephemeral port; the exclusive bind guarantees this one is
        # solely ours.
        server = UiServer(UiHandler, env, config, store, _webui_dir(), port=0)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?token={server.token}"
    return server, url


# Closed outcomes for the tick's ui_server category (agent.py).
ENSURE_CODES: tuple[str, ...] = (
    "ok",
    "skipped_healthy",
    "skipped_not_service",
    "skipped_not_initialized",
    "error",
)


def ensure_ui_server_process(env: dict[str, str], config: Config) -> str:
    """Compatibility entry point; the machine-wide personal UI is retired."""
    if env.get("PRACTICEGRAPH_SCAN_PROFILES") == "1":
        return "skipped_machine_mode_disabled"
    return "skipped_not_service"


def _read_state(data_dir: Path) -> tuple[int, str] | None:
    state_path = data_dir / UI_STATE_FILE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return int(state["port"]), str(state["token"])
    except (OSError, ValueError, KeyError):
        return None


def _instance_is_ours(port: int, token: str) -> bool:
    """Confirm the process on `port` is OUR ui server — a token-checked ping, not
    a bare socket probe. A bare probe can be fooled by any unrelated process that
    happened to grab a reused port; the /api/ping + token settles identity."""
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=1.0)
        conn.request("GET", f"/api/ping?token={token}")
        resp = conn.getresponse()
        ok = resp.status == 200
        resp.read()
        conn.close()
        return ok
    except (OSError, ValueError):
        return False


def _existing_instance(data_dir: Path) -> str | None:
    """A previous `ui serve` of OURS still listening? Identity-checked (token
    ping), so a stale ui.json whose port got reused by an unrelated process is
    correctly treated as dead. Returns the reuse URL, or None."""
    state = _read_state(data_dir)
    if state is None:
        return None
    port, token = state
    if not _instance_is_ours(port, token):
        return None
    return f"http://127.0.0.1:{port}/?token={token}"


def _write_state_atomic(data_dir: Path, port: int, token: str) -> None:
    """Publish an owner-restricted token; no machine-wide audience is allowed."""
    from practicegraph.secureio import atomic_write, private_directory, require_personal_context

    require_personal_context(data_dir, dict(os.environ))
    private_directory(data_dir)
    atomic_write(
        data_dir / UI_STATE_FILE,
        json.dumps({"port": port, "token": token}, sort_keys=True).encode(),
    )


def _per_user_state_dir() -> Path | None:
    """The per-user endpoint-publish location (the shell's first lookup)."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    return Path(local) / "PracticeGraph"


def serve(env: dict[str, str], open_browser: bool) -> int:
    config = resolve(env)
    # First run: create the store rather than refusing.
    #
    # This used to print "no local state yet - run: practicegraph init" and
    # exit 1. That made every fresh install a dead end, because the app is the
    # front door now and the installers ship the .app/.msi without putting a
    # `practicegraph` binary on PATH — so the one instruction on screen named a
    # command the user did not have. The shell then fell back to the newest
    # rendered report, which on a new machine is empty, and the whole product
    # looked like it had simply come up blank.
    #
    # There is nothing to ask a user here. `initialize` creates a directory and
    # an empty schema; it prompts for nothing, touches no network, and leaves
    # sharing OFF (FR-CNS-1) exactly as `init` does. Doing it silently is the
    # honest behaviour for a local-first tool.
    if not Store.in_data_dir(config.data_dir).exists():
        # Imported here, not at module scope: agent pulls in the collectors and
        # this module is imported by the CLI on every command.
        from practicegraph.agent import initialize

        initialize(env)
        print("first run - created the local store")
    existing = _existing_instance(config.data_dir)
    if existing is not None:
        print("dashboard already running - reusing it")
        if open_browser:
            import webbrowser

            webbrowser.open(existing)
        return 0
    # No live instance answered its own token, so any ui.json here is stale.
    # It is NOT cleared here. It used to be — "clear before bind so a reader
    # never sees a dead endpoint" — and that read as harmless until a fresh
    # install: every spawn there is COLD (onefile unpack, AV scanning a
    # just-installed unsigned binary, the upgrade rescan saturating the disk),
    # so each new start deleted the previous instance's published file and
    # then spent its slow seconds unbound — leaving ui.json absent for most
    # of the storm. The shell's 15s poll kept missing, and its old fallback
    # opened the report in a browser: the first thing every upgrade showed.
    # Publishing already REPLACES the file atomically, and a reader that
    # meets a stale endpoint gets a dead port or a 403 — both of which the
    # shell's watchdog treats as "keep waiting", same as an absent file.
    try:
        created = create_server(env)
    except OSError as exc:
        print(f"could not bind a local port: {exc}")
        return 1
    if created is None:
        # serve() creates the store above, so reaching here means it vanished
        # or could not be created — a real fault, not a first run.
        print("could not open the local store")
        return 1
    server, url = created
    # Publish the endpoint BEFORE serving so the first reader that beats us to
    # serve_forever still finds a correct, live-once-we-loop file. The exclusive
    # bind already happened, so the port in it is unambiguously ours.
    _write_state_atomic(config.data_dir, server.server_address[1], server.token)
    print("dashboard listening on 127.0.0.1 (this machine only)")
    print(f"  {url}")
    if open_browser:
        import webbrowser

        webbrowser.open(url)

    # Supersession watchdog. A cold start under a fresh install can convoy:
    # the shell keeps spawning while first-execution AV scans hold every
    # instance pre-publish, and whichever instances lose the ui.json race
    # would otherwise serve invisibly forever — holding a port and the store
    # open with no reader ever coming. Each instance therefore checks whether
    # the published endpoint still names it; when a DIFFERENT live instance
    # owns ui.json (identity-checked with that instance's own token, the
    # _instance_is_ours discipline), this one has been superseded and shuts
    # down. The winner never sees anything to yield to.
    def _stand_down_when_superseded() -> None:
        while True:
            time.sleep(SUPERSESSION_CHECK_S)
            current = _read_state(config.data_dir)
            if current is None or current[0] == server.server_address[1]:
                continue
            if _instance_is_ours(current[0], current[1]):
                server.shutdown()
                return

    threading.Thread(target=_stand_down_when_superseded, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Remove our own ui.json on a clean exit, but only if it still points at
        # us — a newer instance may have replaced it, and we must not delete the
        # live one out from under it.
        current = _read_state(config.data_dir)
        if current is not None and current[0] == server.server_address[1]:
            with contextlib.suppress(OSError):
                (config.data_dir / UI_STATE_FILE).unlink()
    return 0
