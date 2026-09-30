"""News feed: short items about the AI-coding-tools world, shown in the news band.

This is NOT PracticeGraph's own product news — it is what is happening AROUND the
tools people use: model releases, tool/CLI updates, notable posts, papers, pricing
changes. The org (or a shared public default) curates one or more RSS/Atom feeds;
the SERVER polls and parses those feeds, sanitizes each item into the closed JSON
artifact below, and serves it. The endpoint only ever pulls that clean, validated
JSON from its configured source — it never fetches a third-party feed or parses
XML (server_side.rss handles that; NFR-SEC-5 stays intact on the endpoint).

Trust posture is identical to every catalog: inbound copy is UNTRUSTED. A news
item is closed display text — a kind, a short title, a one-line body, an OPTIONAL
source label, and an OPTIONAL https display url — validated with a closed schema,
length caps, the FR-FOC-8 forbidden lexicon, and the no-leak patterns. There is no
runnable field and no raw HTML. The endpoint never fetches an item's url (no
scrape); the url is shown and a person chooses to open it. Which items a person
saw or dismissed stays local — news engagement never crosses the wire (NFR-PRV-6).

Determinism (INV-6): closed schema, sorted iteration, plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from practicegraph.privacy import leak_findings, lexicon_violations

BRIEFINGS_VERSION = "news-bundled-2026-07-09"

# Closed item kinds, framed for external ecosystem news:
#   release      - a new model / tool / CLI version
#   update       - a change to a tool people use (feature, pricing, deprecation)
#   paper        - research / a notable write-up
#   post         - a blog post, thread, or announcement worth a look
BRIEFING_KINDS: tuple[str, ...] = ("release", "update", "paper", "post")

MAX_BRIEFINGS = 50
MAX_TITLE_LEN = 120  # feed titles run a little longer than a hand-written line
MAX_BODY_LEN = 240  # one-line summary of the item
MAX_SOURCE_LEN = 40  # the publication / feed name
MAX_URL_LEN = 300

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# A news url is display-only and https-only; the endpoint never fetches it, but
# we still refuse to render a non-https or malformed link.
_URL_RE = re.compile(r"^https://[^\s]{1,290}$")


@dataclass(frozen=True, slots=True)
class Briefing:
    """One news item: kind, title, one-line body, optional source + display url."""

    briefing_id: str
    kind: str
    title: str
    body: str
    url: str | None = None
    source: str | None = None  # the publication / feed the item came from


# Bundled starter feed — the offline/first-run set. Real, evergreen-ish ecosystem
# notes so a fresh/offline install is not blank; the live RSS source replaces it.
BUNDLED_BRIEFINGS: tuple[Briefing, ...] = (
    Briefing(
        "match-model-to-task",
        "post",
        "Match the model tier to the task, not the other way round",
        "Routine edits rarely need a premium model; reserving the top tier for "
        "hard reasoning keeps spend deliberate without slowing work.",
        source="PracticeGraph field notes",
    ),
    Briefing(
        "read-the-handoff",
        "post",
        "A minute on the handoff pays off",
        "When a long agent run ends in a summary, a short read before you approve "
        "usually catches the one thing worth changing.",
        source="PracticeGraph field notes",
    ),
    Briefing(
        "connect-a-news-feed",
        "update",
        "Connect a news feed to see tool and model updates here",
        "Point PracticeGraph at an RSS feed and this space fills with releases, "
        "updates, and posts about the tools you use.",
        source="PracticeGraph",
    ),
)


def briefings_artifact() -> dict[str, object]:
    """The bundled briefings as a versioned catalog artifact (server side)."""
    return {
        "briefings_version": BRIEFINGS_VERSION,
        "entries": [
            {
                "id": briefing.briefing_id,
                "kind": briefing.kind,
                "title": briefing.title,
                "body": briefing.body,
                **({"url": briefing.url} if briefing.url else {}),
                **({"source": briefing.source} if briefing.source else {}),
            }
            for briefing in BUNDLED_BRIEFINGS
        ],
    }


def parse_briefings_artifact(raw: object) -> tuple[Briefing, ...] | None:
    """Strict validation: closed schema, caps, closed kind vocabulary, https-only
    display url, and the lexicon + leak scans over every copy field. None on any
    deviation — a source cannot push copy the product could not write, a runnable
    payload (there is none), or a non-https link."""
    if not isinstance(raw, dict) or set(raw.keys()) != {"briefings_version", "entries"}:
        return None
    version = raw["briefings_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    entries = raw["entries"]
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_BRIEFINGS:
        return None
    briefings: list[Briefing] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        if not set(entry.keys()) <= {"id", "kind", "title", "body", "url", "source"}:
            return None
        briefing_id = entry.get("id")
        kind = entry.get("kind")
        title = entry.get("title")
        body = entry.get("body")
        url = entry.get("url")
        source = entry.get("source")
        if (
            not isinstance(briefing_id, str)
            or not _ID_RE.match(briefing_id)
            or briefing_id in seen_ids
            or kind not in BRIEFING_KINDS
            or not isinstance(title, str)
            or not 0 < len(title) <= MAX_TITLE_LEN
            or not isinstance(body, str)
            or not 0 < len(body) <= MAX_BODY_LEN
        ):
            return None
        if url is not None and (
            not isinstance(url, str)
            or len(url) > MAX_URL_LEN
            or not _URL_RE.match(url)
        ):
            return None
        if source is not None and (
            not isinstance(source, str) or not 0 < len(source) <= MAX_SOURCE_LEN
        ):
            return None
        for text in (title, body) + ((source,) if source else ()):
            if lexicon_violations(text) or leak_findings(text):
                return None
        seen_ids.add(briefing_id)
        briefings.append(
            Briefing(
                briefing_id=briefing_id, kind=kind, title=title, body=body,
                url=url, source=source,
            )
        )
    return tuple(briefings)
