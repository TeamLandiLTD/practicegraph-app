"""The documentation shelf: official harness references as a served artifact.

The shelf is published JSON (FR-EMT-4 pattern, same as skills / news /
models): TeamLandi organizes the sections centrally and every install picks
the shape up on the daily pull, so reorganizing the shelf is a publish, not
a client release. The bundled default below always exists, the parser is a
closed schema that returns None on ANY deviation, and every link is https
or the whole document is refused — a served artifact can never put a
non-https href on the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEMA = "practicegraph.docs/1"
DOCS_FILE_NAME = "docs.json"

_ROOT_KEYS = frozenset({"schema", "docs_version", "sections"})
_SECTION_KEYS = frozenset({"title", "links"})
_LINK_KEYS = frozenset({"title", "url", "why"})
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
# Plain human text: printable, single-line, bounded. The page renders these
# verbatim, so control characters and newlines are refused outright.
_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,160}\Z")
_MAX_SECTIONS = 8
_MAX_LINKS = 10
_MAX_URL = 200


@dataclass(frozen=True)
class DocsLink:
    title: str
    url: str
    why: str


@dataclass(frozen=True)
class DocsSection:
    title: str
    links: tuple[DocsLink, ...]


@dataclass(frozen=True)
class DocsArtifact:
    docs_version: str
    sections: tuple[DocsSection, ...]


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or _TEXT.fullmatch(value) is None:
        return None
    return value if len(value) <= limit else None


def parse_docs_artifact(raw: object) -> DocsArtifact | None:
    """Parse a closed docs document, returning None on any invalid input."""
    if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS:
        return None
    if raw["schema"] != SCHEMA:
        return None
    version = raw["docs_version"]
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        return None
    raw_sections = raw["sections"]
    if not isinstance(raw_sections, list) or not 1 <= len(raw_sections) <= _MAX_SECTIONS:
        return None
    sections: list[DocsSection] = []
    for raw_section in raw_sections:
        if not isinstance(raw_section, dict) or set(raw_section) != _SECTION_KEYS:
            return None
        title = _text(raw_section["title"], 60)
        raw_links = raw_section["links"]
        if title is None or not isinstance(raw_links, list):
            return None
        if not 1 <= len(raw_links) <= _MAX_LINKS:
            return None
        links: list[DocsLink] = []
        for raw_link in raw_links:
            if not isinstance(raw_link, dict) or set(raw_link) != _LINK_KEYS:
                return None
            link_title = _text(raw_link["title"], 60)
            why = _text(raw_link["why"], 160)
            url = raw_link["url"]
            if (
                link_title is None
                or why is None
                or not isinstance(url, str)
                or len(url) > _MAX_URL
                or not url.startswith("https://")
            ):
                return None
            links.append(DocsLink(link_title, url, why))
        sections.append(DocsSection(title, tuple(links)))
    return DocsArtifact(version, tuple(sections))


EMPTY_DOCS = DocsArtifact("unavailable", ())
