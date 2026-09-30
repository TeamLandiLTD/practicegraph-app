"""Curated news: a richer, editorial layer over the raw briefings feed.

Where a Briefing is one auto-parsed line from an RSS feed, a NewsItem is a
*curated* card: a short hook (three lines, why this matters at a glance), an
expanded summary shown on hover, an explicit "why this is here" note, a source
link, and a small thumbnail. The point of the feature is value, not volume — a
human (with a drafting assist) picks a few items that help people work better
with these tools (harnesses, new models, coding approaches, agents) and writes
the summary + why in PracticeGraph's plain, anti-hype voice. No clickbait.

Trust posture is identical to every catalog and to briefings: inbound copy is
UNTRUSTED display text — closed schema, length caps, the FR-FOC-8 forbidden
lexicon, and the no-leak patterns over every copy field. No runnable field, no
raw HTML. The url is https-only and display-only (never fetched by the endpoint;
a person chooses to open it). The thumbnail is a repo-relative path or a data:
URI — resolved from the site's own origin, never a remote image load — so the
strict site CSP (img-src 'self' data:) and the endpoint no-scrape rule both hold.
Which items a person saw stays local (NFR-PRV-6).

Determinism (INV-6): closed schema, sorted iteration, plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from practicegraph.analysis.briefings import BRIEFING_KINDS
from practicegraph.privacy import leak_findings, lexicon_violations

NEWS_VERSION = "news-curated-bundled"

# Reuse the closed briefings kinds (release / update / paper / post).
NEWS_KINDS: tuple[str, ...] = BRIEFING_KINDS

MAX_NEWS_ITEMS = 12  # a curated shelf, not a firehose
MAX_TITLE_LEN = 120
MAX_HOOK_LEN = 220  # ~3 short lines: the at-a-glance "why this matters"
MAX_SUMMARY_LEN = 600  # the expanded hover read
MAX_WHY_LEN = 280  # "why this is here" — one or two sentences
MAX_SOURCE_LEN = 40
MAX_URL_LEN = 300
MAX_THUMB_LEN = 200_000  # a small stored thumbnail path or bounded data: URI

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_URL_RE = re.compile(r"^https://[^\s]{1,290}$")
# A thumbnail is either a repo-relative path under the site (e.g.
# "assets/news/<id>.jpg") or a self-contained data: URI. Both resolve from the
# site's own origin — never a remote http(s) image URL.
_THUMB_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}$", re.IGNORECASE)
_THUMB_DATA_RE = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")

# The copy fields that must pass the lexicon + leak scans (everything a reader
# sees). Keeping this list explicit means a new field can't silently skip the
# safety scan.
_SCANNED_TEXT_FIELDS = ("title", "hook", "summary", "why", "source")


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One curated news card.

    hook is the three-line card text; summary is the fuller hover read; why is
    the explicit editorial note (why this is worth your time). thumb is a
    site-relative image path or a data: URI. url is the https source link.
    """

    news_id: str
    kind: str
    title: str
    hook: str
    summary: str
    why: str
    url: str
    source: str
    thumb: str | None = None
    attention: NewsAttention | None = None


@dataclass(frozen=True, slots=True)
class NewsAttention:
    """Editorial importance with a bounded notification window; never executable."""

    urgency: str
    reason: str
    starts_at: str
    expires_at: str


def parse_attention(raw: object) -> NewsAttention | None:
    if not isinstance(raw, dict) or set(raw) != {"urgency", "reason", "starts_at", "expires_at"}:
        return None
    if raw["urgency"] not in ("important", "urgent"):
        return None
    reason = _clean_text(raw["reason"], MAX_WHY_LEN)
    if reason is None:
        return None
    dates: list[datetime] = []
    for key in ("starts_at", "expires_at"):
        value = raw[key]
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value,
        ):
            return None
        try:
            dates.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    if not timedelta(0) < dates[1] - dates[0] <= timedelta(hours=72):
        return None
    return NewsAttention(raw["urgency"], reason, raw["starts_at"], raw["expires_at"])


