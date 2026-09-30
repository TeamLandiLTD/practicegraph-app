"""Skill catalog source: the admin server as a trusted gateway to GitHub.

The org curates skills as a repo published to GitHub Pages (a static JSON
artifact). This module lets the PASSIVE server fetch that artifact, validate it
with exactly the same strict validator the endpoint uses, cache it, and re-serve
it at /v1/catalog/skills. The endpoint therefore still only ever talks to the
configured API base URL (NFR-SEC-5) — the server is the one place that reaches
out to GitHub, and only for public catalog content, never anything per-user.

Trust boundary: GitHub Pages content is UNTRUSTED. It passes `parse_skills_artifact`
(closed schema, caps, closed tag vocabularies, lexicon + leak scans, no runnable
field) before it can be served — a compromised or careless skills repo can only
ever publish copy the product itself would be allowed to write. On any failure
(unreachable, oversized, malformed, invalid) the server falls back to the last
good fetch, or the bundled skills — it never serves nothing and never crashes.

The fetch is https-only, size-bounded, and time-bounded; results are cached for
skills_ttl_s so a burst of endpoint pulls hits GitHub at most once per window.
"""

from __future__ import annotations

import json
import threading
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass

from practicegraph import nethttp
from practicegraph.analysis.skills import parse_skills_artifact, skills_artifact

# A monotonic clock (seconds) and a fetcher (url, max_bytes, timeout) -> text.
# Both injectable so tests drive time and network deterministically.
Clock = Callable[[], float]
Fetcher = Callable[[str, int, float], "str | None"]

_MAX_ARTIFACT_BYTES = 512_000  # skills registries are text; generous but bounded
_FETCH_TIMEOUT_S = 10.0


@dataclass
class _CacheEntry:
    artifact: dict[str, object]
    fetched_at_monotonic: float


class SkillCatalog:
    """Serves the skills artifact, sourced from a GitHub Pages URL when
    configured (validated + cached), else the bundled set. The server is
    multithreaded (ThreadingHTTPServer), so the cache is guarded by a lock; a
    refresh happens under the lock so concurrent misses fetch at most once.

    `clock` is an injectable monotonic time source (seconds) so tests can drive
    TTL expiry deterministically without real waiting."""

    def __init__(
        self,
        url: str,
        ttl_s: int,
        clock: Clock,
        fetcher: Fetcher | None = None,
    ) -> None:
        self._url = url.strip()
        self._ttl_s = ttl_s
        self._clock = clock
        self._fetch = fetcher if fetcher is not None else _fetch_https
        self._cache: _CacheEntry | None = None
        self._lock = threading.Lock()

    def artifact(self) -> dict[str, object]:
        """The skills artifact to serve right now. Bundled when no URL is set;
        otherwise the cached fetch (refreshed past its TTL), falling back to the
        last good value or bundled if a refresh fails."""
        if not self._url:
            return skills_artifact()
        with self._lock:
            now = self._clock()
            cache = self._cache
            if cache is not None and now - cache.fetched_at_monotonic < self._ttl_s:
                return cache.artifact
            fetched = self._try_fetch()
            if fetched is not None:
                self._cache = _CacheEntry(artifact=fetched, fetched_at_monotonic=now)
                return fetched
            # Refresh failed: keep serving the last good fetch if we have one.
            if cache is not None:
                return cache.artifact
            return skills_artifact()

    def _try_fetch(self) -> dict[str, object] | None:
        """Fetch + validate once. None on any failure (caller falls back).
        Only an artifact that passes the endpoint's own strict validator is
        accepted — untrusted GitHub content can never bypass the copy rules."""
        try:
            raw = self._fetch(self._url, _MAX_ARTIFACT_BYTES, _FETCH_TIMEOUT_S)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            document = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(document, dict):
            return None
        if parse_skills_artifact(document) is None:
            return None  # schema/lexicon/leak/tag validation failed → reject
        return document


def _fetch_https(url: str, max_bytes: int, timeout_s: float) -> str | None:
    """GET an https URL, returning up to max_bytes of text or None. Refuses any
    non-https scheme (no plaintext, no file://) on every hop, and refuses a
    redirect to an internal/loopback address (SSRF defense) — the server reaches
    out only to public https catalog content."""
    try:
        data = nethttp.fetch_bounded(url, max_bytes, timeout_s)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(data) > max_bytes:
        return None  # oversized → reject rather than truncate into invalid JSON
    try:
        text: str = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text
