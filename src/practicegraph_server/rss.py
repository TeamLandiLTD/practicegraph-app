"""Server-side RSS/Atom ingestion: turn external feeds into a clean news artifact.

The org configures one or more RSS/Atom feed URLs (vendor blogs, arXiv, a
changelog). This module — running ONLY on the admin server, never the endpoint —
fetches those feeds, parses the XML safely, strips each item down to plain text,
and produces the closed briefings artifact the endpoint already validates and
shows. The endpoint never sees XML or a third-party origin (NFR-SEC-5 holds on
the endpoint); all the untrusted-XML risk is contained here.

Safety of untrusted XML is the whole point of doing this server-side:
  - any feed that declares a DTD or a custom entity is rejected BEFORE it
    reaches the XML parser, so the billion-laughs / entity-expansion and
    external-entity (XXE) attack classes cannot fire;
  - the fetch is https-only, size-bounded, and time-bounded per feed;
  - every extracted field is stripped of markup to plain text, length-capped,
    and then must survive parse_briefings_artifact (lexicon + leak + schema)
    before it can be served — a hostile feed can only ever publish clean,
    bounded, display-only text, never markup or a runnable payload.
"""

from __future__ import annotations

import codecs
import hashlib
import html
import re
import threading
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from practicegraph import nethttp
from practicegraph.analysis.briefings import (
    BRIEFING_KINDS,
    MAX_BODY_LEN,
    MAX_SOURCE_LEN,
    MAX_TITLE_LEN,
    briefings_artifact,
    parse_briefings_artifact,
)

_MAX_FEED_BYTES = 2_000_000  # a feed is larger than a catalog; still bounded
_FETCH_TIMEOUT_S = 12.0
_MAX_ITEMS_PER_FEED = 20
# The news band is a quick daily aside, not a reader: keep only the newest few
# across all feeds. Small on purpose.
MAX_NEWS_ITEMS = 3
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Atom namespace; RSS 2.0 is namespace-free.
_ATOM = "{http://www.w3.org/2005/Atom}"
# A well-formed feed has no DTD and no custom entities. The billion-laughs /
# entity-expansion and external-entity (XXE) attack classes BOTH require a
# DOCTYPE with <!ENTITY. Rejecting any feed that declares either — before it
# reaches the XML parser — closes both classes portably, without depending on
# a specific parser's private entity-handler hooks.
#
# The byte-level regex only sees the marker when it is ASCII/UTF-8. A UTF-16/32
# document interleaves the DOCTYPE bytes with NULs, so the marker would slip
# past this regex while expat (which auto-detects the encoding) still parses it
# and expands entities. We therefore reject NUL-bearing encodings outright
# (below) so only single-byte-compatible feeds ever reach this check.
_DTD_RE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
_UTF16_32_BOMS = (
    codecs.BOM_UTF16_LE,
    codecs.BOM_UTF16_BE,
    codecs.BOM_UTF32_LE,
    codecs.BOM_UTF32_BE,
)


def _is_multibyte_encoded(xml_bytes: bytes) -> bool:
    """True for UTF-16/UTF-32 (BOM or NUL-interleaved) content. A genuine UTF-8
    RSS/Atom prologue (`<?xml version=...`) has no NUL bytes; a NUL in the head
    means a multi-byte encoding that could hide a DOCTYPE from the byte guard."""
    if xml_bytes.startswith(_UTF16_32_BOMS):
        return True
    return b"\x00" in xml_bytes[:64]


@dataclass(frozen=True, slots=True)
class FeedSpec:
    """One configured feed: its https URL and how to classify its items.

    `kind` is the news kind assigned to every item from this feed (a releases
    feed → "release", a blog → "post"); `source` is the display label."""

    url: str
    kind: str
    source: str


def _text(value: str | None) -> str:
    """Markup + entities → bounded plain text. Strips tags, unescapes HTML
    entities, collapses whitespace. Never returns markup."""
    if not value:
        return ""
    stripped = _TAG_RE.sub(" ", value)
    unescaped = html.unescape(stripped)
    return _WS_RE.sub(" ", unescaped).strip()


