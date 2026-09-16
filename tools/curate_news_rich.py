#!/usr/bin/env python3
"""Gather and safely publish human-curated PracticeGraph news.

Gathering creates a small, text-only reading pile. Publishing accepts a draft
only after the editor has read the sources and explicitly approved its copy.
This command never commits, pushes, or deploys the website repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from tools.curate_news import FEEDS  # noqa: E402

from practicegraph.analysis.news import (  # noqa: E402
    MAX_HOOK_LEN,
    MAX_SOURCE_LEN,
    MAX_SUMMARY_LEN,
    MAX_TITLE_LEN,
    MAX_URL_LEN,
    MAX_WHY_LEN,
    NEWS_KINDS,
    parse_news_artifact,
)
from practicegraph_server.rss import (  # type: ignore[import-untyped]  # noqa: E402
    _ATOM,
    _fetch_feed,
    _published_sort,
    _text,
)

DEFAULT_SITE_REPO = _REPO.parent / "practicegraph.dev"
NEWS_FILE_NAME = "news.json"
from tools.editorial_workspace import editorial_root  # noqa: E402

CANDIDATES_FILE = editorial_root() / "candidates" / "news.json"

_MAX_FEED_BYTES = 2_000_000
_FETCH_TIMEOUT_S = 12.0
_MAX_DESC_LEN = 1200
_MAX_PER_SOURCE = 15
_MAX_CANDIDATES = 30
_ITEM_FIELDS = ("id", "kind", "title", "hook", "summary", "why", "url", "source")
_TEXT_LIMITS = {
    "title": MAX_TITLE_LEN,
    "hook": MAX_HOOK_LEN,
    "summary": MAX_SUMMARY_LEN,
    "why": MAX_WHY_LEN,
    "source": MAX_SOURCE_LEN,
}


def _iter_entries(root: ET.Element) -> list[ET.Element]:
    return root.findall("channel/item") or root.findall(f"{_ATOM}entry")


def _entry_link(entry: ET.Element) -> str:
    link = entry.findtext("link") or ""
    if not link:
        atom_link = entry.find(f"{_ATOM}link")
        if atom_link is not None:
            link = atom_link.get("href") or ""
    return link.strip()


def _canonical_https_url(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return urllib.parse.urlunsplit(
        ("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
    )


def _entry_published(entry: ET.Element) -> str:
    return str(_published_sort(entry))[:80]


def gather(output: Path = CANDIDATES_FILE) -> int:
    """Fetch approved feeds and write at most 30 deduplicated candidates."""
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for spec in FEEDS:
        try:
            xml = _fetch_feed(spec.url, _MAX_FEED_BYTES, _FETCH_TIMEOUT_S)
        except Exception:
            xml = None
        if not xml:
            print(f"  skipped unreachable source: {spec.source}", file=sys.stderr)
            continue
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            print(f"  skipped unparseable source: {spec.source}", file=sys.stderr)
            continue
        count = 0
        for entry in _iter_entries(root):
            title = _text(entry.findtext("title") or entry.findtext(f"{_ATOM}title"))
            url = _canonical_https_url(_entry_link(entry))
            if not title or url is None or url in seen:
                continue
            seen.add(url)
            description = _text(
                entry.findtext("description")
                or entry.findtext(f"{_ATOM}summary")
                or entry.findtext(f"{_ATOM}content")
                or ""
            )[:_MAX_DESC_LEN]
            candidates.append(
                {
                    "source": spec.source,
                    "kind": spec.kind,
                    "title": title,
                    "url": url,
                    "description": description,
                    "published": _entry_published(entry),
                }
            )
            count += 1
            if count >= _MAX_PER_SOURCE:
                break
        print(f"  {spec.source}: {count} candidates", file=sys.stderr)

    candidates.sort(key=lambda item: (item["published"], item["url"]), reverse=True)
    candidates = candidates[:_MAX_CANDIDATES]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(candidates, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(candidates)} candidates to {output}")
    return 0


def _draft_errors(raw: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["draft must be a JSON object"]
    if not set(raw) <= {"news_version", "items"}:
        errors.append("draft root may contain only news_version and items")
    items = raw.get("items")
    if not isinstance(items, list):
        return [*errors, "items must be a list"]
    if not 1 <= len(items) <= 3:
        errors.append("items must contain between one and three entries")
    seen: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        missing = [field for field in _ITEM_FIELDS if field not in item]
        for field in missing:
            errors.append(f"{prefix}.{field} is required")
        unexpected = set(item) - set(_ITEM_FIELDS) - {"thumb", "thumb_src", "attention"}
        for field in sorted(unexpected):
            errors.append(f"{prefix}.{field} is not supported")
        news_id = item.get("id")
        if not isinstance(news_id, str) or not news_id.strip():
            errors.append(f"{prefix}.id must be non-empty text")
        elif news_id in seen:
            errors.append(f"{prefix}.id must be unique")
        else:
            seen.add(news_id)
        kind = item.get("kind")
        if kind not in NEWS_KINDS:
            errors.append(f"{prefix}.kind must be one of {', '.join(NEWS_KINDS)}")
        for field, limit in _TEXT_LIMITS.items():
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{prefix}.{field} must be non-empty text")
            elif len(value.strip()) > limit:
                errors.append(f"{prefix}.{field} exceeds {limit} characters")
        url = item.get("url")
        if not isinstance(url, str) or _canonical_https_url(url) is None:
            errors.append(f"{prefix}.url must be an HTTPS URL")
        elif len(url) > MAX_URL_LEN:
            errors.append(f"{prefix}.url exceeds {MAX_URL_LEN} characters")
    return errors


def _content_version(items: list[dict[str, object]]) -> str:
    encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"news-curated-{hashlib.sha256(encoded).hexdigest()[:12]}"


def load_validated_draft(path: Path) -> tuple[dict[str, object] | None, list[str]]:
    """Load, explain obvious errors, normalize, and run production validation."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, [f"cannot read draft: {exc}"]
    errors = _draft_errors(raw)
    if errors:
        return None, errors
    assert isinstance(raw, dict)
    raw_items = raw["items"]
    assert isinstance(raw_items, list)
    items = [{field: item[field] for field in _ITEM_FIELDS} for item in raw_items]
    for item, original in zip(items, raw_items, strict=True):
        if "attention" in original:
            item["attention"] = original["attention"]
    version = raw.get("news_version")
    if not isinstance(version, str) or not version.strip():
        version = _content_version(items)
    artifact: dict[str, object] = {"news_version": version, "items": items}
    if parse_news_artifact(artifact) is None:
        return None, [
            "draft failed production safety validation; check IDs, copy, URLs, and prohibited text"
        ]
    return artifact, []


