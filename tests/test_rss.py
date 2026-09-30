"""Server-side RSS/Atom ingestion: external feeds become a clean, validated news
artifact. These tests pin the safety contract — untrusted XML is parsed with the
entity/DTD attack classes closed, every item is stripped to plain text and must
survive the briefings validator, a hostile or unreachable feed yields the bundled
fallback (never markup, never a runnable payload, never empty), and the whole
thing is deterministic."""

from __future__ import annotations

from practicegraph.analysis.briefings import (
    briefings_artifact,
    parse_briefings_artifact,
)
from practicegraph_server.rss import (
    FeedSpec,
    NewsCatalog,
    build_news_from_feeds,
    parse_feed_specs,
)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Tool Blog</title>
<item><title>Claude Opus 4.9 released</title>
<description>New model with &lt;b&gt;faster&lt;/b&gt; output and lower latency.</description>
<link>https://example.com/opus49</link></item>
<item><title>Codex CLI 2.1</title>
<description>Adds session resume and better diffs.</description>
<link>https://example.com/codex21</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>arXiv</title>
<entry><title>On automation bias in AI-assisted coding</title>
<summary>A study of when developers over-trust agent output.</summary>
<link href="https://arxiv.org/abs/2601.00001"/></entry>
</feed>"""

# The billion-laughs / entity-expansion attack — must be refused before parsing.
BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<rss version="2.0"><channel><item><title>&lol2;</title></item></channel></rss>"""

