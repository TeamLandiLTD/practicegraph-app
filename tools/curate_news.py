#!/usr/bin/env python3
"""Daily news curation: fetch a wide set of AI feeds, show the freshest items,
let a human pick a few, and write a validated news.json for the website.

The endpoint clients fetch that news.json directly from the site (the same
deliberate single-URL relaxation used for the public skills catalog), so the
file MUST be a valid briefings artifact — the exact closed shape the endpoint's
briefings loader already validates. This tool reuses the server's RSS parser and
the endpoint's validator, so nothing here re-implements fetching or the schema:
it only gathers candidates, takes your selection, and writes the artifact.

Run it once a day:

    python tools/curate_news.py

It prints ~20 numbered candidates (newest first, across all feeds). Enter the
numbers you want (e.g. `3 7 12`), it builds the artifact from those, and — after
you confirm — writes it to the website repo's news.json. Nothing is published
until you confirm and then commit/push the site yourself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Reuse the server's RSS machinery and the endpoint's validator. No re-impl.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from tools.editorial_workspace import editorial_root  # noqa: E402

from practicegraph.analysis.briefings import (  # noqa: E402
    parse_briefings_artifact,
)
from practicegraph_server.rss import (  # noqa: E402
    FeedSpec,
    _fetch_feed,
    _parse_feed,
)

# The website repo (CodeTeamLandi/practicegraph.dev). The curated file lands at
# its root as news.json; the client fetches
# https://practicegraph.dev/news.json (DEFAULT_NEWS_SOURCE_URL).
DEFAULT_SITE_REPO = _REPO.parent / "practicegraph.dev"
NEWS_FILE_NAME = "news.json"

# How many candidates to surface for curation, and how many to publish. The
# published count matches the endpoint's small news band (a quick daily aside).
CANDIDATE_COUNT = 20
PUBLISH_COUNT = 3

# The verified feed set (checked live 2026-07-11: each returns valid RSS/Atom).
# kind is the closed briefings kind assigned to every item from that feed:
#   release  vendor product/model announcements
#   update   platform / library / ecosystem updates
#   paper    research (papers, research-focused publications)
#   post     everything else (videos, blog posts, newsletters, commentary)
# A dead feed contributes nothing (fetch failure is skipped), so the set can be
# trimmed or extended freely — add a (url, kind, source) row.
FEEDS: tuple[FeedSpec, ...] = (
    # Vendor / lab announcements
    FeedSpec("https://openai.com/news/rss.xml", "release", "OpenAI"),
    FeedSpec("https://deepmind.google/blog/rss.xml", "release", "Google DeepMind"),
    # Platform / ecosystem
    FeedSpec("https://huggingface.co/blog/feed.xml", "update", "Hugging Face"),
    FeedSpec(
        "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
        "update",
        "MIT Technology Review",
    ),
    # Research
    FeedSpec("https://rss.arxiv.org/rss/cs.AI", "paper", "arXiv cs.AI"),
    FeedSpec("https://thegradient.pub/rss/", "paper", "The Gradient"),
    # Newsletters / commentary
    FeedSpec("https://magazine.sebastianraschka.com/feed", "post", "Ahead of AI"),
    FeedSpec("https://jack-clark.net/feed/", "post", "Import AI"),
    FeedSpec("https://lastweekin.ai/feed", "post", "Last Week in AI"),
    FeedSpec("https://simonwillison.net/atom/everything/", "post", "Simon Willison"),
    FeedSpec("https://www.marktechpost.com/feed/", "post", "MarkTechPost"),
    # YouTube channels (channel feeds; a video is a "post")
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?user=keeroyz",
        "post",
        "Two Minute Papers",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCZHmQk67mSJgfCCTn7xBfew",
        "post",
        "Yannic Kilcher",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCNJ1Ymd5yFuUPtn21xtRbbw",
        "post",
        "AI Explained",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UChpleBmo18P08aKCIgti38g",
        "post",
        "Matt Wolfe",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCXZCJLdBC09xxGZ6gcdrc6A",
        "release",
        "OpenAI YouTube",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCrDwWp7EBBv4NwvScIpBDOA",
        "release",
        "Anthropic YouTube",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCbRP3c757lWg9M-U7TyEkXA",
        "post",
        "Theo",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCawZsQWqfGSbCI5yjkdVkTA",
        "post",
        "Matthew Berman",
    ),
    FeedSpec(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCqcbQf6yw5KzRoDDcZ_wBSw",
        "post",
        "Wes Roth",
    ),
)

_MAX_FEED_BYTES = 2_000_000
_FETCH_TIMEOUT_S = 12.0


def gather_candidates() -> list[dict[str, object]]:
    """Fetch every feed and return de-duplicated items, newest first.

    Reuses the server's _fetch_feed (bounded, https-only, DTD/entity-rejecting)
    and _parse_feed (markup-stripped, schema-shaped). A feed that is unreachable
    or unparseable simply contributes nothing — the tool still runs.
    """
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for spec in FEEDS:
        try:
            xml = _fetch_feed(spec.url, _MAX_FEED_BYTES, _FETCH_TIMEOUT_S)
        except Exception:
            xml = None
        if not xml:
            print(f"  (skipped, unreachable: {spec.source})", file=sys.stderr)
            continue
        count = 0
        for item in _parse_feed(xml, spec):
            iid = str(item["id"])
            if iid in seen:
                continue
            # Keep only items that validate as a served entry (drop the private
            # _sort key first) — so what you pick from is exactly publishable.
            served = {k: v for k, v in item.items() if k != "_sort"}
            probe = {"briefings_version": "candidate", "entries": [served]}
            if parse_briefings_artifact(probe) is None:
                continue
            seen.add(iid)
            items.append(item)
            count += 1
        print(f"  {spec.source}: {count} items", file=sys.stderr)
    # Newest first (by published sort), stable id tiebreak for undated items.
    items.sort(key=lambda i: (str(i.get("_sort", "")), str(i["id"])), reverse=True)
    return items


def show_candidates(items: list[dict[str, object]]) -> None:
    print()
    print(f"=== {len(items)} candidates (newest first) ===")
    for n, item in enumerate(items, 1):
        title = str(item["title"])
        if len(title) > 90:
            title = title[:87] + "..."
        src = str(item.get("source") or "")
        kind = str(item["kind"])
        when = str(item.get("_sort") or "")[:10]  # YYYY-MM-DD
        print(f"  [{n:2}] {kind:7} {src:20} {when}  {title}")
    print()


def parse_selection(raw: str, count: int) -> list[int]:
    """'3 7 12' or '3,7,12' -> [2, 6, 11] (0-based, validated)."""
    picks: list[int] = []
    for tok in raw.replace(",", " ").split():
        try:
            idx = int(tok)
        except ValueError:
            raise SystemExit(f"not a number: {tok!r}") from None
        if not 1 <= idx <= count:
            raise SystemExit(f"out of range (1..{count}): {idx}")
        if idx - 1 not in picks:
            picks.append(idx - 1)
    return picks


def build_artifact(chosen: list[dict[str, object]]) -> dict[str, object]:
    """A validated briefings artifact from the chosen items. Same content-derived
    version scheme the server uses, so an unchanged pick yields the same file.

    Only the private _sort key is stripped — the served entry keeps its `id`
    (the validator reads it as the briefing_id and dedupes on it, so dropping it
    would fail validation)."""
    entries = [
        {k: v for k, v in item.items() if k != "_sort"} for item in chosen
    ]
    fingerprint = hashlib.sha256(
        "".join(str(i["id"]) for i in chosen).encode()
    ).hexdigest()[:12]
    artifact: dict[str, object] = {
        "briefings_version": f"news-curated-{fingerprint}",
        "entries": entries,
    }
    if parse_briefings_artifact(artifact) is None:
        raise SystemExit("internal error: built artifact failed validation")
    return artifact


def main() -> int:
    ap = argparse.ArgumentParser(description="Curate the daily AI news file.")
    ap.add_argument(
        "--out",
        type=Path,
        default=editorial_root() / "drafts" / "legacy-briefings.json",
        help="private legacy briefing draft; use curate_news_rich.py for the news channel",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=CANDIDATE_COUNT,
        help=f"how many candidates to show (default: {CANDIDATE_COUNT})",
    )
    ap.add_argument(
        "--pick",
        type=str,
        default=None,
        help="non-interactive selection, e.g. --pick '3 7 12' (skips the prompt)",
    )
    args = ap.parse_args()

    print("fetching feeds...", file=sys.stderr)
    items = gather_candidates()
    if not items:
        print("no items from any feed (all unreachable?)", file=sys.stderr)
        return 1
    items = items[: args.top]
    show_candidates(items)

    if args.pick is not None:
        raw = args.pick
        print(f"picking (non-interactive): {raw}")
    else:
        raw = input(
            f"enter {PUBLISH_COUNT} numbers to publish (space/comma separated), "
            "or blank to cancel: "
        ).strip()
    if not raw:
        print("cancelled — nothing written.")
        return 0

    picks = parse_selection(raw, len(items))
    chosen = [items[i] for i in picks]

    print("\n=== you picked ===")
    for item in chosen:
        print(f"  - [{item['kind']}] {item['source']}: {item['title']}")
        if item.get("url"):
            print(f"      {item['url']}")
    artifact = build_artifact(chosen)

    if len(chosen) != PUBLISH_COUNT:
        print(
            f"\nnote: {len(chosen)} picked (the endpoint shows up to a few; "
            f"{PUBLISH_COUNT} is the usual daily count)."
        )

    confirm = (
        args.pick is not None
        or input(f"\nwrite {len(chosen)} items to {args.out}? [y/N] ").strip().lower()
        == "y"
    )
    if not confirm:
        print("not written.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {args.out}  (version {artifact['briefings_version']})")
    print("Legacy briefing draft only. Use curate_news_rich.py for the current news channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