def validate(draft_path: Path) -> int:
    artifact, errors = load_validated_draft(draft_path)
    if artifact is None:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    items = cast(list[object], artifact["items"])
    print(f"valid draft: {len(items)} item(s), {artifact['news_version']}")
    return 0


def publish(draft_path: Path, site_repo: Path) -> int:
    """Atomically replace only the site's news.json after full validation."""
    artifact, errors = load_validated_draft(draft_path)
    if artifact is None:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if not site_repo.is_dir():
        print(f"site repository not found: {site_repo}", file=sys.stderr)
        return 1
    target = site_repo / NEWS_FILE_NAME
    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(dir=site_repo, prefix=".news-", suffix=".tmp")
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(artifact, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        temp_path = None
    except OSError as exc:
        print(f"could not write {target}: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print(f"wrote {target}; review its Git diff before any separate publish action")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gather_parser = sub.add_parser("gather", help="write a text-only reading pile")
    gather_parser.add_argument("--out", type=Path, default=CANDIDATES_FILE)
    validate_parser = sub.add_parser("validate", help="validate an editorial draft")
    validate_parser.add_argument("draft", type=Path)
    publish_parser = sub.add_parser("publish", help="write approved news.json")
    publish_parser.add_argument("draft", type=Path)
    publish_parser.add_argument("--site", type=Path, default=DEFAULT_SITE_REPO)
    args = parser.parse_args()
    if args.command == "gather":
        return gather(args.out)
    if args.command == "validate":
        return validate(args.draft)
    return publish(args.draft, args.site)


if __name__ == "__main__":
    raise SystemExit(main())
