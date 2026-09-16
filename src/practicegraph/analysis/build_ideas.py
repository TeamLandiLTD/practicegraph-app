"""Let's build: what the APIs behind these tools can do, one recipe at a time.

The harnesses people run all day sit on APIs most of them have never called
directly — web search that grounds an answer, vector stores, code execution,
batch pricing, structured outputs. Each BuildIdea is one concrete thing a
person could build this week with one of those capabilities: what it is in
plain words, why it is worth an afternoon, and the three-to-six steps that
get a first version running. Editorial, in the product's own voice — never
hype, never a benchmark chase.

Trust posture is identical to curated news: inbound copy is UNTRUSTED display
text — closed schema, length caps, the FR-FOC-8 forbidden lexicon, and the
no-leak patterns over every copy field. No runnable field, no raw HTML. The
url is https-only and display-only (an official docs page a person chooses to
open; the endpoint never fetches it). Which ideas a person read stays local
(NFR-PRV-6).

Determinism (INV-6): closed schema, sorted iteration, plain text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from practicegraph.privacy import leak_findings, lexicon_violations

IDEAS_VERSION = "build-ideas-bundled"

# Closed set: which vendor's API the idea builds on. Labels are for the page.
IDEA_APIS: tuple[str, ...] = ("claude", "openai")
IDEA_API_LABELS: dict[str, str] = {"claude": "Claude API", "openai": "OpenAI API"}

MAX_IDEAS = 12  # a daily shelf, not a catalog
MAX_FEATURE_LEN = 60   # the capability tag ("Web search tool")
MAX_TITLE_LEN = 120
MAX_HOOK_LEN = 220     # the card face: what you could build, three lines
MAX_SUMMARY_LEN = 600  # the expanded read: how it works, what to watch
MAX_STEP_LEN = 160
MIN_STEPS, MAX_STEPS = 2, 6
MAX_WHY_LEN = 280
MAX_SOURCE_LEN = 40
MAX_URL_LEN = 300

# The GitHub shelf: hand-picked repositories, never scraped popularity.
# Star counts may appear inside prose (sourced and dated) but nothing here
# ranks by them - the product's anti-hype rule applies to lists too.
MAX_REPOS = 6
MAX_REPO_NAME_LEN = 80    # "owner/name" or the project's plain name
MAX_REPO_WHAT_LEN = 220   # what it is, in plain words
MAX_REPO_WHY_LEN = 280    # why it is worth the reader's time
MAX_REPO_CAVEAT_LEN = 160  # the honest note, when one is due

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
# Repo links are github.com project URLs only - owner/name, nothing deeper,
# no query strings, no tracking.
_REPO_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]{1,60}/[A-Za-z0-9_.-]{1,80}/?$"
)
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_URL_RE = re.compile(r"^https://[^\s]{1,290}$")

_SCANNED_TEXT_FIELDS = ("feature", "title", "hook", "summary", "why", "source")


@dataclass(frozen=True, slots=True)
class BuildIdea:
    """One buildable idea. hook is the card face; summary the expanded read;
    steps the first-version recipe; url the official docs page it rests on."""

    idea_id: str
    api: str        # one of IDEA_APIS
    feature: str    # the capability in the vendor's own words
    title: str
    hook: str
    summary: str
    steps: tuple[str, ...]
    why: str
    url: str
    source: str
    # A receipt, when the edition has one: an open-source project that does
    # what the idea describes. github.com project URL, display-only.
    repo: str | None = None


@dataclass(frozen=True, slots=True)
class RepoPick:
    """One hand-picked repository on the "worth a look" shelf."""

    repo_id: str
    name: str      # "owner/name" or the project's plain name
    url: str       # github.com project URL, display-only
    what: str      # what it is, in plain words
    why: str       # why it is worth the reader's time
    caveat: str | None = None


@dataclass(frozen=True, slots=True)
class BuildEdition:
    """One validated edition: the ideas and the repo shelf together."""

    ideas: tuple[BuildIdea, ...]
    repos: tuple[RepoPick, ...]


def _clean_text(value: object, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not 0 < len(text) <= max_len:
        return None
    if lexicon_violations(text) or leak_findings(text):
        return None
    return text


def _parse_repo_pick(entry: object, seen: set[str]) -> RepoPick | None:
    """One shelf repository, strictly: closed keys, github project URL,
    scans over every copy field."""
    required = {"id", "name", "url", "what", "why"}
    if not isinstance(entry, dict):
        return None
    keys = set(entry.keys())
    if not required <= keys or not keys <= required | {"caveat"}:
        return None
    repo_id = entry["id"]
    if not isinstance(repo_id, str) or not _ID_RE.match(repo_id) \
            or repo_id in seen:
        return None
    name = _clean_text(entry["name"], MAX_REPO_NAME_LEN)
    what = _clean_text(entry["what"], MAX_REPO_WHAT_LEN)
    why = _clean_text(entry["why"], MAX_REPO_WHY_LEN)
    if name is None or what is None or why is None:
        return None
    caveat: str | None = None
    if "caveat" in entry:
        caveat = _clean_text(entry["caveat"], MAX_REPO_CAVEAT_LEN)
        if caveat is None:
            return None
    url = entry["url"]
    if not isinstance(url, str) or not _REPO_URL_RE.match(url):
        return None
    seen.add(repo_id)
    return RepoPick(
        repo_id=repo_id, name=name, url=url, what=what, why=why, caveat=caveat
    )


def parse_build_ideas_artifact(raw: object) -> BuildEdition | None:
    """Strict validation of a build-ideas artifact. Closed schema, caps,
    closed api enum, https-only display url, and the lexicon + leak scans
    over every copy field. None on any deviation — our own published file
    is parsed as untrusted, like every catalog.

    The optional root ``repos`` list is the hand-picked GitHub shelf; an
    edition without one parses as an edition with an empty shelf. Each idea
    may carry an optional ``repo`` receipt - a github.com project URL."""
    if not isinstance(raw, dict):
        return None
    root_keys = set(raw.keys())
    if not {"ideas_version", "items"} <= root_keys \
            or not root_keys <= {"ideas_version", "items", "repos"}:
        return None
    version = raw["ideas_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    items = raw["items"]
    if not isinstance(items, list) or not 0 < len(items) <= MAX_IDEAS:
        return None

    required = {
        "id", "api", "feature", "title", "hook", "summary", "steps", "why",
        "url", "source",
    }

    parsed: list[BuildIdea] = []
    seen: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            return None
        entry_keys = set(entry.keys())
        if not required <= entry_keys or not entry_keys <= required | {"repo"}:
            return None

        idea_id = entry["id"]
        if not isinstance(idea_id, str) or not _ID_RE.match(idea_id) \
                or idea_id in seen:
            return None
        if entry["api"] not in IDEA_APIS:
            return None

        feature = _clean_text(entry["feature"], MAX_FEATURE_LEN)
        title = _clean_text(entry["title"], MAX_TITLE_LEN)
        hook = _clean_text(entry["hook"], MAX_HOOK_LEN)
        summary = _clean_text(entry["summary"], MAX_SUMMARY_LEN)
        why = _clean_text(entry["why"], MAX_WHY_LEN)
        source = _clean_text(entry["source"], MAX_SOURCE_LEN)
        if feature is None or title is None or hook is None or summary is None \
                or why is None or source is None:
            return None

        raw_steps = entry["steps"]
        if not isinstance(raw_steps, list) \
                or not MIN_STEPS <= len(raw_steps) <= MAX_STEPS:
            return None
        steps: list[str] = []
        for raw_step in raw_steps:
            step = _clean_text(raw_step, MAX_STEP_LEN)
            if step is None:
                return None
            steps.append(step)

        url = entry["url"]
        if not isinstance(url, str) or len(url) > MAX_URL_LEN \
                or not _URL_RE.match(url):
            return None

        repo: str | None = None
        if "repo" in entry:
            raw_repo = entry["repo"]
            if not isinstance(raw_repo, str) or not _REPO_URL_RE.match(raw_repo):
                return None
            repo = raw_repo

        seen.add(idea_id)
        parsed.append(
            BuildIdea(
                idea_id=idea_id,
                api=str(entry["api"]),
                feature=feature,
                title=title,
                hook=hook,
                summary=summary,
                steps=tuple(steps),
                why=why,
                url=url,
                source=source,
                repo=repo,
            )
        )

    picks: list[RepoPick] = []
    if "repos" in raw:
        raw_repos = raw["repos"]
        if not isinstance(raw_repos, list) or len(raw_repos) > MAX_REPOS:
            return None
        repo_seen: set[str] = set()
        for raw_pick in raw_repos:
            pick = _parse_repo_pick(raw_pick, repo_seen)
            if pick is None:
                return None
            picks.append(pick)
    return BuildEdition(ideas=tuple(parsed), repos=tuple(picks))


EMPTY_EDITION = BuildEdition(ideas=(), repos=())