def _clean_text(value: object, max_len: int) -> str | None:
    """A required copy field: a non-empty str within the cap that passes the
    lexicon + leak scans. None on any deviation."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not 0 < len(text) <= max_len:
        return None
    if lexicon_violations(text) or leak_findings(text):
        return None
    return text


def parse_news_artifact(raw: object) -> tuple[NewsItem, ...] | None:
    """Strict validation of a curated news artifact. Closed schema, caps, closed
    kinds, https-only display url, self-origin-only thumbnail, and the lexicon +
    leak scans over every copy field. None on any deviation — a source (including
    our own published file, treated as untrusted) cannot push copy the product
    could not write, a runnable payload, a non-https link, or a remote image."""
    if not isinstance(raw, dict) or set(raw.keys()) != {"news_version", "items"}:
        return None
    version = raw["news_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    items = raw["items"]
    if not isinstance(items, list) or not 0 < len(items) <= MAX_NEWS_ITEMS:
        return None

    allowed_keys = {
        "id", "kind", "title", "hook", "summary", "why", "url", "source", "thumb", "attention",
    }
    required_keys = {"id", "kind", "title", "hook", "summary", "why", "url", "source"}

    parsed: list[NewsItem] = []
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            return None
        keys = set(entry.keys())
        if not keys <= allowed_keys or not required_keys <= keys:
            return None

        news_id = entry["id"]
        if not isinstance(news_id, str) or not _ID_RE.match(news_id) or news_id in seen:
            return None

        kind = entry["kind"]
        if kind not in NEWS_KINDS:
            return None

        title = _clean_text(entry["title"], MAX_TITLE_LEN)
        hook = _clean_text(entry["hook"], MAX_HOOK_LEN)
        summary = _clean_text(entry["summary"], MAX_SUMMARY_LEN)
        why = _clean_text(entry["why"], MAX_WHY_LEN)
        source = _clean_text(entry["source"], MAX_SOURCE_LEN)
        # Each is None when its field was absent, wrong-typed, out of range, or
        # tripped a scan. Bail as a group so the constructor sees only str below.
        if title is None or hook is None or summary is None or why is None \
                or source is None:
            return None

        url = entry["url"]
        if not isinstance(url, str) or len(url) > MAX_URL_LEN or not _URL_RE.match(url):
            return None

        thumb = entry.get("thumb")
        if thumb is not None:
            if not isinstance(thumb, str) or not 0 < len(thumb) <= MAX_THUMB_LEN:
                return None
            if not (_THUMB_PATH_RE.match(thumb) or _THUMB_DATA_RE.match(thumb)):
                return None

        seen.add(news_id)
        attention = parse_attention(entry["attention"]) if "attention" in entry else None
        if "attention" in entry and attention is None:
            return None
        parsed.append(
            NewsItem(
                news_id=news_id,
                kind=kind,
                title=title,
                hook=hook,
                summary=summary,
                why=why,
                url=url,
                source=source,
                thumb=thumb,
                attention=attention,
            )
        )
    return tuple(parsed)


def news_item_to_entry(item: NewsItem) -> dict[str, object]:
    """A NewsItem back to its JSON entry (the exact served shape)."""
    entry: dict[str, object] = {
        "id": item.news_id,
        "kind": item.kind,
        "title": item.title,
        "hook": item.hook,
        "summary": item.summary,
        "why": item.why,
        "url": item.url,
        "source": item.source,
    }
    if item.thumb:
        entry["thumb"] = item.thumb
    if item.attention:
        entry["attention"] = {
            "urgency": item.attention.urgency,
            "reason": item.attention.reason,
            "starts_at": item.attention.starts_at,
            "expires_at": item.attention.expires_at,
        }
    return entry