def _first(elem: ET.Element, *paths: str) -> str:
    for path in paths:
        found = elem.find(path)
        if found is not None and (found.text or found.get("href")):
            return found.text or found.get("href") or ""
    return ""


def _published_sort(elem: ET.Element) -> str:
    """A sortable ISO-8601 timestamp for the item (newest sorts largest), from
    RSS pubDate or Atom updated/published. Unparseable/absent → empty string, so
    dated items always rank above undated ones. Used ONLY to order + trim; it is
    never part of the served item."""
    raw = _text(
        _first(
            elem,
            "pubDate",
            f"{_ATOM}updated",
            f"{_ATOM}published",
            "{http://purl.org/dc/elements/1.1/}date",
        )
    )
    if not raw:
        return ""
    # RFC-822 (RSS) first, then ISO-8601 (Atom).
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return ""


def _item_id(source: str, link: str, title: str) -> str:
    """A stable, schema-valid id for an item (id must be [a-z0-9-])."""
    basis = f"{source}|{link}|{title}".encode()
    return "feed-" + hashlib.sha256(basis).hexdigest()[:16]


def _parse_feed(xml_bytes: bytes, spec: FeedSpec) -> list[dict[str, object]]:
    """Parse one feed's XML into raw item dicts (pre-validation). A feed that
    declares a DTD or custom entities is rejected outright (XXE / entity-
    expansion defense), as is any malformed XML or NUL-bearing (UTF-16/32)
    encoding that could hide a DOCTYPE from the byte-level guard."""
    if _is_multibyte_encoded(xml_bytes):
        return []
    if _DTD_RE.search(xml_bytes):
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    # RSS: channel/item ; Atom: feed/entry
    items = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
    out: list[dict[str, object]] = []
    for item in items[:_MAX_ITEMS_PER_FEED]:
        title = _text(_first(item, "title", f"{_ATOM}title"))[:MAX_TITLE_LEN]
        body = _text(
            _first(item, "description", f"{_ATOM}summary", f"{_ATOM}content")
        )[:MAX_BODY_LEN]
        link = _text(_first(item, "link", f"{_ATOM}link", "guid"))
        if not title:
            continue
        if not body:
            body = title  # some feeds have title only; a one-line body is required
        entry: dict[str, object] = {
            "id": _item_id(spec.source, link, title),
            "kind": spec.kind,
            "title": title,
            "body": body,
            "source": spec.source[:MAX_SOURCE_LEN],
            # Private ordering key (published date); stripped before serving so
            # the artifact schema stays closed. Newest sorts largest.
            "_sort": _published_sort(item),
        }
        if link.lower().startswith("https://") and len(link) <= 300:
            entry["url"] = link
        out.append(entry)
    return out


def build_news_from_feeds(
    specs: list[FeedSpec],
    fetcher: Callable[[str, int, float], bytes | None] | None = None,
) -> dict[str, object]:
    """Fetch + parse + sanitize every feed into ONE validated news artifact.

    Items that fail the strict briefings validation are dropped individually; a
    feed that is unreachable or unparseable contributes nothing. If nothing
    valid survives across all feeds, returns the bundled starter artifact (never
    empty). The result is guaranteed to pass parse_briefings_artifact.
    """
    fetch = fetcher if fetcher is not None else _fetch_feed
    raw_items: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for spec in specs:
        if spec.kind not in BRIEFING_KINDS:
            continue
        try:
            xml_bytes = fetch(spec.url, _MAX_FEED_BYTES, _FETCH_TIMEOUT_S)
        except Exception:
            xml_bytes = None
        if not xml_bytes:
            continue
        for item in _parse_feed(xml_bytes, spec):
            item_id = str(item["id"])
            if item_id in seen_ids:
                continue
            # Validate the SERVED shape (without the private _sort key) so a bad
            # item is dropped exactly as the endpoint would drop it.
            served = {k: v for k, v in item.items() if k != "_sort"}
            candidate = {"briefings_version": "feed-probe", "entries": [served]}
            if parse_briefings_artifact(candidate) is not None:
                seen_ids.add(item_id)
                raw_items.append(item)

    if not raw_items:
        return briefings_artifact()  # bundled fallback — never serve nothing

    # Newest first (by published date), then a stable id tiebreak so undated
    # items keep a deterministic order. Keep only the newest few, then strip the
    # private sort key so the served entries match the closed schema.
    raw_items.sort(key=lambda i: (str(i.get("_sort", "")), str(i["id"])), reverse=True)
    entries = [
        {k: v for k, v in item.items() if k != "_sort"}
        for item in raw_items[:MAX_NEWS_ITEMS]
    ]
    # A content-derived version so the same feed items yield the same version
    # (deterministic, no clock) and any change to the item set invalidates it.
    fingerprint = hashlib.sha256(
        "".join(str(i["id"]) for i in entries).encode()
    ).hexdigest()[:12]
    artifact: dict[str, object] = {
        "briefings_version": f"news-feed-{fingerprint}",
        "entries": entries,
    }
    # Final belt-and-braces: the whole thing must validate as a unit.
    if parse_briefings_artifact(artifact) is None:
        return briefings_artifact()
    return artifact


