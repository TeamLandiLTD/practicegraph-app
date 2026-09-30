"""The community reading: what practitioners measured, from the outside.

Practitioners hit the same walls this product measures from the inside —
quota windows, context weight, routing, compaction — and they report them
days before any vendor documents them. Each CommunityItem is one such
observation in the curator's own words, with a permalink to where it was
observed and, when it applies, the id of the finding this engine already
detects for the same thing.

Trust posture is the strictest in the product: the source material is
user-generated text on an open write surface, so a published edition is
parsed as UNTRUSTED display copy — closed schema, length caps, the
FR-FOC-8 forbidden lexicon and the no-leak patterns over every copy field.
No runnable field, no raw HTML. The url is https-only and display-only:
the endpoint never fetches it. Which items a person read stays local
(NFR-PRV-6).

Never ordered by score. ``discussion`` is corroboration a reader can see,
never a ranking key — the reconnaissance behind this reading found the
week's most useful measurement sitting at two points while the top slots
held jokes.

Determinism (INV-6): closed schema, fixed iteration order, plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from practicegraph.analysis.insights import FINDING_IDS
from practicegraph.privacy import leak_findings, lexicon_violations

# Closed set: what kind of observation the card carries.
COMMUNITY_KINDS: tuple[str, ...] = ("problem", "discovery", "signal", "practice")
COMMUNITY_KIND_LABELS: dict[str, str] = {
    "problem": "A wall people hit",
    "discovery": "Measured, not announced",
    "signal": "What changed",
    "practice": "How people work",
}

MAX_COMMUNITY = 12  # a daily shelf, not a catalog
MAX_TITLE_LEN = 120
MAX_HOOK_LEN = 220  # the card face
MAX_FINDING_LEN = 400  # what the thread landed on
MAX_SOURCE_LEN = 40
MAX_URL_LEN = 300
MAX_DISCUSSION = 100_000  # a reply count, sanity-bounded

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_URL_RE = re.compile(r"^https://[^\s]{1,290}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True, slots=True)
class CommunityItem:
    """One community observation. hook is the card face; finding is what the
    thread actually landed on; answers names the engine finding it speaks to."""

    item_id: str
    kind: str  # one of COMMUNITY_KINDS
    title: str
    hook: str
    finding: str
    url: str
    source: str
    observed: str
    answers: str | None = None  # one of FINDING_IDS
    discussion: int | None = None  # reply count, display-only


@dataclass(frozen=True, slots=True)
class CommunityEdition:
    """One validated edition."""

    items: tuple[CommunityItem, ...]


def _clean_text(value: object, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not 0 < len(text) <= max_len:
        return None
    if lexicon_violations(text) or leak_findings(text):
        return None
    return text


def parse_community_artifact(raw: object) -> CommunityEdition | None:
    """Strict validation of a community artifact. Closed schema, caps, closed
    kind enum, answers checked against the engine's own finding ids, https-only
    display url, and the lexicon + leak scans over every copy field. None on
    any deviation — our own published file is parsed as untrusted, like every
    catalog, and a deviation kills the edition rather than yielding part of it.
    """
    if not isinstance(raw, dict):
        return None
    if set(raw.keys()) != {"community_version", "items"}:
        return None
    version = raw["community_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    items = raw["items"]
    if not isinstance(items, list) or not 0 < len(items) <= MAX_COMMUNITY:
        return None

    required = {"id", "kind", "title", "hook", "finding", "url", "source", "observed"}
    optional = {"answers", "discussion"}

    parsed: list[CommunityItem] = []
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            return None
        entry_keys = set(entry.keys())
        if not required <= entry_keys or not entry_keys <= required | optional:
            return None

        item_id = entry["id"]
        if not isinstance(item_id, str) or not _ID_RE.match(item_id) or item_id in seen:
            return None
        if entry["kind"] not in COMMUNITY_KINDS:
            return None

        title = _clean_text(entry["title"], MAX_TITLE_LEN)
        hook = _clean_text(entry["hook"], MAX_HOOK_LEN)
        finding = _clean_text(entry["finding"], MAX_FINDING_LEN)
        source = _clean_text(entry["source"], MAX_SOURCE_LEN)
        if title is None or hook is None or finding is None or source is None:
            return None

        url = entry["url"]
        if not isinstance(url, str) or len(url) > MAX_URL_LEN or not _URL_RE.match(url):
            return None

        observed = entry["observed"]
        if not isinstance(observed, str) or not _DAY_RE.match(observed):
            return None

        answers: str | None = None
        if "answers" in entry:
            raw_answers = entry["answers"]
            if not isinstance(raw_answers, str) or raw_answers not in FINDING_IDS:
                return None
            answers = raw_answers

        discussion: int | None = None
        if "discussion" in entry:
            raw_discussion = entry["discussion"]
            if (
                not isinstance(raw_discussion, int)
                or isinstance(raw_discussion, bool)
                or not 0 <= raw_discussion <= MAX_DISCUSSION
            ):
                return None
            discussion = raw_discussion

        seen.add(item_id)
        parsed.append(
            CommunityItem(
                item_id=item_id,
                kind=str(entry["kind"]),
                title=title,
                hook=hook,
                finding=finding,
                url=url,
                source=source,
                observed=observed,
                answers=answers,
                discussion=discussion,
            )
        )
    return CommunityEdition(items=tuple(parsed))


# No bundled first edition ships with the product: unlike build ideas, a
# community observation is dated and about a moment, and a stale one shown on
# a fresh install would be a claim about a week the reader never saw. An
# install with no pulled artifact shows nothing here — withheld, not filled.
EMPTY_COMMUNITY_EDITION = CommunityEdition(items=())
