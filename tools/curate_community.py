#!/usr/bin/env python3
"""Gather and validate the human-curated PracticeGraph community edition.

Gathering builds a small, text-only reading pile from public developer
forums. Validation runs the engine's own strict parser over a drafted
edition. This command never commits, pushes, or deploys the website repo,
and it never publishes without an explicit publish invocation.

Access is anonymous and deliberately slow. Reddit blocks unauthenticated
HTTP JSON and rate-limits bursts, so every request goes through _polite():
a fixed floor between calls and exponential backoff on 429. A full daily
pass is roughly twenty requests and takes about a minute — that is the
price of needing no account, no API key and no OAuth flow.

Sorting is by recency and activity only. Score is never a ranking key:
the reconnaissance behind this reading found the week's most useful
measurement sitting at two points while the top-of-week slots held jokes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from tools.editorial_workspace import editorial_root  # noqa: E402

from practicegraph.analysis.community import (  # noqa: E402
    COMMUNITY_KINDS,
    MAX_COMMUNITY,
    parse_community_artifact,
)

CANDIDATES_FILE = editorial_root() / "candidates" / "community.json"
COMMUNITY_FILE_NAME = "community.json"
DEFAULT_SITE_REPO = _REPO.parent / "practicegraph.dev"

# The reading pile. Developer-practice forums only: places where people
# report what they measured, not general AI news.
SUBREDDITS: tuple[str, ...] = ("codex", "ClaudeAI", "ChatGPTCoding")

# Sorts worth reading. "top" is deliberately absent — see the module docstring.
SORTS: tuple[str, ...] = ("new", "hot")

_PAUSE_S = 6.0  # polite floor between requests; anonymous access is fragile
_MAX_TRIES = 4
_PER_LISTING = 25
_MIN_COMMENTS = 4  # below this a thread has no resolution to read
_MAX_BODY_LEN = 900
_MAX_COMMENT_LEN = 420
_MAX_COMMENTS_KEPT = 4
_MAX_THREADS = 12  # how many threads get their comments pulled

# Titles worth a curator's attention: the vocabulary of measured practice.
_SUBSTANTIVE = re.compile(
    r"limit|rate|quota|usage|token|cost|price|plan|context|compact|error|fail|"
    r"fix|bug|slow|workflow|prompt|agent|mcp|skill|model|config|setting|"
    r"version|update|release|dumb|memory|subagent",
    re.I,
)
# Titles that are jokes, drama or self-promotion. Dropped before a human reads.
_NOISE = re.compile(
    r"\bmeme\b|lol|haha|average .* user|\bhate\b|drama|rant|why we can|"
    r"showcase|show r/|i built|i made|check out my|my new app",
    re.I,
)

def _polite[T](call: Callable[[], T]) -> T:
    """One request, paced, with exponential backoff on 429.

    Every network call in this module goes through here. Unpaced bursts get
    throttled within seconds; a paced pass of ~20 requests never is.
    """
    from redditwarp.http.exceptions import StatusCodeException  # type: ignore[import-untyped]

    delay = _PAUSE_S
    for attempt in range(_MAX_TRIES):
        try:
            result = call()
            time.sleep(_PAUSE_S)
            return result
        except StatusCodeException as exc:
            if getattr(exc, "status_code", None) != 429 or attempt == _MAX_TRIES - 1:
                raise
            delay *= 2
            print(f"    rate limited; waiting {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError("unreachable: _MAX_TRIES exhausted without raising")


def _client() -> Any:
    """The anonymous Reddit client.

    redditwarp needs a real HTTP backend; without httpx installed it falls
    back to urllib and fails on the first request with a confusing error, so
    the missing dependency is reported plainly here instead.
    """
    try:
        import redditwarp.SYNC as reddit  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - environment guidance
        raise SystemExit(
            "redditwarp is not installed. Install it and httpx into a venv:\n"
            "  python -m venv .venv-community\n"
            "  .venv-community/Scripts/python.exe -m pip install redditwarp httpx"
        ) from None
    try:
        import httpx  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:  # pragma: no cover - environment guidance
        raise SystemExit(
            "httpx is not installed; redditwarp falls back to urllib and fails. "
            "Install httpx into the same environment."
        ) from None
    return reddit.Client()


def _list_posts(pull: Any, subreddit: str) -> list[Any]:
    return list(pull(subreddit, amount=_PER_LISTING))


def _fetch_tree(client: Any, post_id: int) -> Any:
    return client.p.comment_tree.fetch(post_id, sort="top", limit=8)


def _worth_reading(title: str, comments: int) -> bool:
    if comments < _MIN_COMMENTS:
        return False
    return bool(_SUBSTANTIVE.search(title)) and not _NOISE.search(title)


def gather(output: Path = CANDIDATES_FILE, threads: int = _MAX_THREADS) -> int:
    """Build the reading pile: listings first, then comments for the most
    discussed candidates. Comments are where a thread's resolution lives, so
    a candidate without them is a headline, not a finding."""
    client = _client()
    seen: set[str] = set()
    candidates: list[dict[str, object]] = []

    for subreddit in SUBREDDITS:
        for sort in SORTS:
            pull = getattr(client.p.subreddit.pull, sort)
            try:
                posts = _polite(partial(_list_posts, pull, subreddit))
            except Exception as exc:
                print(f"  r/{subreddit} {sort}: unavailable ({type(exc).__name__})",
                      file=sys.stderr)
                continue
            kept = 0
            for post in posts:
                if post.id in seen:
                    continue
                seen.add(post.id)
                title = str(post.title)
                if not _worth_reading(title, int(post.comment_count)):
                    continue
                body = " ".join(str(getattr(post, "body", "") or "").split())
                # Two ids, deliberately. `id` is the numeric one the comment
                # API expects; `id36` is the base-36 one people see. Passing
                # the numeric id as a STRING makes the comment fetch look for
                # a base-36 id that does not exist, and building a permalink
                # from it produces a URL that resolves to nothing — so the
                # canonical `permalink` is what gets published, never a
                # hand-assembled link.
                candidates.append(
                    {
                        "id": int(post.id),
                        "id36": str(post.id36),
                        "subreddit": f"r/{subreddit}",
                        "sort": sort,
                        "title": title,
                        "comments": int(post.comment_count),
                        "body": body[:_MAX_BODY_LEN],
                        "url": str(post.permalink),
                        "top_comments": [],
                    }
                )
                kept += 1
            print(f"  r/{subreddit:16} {sort:4} {len(posts):3} posts, {kept} kept")

    # Most-discussed first: a thread with replies has something to resolve.
    # This orders which threads are READ, never which cards are shown.
    candidates.sort(key=lambda c: -int(str(c["comments"])))
    for candidate in candidates[:threads]:
        try:
            post_id = int(str(candidate["id"]))
            tree = _polite(partial(_fetch_tree, client, post_id))
        except Exception as exc:
            candidate["top_comments"] = [f"unavailable ({type(exc).__name__})"]
            continue
        replies: list[str] = []
        for node in tree.children[:8]:
            text = " ".join(str(getattr(node.value, "body", "") or "").split())
            if len(text) > 80:
                replies.append(text[:_MAX_COMMENT_LEN])
        candidate["top_comments"] = replies[:_MAX_COMMENTS_KEPT]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\nwrote {len(candidates)} candidates to {output}")
    print(f"comments pulled for the {min(threads, len(candidates))} most discussed")
    return 0


def validate(draft_path: Path) -> int:
    """Run the engine's own parser over a drafted edition.

    The parser is deliberately all-or-nothing: one bad field kills the whole
    edition rather than yielding a partial one, so a failure here means the
    draft would have been rejected on every client.
    """
    try:
        raw = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {draft_path}: {exc}", file=sys.stderr)
        return 1
    edition = parse_community_artifact(raw)
    if edition is None:
        print("INVALID: the edition would be rejected by every client.",
              file=sys.stderr)
        print("Check: closed root keys (community_version, items); kind in "
              f"{list(COMMUNITY_KINDS)}; answers is a real finding id; url is "
              f"https; caps (title 120, hook 220, finding 400, source 40); "
              f"1..{MAX_COMMUNITY} items; no forbidden lexicon or leak "
              "patterns in any copy field.", file=sys.stderr)
        return 1
    print(f"valid: {len(edition.items)} items")
    for item in edition.items:
        link = f" -> {item.answers}" if item.answers else ""
        print(f"  [{item.kind:9}] {item.title[:64]}{link}")
    return 0


def publish(draft_path: Path, site_repo: Path) -> int:
    """Copy a validated edition into the website repository working tree.

    Writes one file and stops. Committing, pushing and deploying are separate
    actions that need their own explicit request.
    """
    if validate(draft_path) != 0:
        return 1
    if not (site_repo / ".git").exists():
        print(f"not a git worktree: {site_repo}", file=sys.stderr)
        return 1
    target = site_repo / COMMUNITY_FILE_NAME
    target.write_text(draft_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"wrote {target}")
    print("Nothing was committed, pushed or deployed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gather_cmd = sub.add_parser("gather", help="build the reading pile")
    gather_cmd.add_argument("--output", type=Path, default=CANDIDATES_FILE)
    gather_cmd.add_argument("--threads", type=int, default=_MAX_THREADS)

    validate_cmd = sub.add_parser("validate", help="check a drafted edition")
    validate_cmd.add_argument("draft", type=Path)

    publish_cmd = sub.add_parser("publish", help="write a validated edition to the site")
    publish_cmd.add_argument("draft", type=Path)
    publish_cmd.add_argument("--site", type=Path, default=DEFAULT_SITE_REPO)

    args = parser.parse_args()
    if args.command == "gather":
        return gather(args.output, args.threads)
    if args.command == "validate":
        return validate(args.draft)
    return publish(args.draft, args.site)


if __name__ == "__main__":
    raise SystemExit(main())