# An XXE external-entity attempt — also refused (has a DOCTYPE/ENTITY).
XXE = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss version="2.0"><channel><item><title>&xxe;</title></item></channel></rss>"""

# The same DOCTYPE attack, UTF-16 encoded. The interleaved NUL bytes would hide
# the "<!DOCTYPE" marker from a byte-level ASCII guard, so a NUL-bearing
# (UTF-16/32) feed is rejected outright before it reaches the parser.
BILLION_LAUGHS_UTF16 = BILLION_LAUGHS.decode("ascii").encode("utf-16")


def _fake(mapping):
    def fetch(url, mx, to):
        return mapping.get(url)
    return fetch


def test_rss_and_atom_parse_to_clean_items() -> None:
    fetch = _fake({"https://x/rss": RSS, "https://x/atom": ATOM})
    art = build_news_from_feeds(
        [
            FeedSpec("https://x/rss", "release", "Tool Blog"),
            FeedSpec("https://x/atom", "paper", "arXiv"),
        ],
        fetcher=fetch,
    )
    assert parse_briefings_artifact(art) is not None
    titles = {e["title"] for e in art["entries"]}
    assert "Claude Opus 4.9 released" in titles
    assert "On automation bias in AI-assisted coding" in titles
    # HTML was stripped to plain text (no tags survive).
    for e in art["entries"]:
        assert "<" not in e["body"] and ">" not in e["body"]
    # Kind + source come from the feed spec; https links are kept.
    by_title = {e["title"]: e for e in art["entries"]}
    assert by_title["Claude Opus 4.9 released"]["kind"] == "release"
    assert by_title["Claude Opus 4.9 released"]["source"] == "Tool Blog"
    assert by_title["Claude Opus 4.9 released"]["url"] == "https://example.com/opus49"


def test_entity_expansion_and_xxe_are_refused() -> None:
    """A feed declaring a DTD or custom entity is dropped before the parser sees
    it — the billion-laughs and XXE classes cannot fire — and the artifact falls
    back to bundled (nothing from the hostile feed appears)."""
    for attack in (BILLION_LAUGHS, XXE):
        art = build_news_from_feeds(
            [FeedSpec("https://x/evil", "post", "Evil")],
            fetcher=_fake({"https://x/evil": attack}),
        )
        # Bundled fallback: no feed-derived ids leaked through.
        assert art["briefings_version"] == briefings_artifact()["briefings_version"]
        assert all(not str(e["id"]).startswith("feed-") for e in art["entries"])


def test_utf16_encoded_dtd_cannot_bypass_the_guard() -> None:
    """A UTF-16-encoded DOCTYPE slips a byte-level ASCII guard (NULs between the
    marker bytes); the NUL-encoding is rejected outright so the entity-expansion
    class still cannot fire."""
    art = build_news_from_feeds(
        [FeedSpec("https://x/evil16", "post", "Evil")],
        fetcher=_fake({"https://x/evil16": BILLION_LAUGHS_UTF16}),
    )
    assert art["briefings_version"] == briefings_artifact()["briefings_version"]
    assert all(not str(e["id"]).startswith("feed-") for e in art["entries"])


def test_non_https_and_unreachable_feeds_yield_bundled() -> None:
    # A spec that never returns bytes.
    art = build_news_from_feeds(
        [FeedSpec("https://x/down", "post", "Down")],
        fetcher=_fake({}),
    )
    assert art["briefings_version"] == briefings_artifact()["briefings_version"]


def test_malformed_xml_is_dropped() -> None:
    art = build_news_from_feeds(
        [FeedSpec("https://x/bad", "post", "Bad")],
        fetcher=_fake({"https://x/bad": b"not xml <<<"}),
    )
    assert art["briefings_version"] == briefings_artifact()["briefings_version"]


def test_feed_spec_parsing() -> None:
    specs = parse_feed_specs(
        "https://a.example/rss|release|Vendor A\n"
        "https://b.example/atom|paper|arXiv;"
        "http://insecure/rss|post|Nope\n"           # non-https -> dropped
        "https://c.example/rss|boguskind|C\n"        # bad kind -> dropped
        "https://d.example/rss"                       # bare url -> defaults
    )
    urls = {s.url: s for s in specs}
    assert "https://a.example/rss" in urls
    assert urls["https://a.example/rss"].kind == "release"
    assert urls["https://a.example/rss"].source == "Vendor A"
    assert "http://insecure/rss" not in urls
    assert "https://c.example/rss" not in urls
    assert urls["https://d.example/rss"].kind == "post"  # default


def test_caps_to_newest_few_by_date() -> None:
    """The news band keeps only the newest MAX_NEWS_ITEMS across all feeds,
    ordered by published date; the private sort key never reaches the served
    item (the schema stays closed)."""
    from practicegraph_server.rss import MAX_NEWS_ITEMS

    dated = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Oldest</title><description>o</description><link>https://e/1</link>
<pubDate>Mon, 01 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>Newest</title><description>n</description><link>https://e/2</link>
<pubDate>Wed, 09 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>Middle</title><description>m</description><link>https://e/3</link>
<pubDate>Sat, 05 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>Second-newest</title><description>s</description><link>https://e/4</link>
<pubDate>Tue, 08 Jul 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""
    art = build_news_from_feeds(
        [FeedSpec("https://x/rss", "update", "Blog")],
        fetcher=_fake({"https://x/rss": dated}),
    )
    assert len(art["entries"]) == MAX_NEWS_ITEMS
    titles = [e["title"] for e in art["entries"]]
    assert titles == ["Newest", "Second-newest", "Middle"]  # date-desc
    assert "Oldest" not in titles  # trimmed
    for e in art["entries"]:
        assert "_sort" not in e  # private key stripped
    assert parse_briefings_artifact(art) is not None


def test_deterministic_output() -> None:
    fetch = _fake({"https://x/rss": RSS, "https://x/atom": ATOM})
    specs = [
        FeedSpec("https://x/rss", "release", "Tool Blog"),
        FeedSpec("https://x/atom", "paper", "arXiv"),
    ]
    a = build_news_from_feeds(specs, fetcher=fetch)
    b = build_news_from_feeds(specs, fetcher=fetch)
    assert a == b


def test_news_catalog_caches_and_falls_back() -> None:
    ticks = [1000.0]
    calls = []

    def fetch(url, mx, to):
        calls.append(url)
        return RSS if "rss" in url else ATOM

    cat = NewsCatalog(
        "https://x/rss|release|Tool Blog", 3600, lambda: ticks[0], fetcher=fetch
    )
    first = cat.artifact()
    assert any(str(e["id"]).startswith("feed-") for e in first["entries"])
    n = len(calls)
    # Within TTL: served from cache, no new fetch.
    cat.artifact()
    assert len(calls) == n
    # No feeds configured -> bundled, no fetch attempt.
    empty = NewsCatalog("", 3600, lambda: 0.0, fetcher=fetch)
    assert empty.artifact()["briefings_version"] == (
        briefings_artifact()["briefings_version"]
    )