def _fetch_feed(url: str, max_bytes: int, timeout_s: float) -> bytes | None:
    """GET an https feed URL, up to max_bytes. None on any failure or non-https.
    A User-Agent is set because some feed hosts reject blank agents. The fetch
    is https on every hop and a redirect to an internal/loopback address is
    refused (SSRF defense) — a hostile feed cannot bounce the server at an
    intranet host."""
    try:
        data = nethttp.fetch_bounded(
            url,
            max_bytes,
            timeout_s,
            headers={"User-Agent": "PracticeGraph-news/1.0"},
        )
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(data) > max_bytes:
        return None
    return data


def parse_feed_specs(config_value: str) -> list[FeedSpec]:
    """Parse the news_feeds config string into FeedSpecs. Each spec is
    "url|kind|source", specs separated by newlines or ';'. Malformed or
    non-https / bad-kind specs are skipped. `source` defaults to a host-ish
    label when omitted."""
    specs: list[FeedSpec] = []
    raw = config_value.replace(";", "\n")
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        kind = parts[1] if len(parts) > 1 and parts[1] else "post"
        source = parts[2] if len(parts) > 2 and parts[2] else "feed"
        if not url.lower().startswith("https://") or kind not in BRIEFING_KINDS:
            continue
        specs.append(FeedSpec(url=url, kind=kind, source=source[:MAX_SOURCE_LEN]))
    return specs


@dataclass
class _NewsCache:
    artifact: dict[str, object]
    fetched_at_monotonic: float


class NewsCatalog:
    """Serves the news artifact from the configured RSS feeds (parsed +
    sanitized + validated), cached per TTL, bundled fallback. Thread-safe (the
    server is multithreaded); a refresh happens under the lock so concurrent
    misses fetch at most once. `clock` is injectable for deterministic tests."""

    def __init__(
        self,
        feeds_config: str,
        ttl_s: int,
        clock: Callable[[], float],
        fetcher: Callable[[str, int, float], bytes | None] | None = None,
    ) -> None:
        self._specs = parse_feed_specs(feeds_config)
        self._ttl_s = ttl_s
        self._clock = clock
        self._fetcher = fetcher
        self._cache: _NewsCache | None = None
        self._lock = threading.Lock()

    def artifact(self) -> dict[str, object]:
        """Today's news to serve: bundled when no feeds are configured; else the
        cached feed set (refreshed past its TTL), falling back to the last good
        set or bundled if a refresh yields nothing."""
        if not self._specs:
            return briefings_artifact()
        with self._lock:
            now = self._clock()
            cache = self._cache
            if cache is not None and now - cache.fetched_at_monotonic < self._ttl_s:
                return cache.artifact
            built = build_news_from_feeds(self._specs, fetcher=self._fetcher)
            self._cache = _NewsCache(artifact=built, fetched_at_monotonic=now)
            return built
