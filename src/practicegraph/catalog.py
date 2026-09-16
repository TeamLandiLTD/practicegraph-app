"""Catalog pull (FR-EMT-4, client side).

Pulls versioned catalog artifacts from the backend under a single
catalog-wide coordination version. Every failure classifies into a closed
code and never blocks local analysis — the bundled defaults always exist.
Artifacts are strictly validated before they touch disk; a server can never
push free text or a malformed card into the endpoint.
"""

from __future__ import annotations

import contextlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from practicegraph import nethttp
from practicegraph.analysis.advisor_market import (
    AdvisorArtifact,
    parse_advisor_artifact,
)
from practicegraph.analysis.briefings import parse_briefings_artifact
from practicegraph.analysis.build_ideas import (
    EMPTY_EDITION,
    BuildEdition,
    parse_build_ideas_artifact,
)
from practicegraph.analysis.community import (
    EMPTY_COMMUNITY_EDITION,
    CommunityEdition,
    parse_community_artifact,
)
from practicegraph.analysis.docs import (
    EMPTY_DOCS,
    DocsArtifact,
    parse_docs_artifact,
)
from practicegraph.analysis.harness_playbooks import PlaybookEdition, parse_playbooks
from practicegraph.analysis.model_catalog import (
    CATALOG_FILE_NAME,
    EMPTY_CATALOG,
    ModelCatalog,
    parse_model_catalog,
)
from practicegraph.analysis.model_intelligence import ModelArtifact, parse_model_artifact
from practicegraph.analysis.news import NewsItem, parse_news_artifact
from practicegraph.analysis.practices import parse_practices_artifact
from practicegraph.analysis.ratecard import (
    CATALOG_DIR_NAME,
    RATE_CARD_FILE_NAME,
    parse_rate_card_artifact,
)
from practicegraph.analysis.skills import parse_skills_artifact
from practicegraph.analysis.token_prices import check_transition, parse_token_prices
from practicegraph.analysis.training_catalog import TrainingEdition, parse_training
from practicegraph.catalog_crypto import MAX_ENVELOPE_BYTES, MAX_PLAINTEXT_BYTES, open_catalog
from practicegraph.config import DEFAULT_SKILLS_SOURCE_URL, Config, read_prefs
from practicegraph.content import (
    CHANNELS,
    DEFAULT_CONTENT_BASE_URL,
    content_digest,
    edition_date,
    edition_version,
    profile_url,
    public_source_url,
)
from practicegraph.store import Store

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Annotation-only: the runtime import stays inside the update functions so
    # the release-check path is loaded lazily, like every other pull here.
    from practicegraph.analysis.update import UpdateOffer

PRACTICES_FILE_NAME = "practices.json"
SKILLS_FILE_NAME = "skills.json"
BRIEFINGS_FILE_NAME = "briefings.json"
NEWS_FILE_NAME = "news.json"
MODELS_FILE_NAME = "models.json"
ADVISOR_FILE_NAME = "advisor.json"
DOCS_FILE_NAME = "docs.json"
_META_LAST_DOCS_PULL_DAY = "docs_catalog_pull_day"

BUILD_IDEAS_FILE_NAME = "build-ideas.json"
COMMUNITY_FILE_NAME = "community.json"
_META_LAST_COMMUNITY_ATTEMPT_AT = "community_public_last_attempt_at"

# Dated history of the editorial feeds (news + build ideas), so a reader can
# go back to a previous day. One file per UTC day per channel, written only
# after the artifact passes its strict parse, kept for a bounded window and
# pruned on every write. The current file stays the single source for
# "today"; history is a read-only shelf behind it.
HISTORY_DIR_NAME = "history"
HISTORY_KEEP_DAYS = 14  # "at least a week", with margin
_HISTORY_CHANNELS = ("news", "build-ideas", "community")
_HISTORY_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TEAMLANDI_SKILLS_URL = DEFAULT_SKILLS_SOURCE_URL
SKILLS_SOURCE_ENTERPRISE = "enterprise"
_META_SKILLS_ACCEPTED_SOURCE = "skills_catalog_accepted_source"
_SKILLS_SOURCE_INVALIDATED = ""
SkillSource = Literal["teamlandi_public", "enterprise", "custom_public", "bundled"]

# Closed pull outcome codes (surfaced in tick outcomes and doctor).
PULL_CODES: tuple[str, ...] = (
    "pulled",
    "unchanged",
    "skipped_unconfigured",
    "skipped_already_today",
    "skipped_recently",
    "skipped_enterprise_configured",
    "server_unavailable",
    "timeout",
    "http_error",
    "invalid_artifact",
    "transport_error",
)

_META_CATALOG_VERSION = "catalog_version"
_META_LAST_PULL_DAY = "catalog_last_pull_day"

_MAX_ARTIFACT_BYTES = 262_144
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _get_json_any(
    url: str, timeout_s: float = 10.0, *, harden: bool = True,
    max_bytes: int | None = None, allow_list: bool = False,
) -> dict[str, object] | list[object] | str:
    """GET a JSON document; returns the dict (or, opt-in, list) or a closed
    error code.

    Public pulls (``harden=True``, the default) fetch attacker-influenceable
    URLs, so the request is https on every hop and a redirect to an internal /
    loopback / private address is refused rather than followed (SSRF defense).
    The enterprise backend pull (``harden=False``) is the admin-configured,
    trusted origin — possibly an http intranet host — and keeps the original
    permissive fetch.

    ``max_bytes`` widens the default artifact bound for the one endpoint
    that legitimately exceeds it: GitHub's release LISTS carry the full
    asset metadata of every release (a single Codex release runs ~270KB,
    which is also why the old /releases/latest pull silently never landed
    — it hit this cap). ``allow_list`` admits a top-level JSON array for
    the same endpoint; everything else stays dict-only."""
    bound = max_bytes if max_bytes is not None else _MAX_ARTIFACT_BYTES
    try:
        if harden:
            raw = nethttp.fetch_bounded(url, bound, timeout_s)
        else:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw = response.read(bound + 1)
    except urllib.error.HTTPError:
        return "http_error"
    except TimeoutError:
        return "timeout"
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            return "timeout"
        return "server_unavailable"
    except OSError:
        return "transport_error"
    if len(raw) > bound:
        return "invalid_artifact"
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "invalid_artifact"
    if isinstance(document, dict):
        return document
    if allow_list and isinstance(document, list):
        return document
    return "invalid_artifact"


def _get_json(
    url: str, timeout_s: float = 10.0, *, harden: bool = True, max_bytes: int | None = None,
) -> dict[str, object] | str:
    """The dict-only fetch every artifact pull uses; see _get_json_any."""
    result = _get_json_any(url, timeout_s, harden=harden, max_bytes=max_bytes)
    return "invalid_artifact" if isinstance(result, list) else result


def _get_catalog_json(url: str, channel: str) -> dict[str, object] | str:
    """Decrypt before the existing production parser/cache boundary; never fetch a key URL.

    Public pulls accept sealed editions only: the publisher signature, not the
    host, is what makes downloaded prompts, playbooks and model pins trustworthy.
    Enterprise catalogs (``pull_catalog``) come from the operator's own server
    and do not pass through here."""
    document = _get_json(url, max_bytes=MAX_ENVELOPE_BYTES)
    if isinstance(document, str):
        return document
    try:
        result = open_catalog(document, channel, require_sealed=True)
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_PLAINTEXT_BYTES:
            return "invalid_artifact"
        return result
    except (ValueError, TypeError, RecursionError):
        return "invalid_artifact"


def pull_catalog(store: Store, config: Config, now: datetime) -> str:
    """Pull at most once per UTC day. Returns a closed PULL_CODES value."""
    if not config.api_base_url:
        return "skipped_unconfigured"
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_PULL_DAY) == today:
        return "skipped_already_today"
    base = config.api_base_url.rstrip("/")

    versions = _get_json(base + "/v1/catalog/versions", harden=False)
    if isinstance(versions, str):
        return versions
    coordination = versions.get("catalog_version")
    if not isinstance(coordination, str) or not _VERSION_RE.match(coordination):
        return "invalid_artifact"
    if coordination == store.meta_get(_META_CATALOG_VERSION):
        store.meta_set(_META_LAST_PULL_DAY, today)
        return "unchanged"

    artifact = _get_json(base + "/v1/catalog/rate-card", harden=False)
    if isinstance(artifact, str):
        return artifact
    if parse_rate_card_artifact(artifact) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(catalog_dir / RATE_CARD_FILE_NAME, artifact)

    # Practices ride the same coordination version. Optional: a server without
    # the artifact (or an invalid one) never blocks the rate-card pull.
    practices = _get_json(base + "/v1/catalog/practices", harden=False)
    if isinstance(practices, dict) and parse_practices_artifact(practices) is not None:
        _atomic_write(catalog_dir / PRACTICES_FILE_NAME, practices)

    # The skill registry rides the same coordination version, same discipline:
    # validated before it touches disk, and an absent/invalid artifact never
    # blocks the pull (the endpoint falls back to the bundled skills).
    skills = _get_json(base + "/v1/catalog/skills", harden=False)
    if isinstance(skills, dict) and parse_skills_artifact(skills) is not None:
        _accept_skills_artifact(
            store,
            catalog_dir / SKILLS_FILE_NAME,
            skills,
            SKILLS_SOURCE_ENTERPRISE,
        )

    # The news feed rides the same coordination version, same discipline.
    briefings = _get_json(base + "/v1/catalog/briefings", harden=False)
    if isinstance(briefings, dict) and parse_briefings_artifact(briefings) is not None:
        _atomic_write(catalog_dir / BRIEFINGS_FILE_NAME, briefings)

    store.meta_set(_META_CATALOG_VERSION, coordination)
    store.meta_set(_META_LAST_PULL_DAY, today)
    return "pulled"


# Independent (serverless) skill source: a public https URL the endpoint pulls
# skills DIRECTLY from when no enterprise server proxies them. One explicit,
# configured URL, https-only, validated identically to a server artifact — the
# deliberate opt-in relaxation of NFR-SEC-5 for the serverless case. When an
# api_base_url IS configured, the server-proxied skills (pull_catalog above)
# already wrote skills.json this cycle and take precedence; this only fills the
# gap for standalone agents, and never overwrites a same-day server pull.
_META_LAST_SKILLS_PULL_DAY = "skills_public_last_pull_day"
_META_LAST_NEWS_ATTEMPT_AT = "news_public_last_attempt_at"
_META_LAST_BUILD_IDEAS_ATTEMPT_AT = "build_ideas_public_last_attempt_at"
_META_LAST_MODELS_PULL_DAY = "models_public_last_pull_day"
NEWS_PULL_INTERVAL_S = 15 * 60
_META_LAST_ADVISOR_PULL_DAY = "advisor_public_last_pull_day"
_META_LAST_RATECARD_PULL_DAY = "ratecard_public_last_pull_day"

# Public skill catalogs can be larger than a rate card (a real registry of many
# skills), but still bounded.
_MAX_SKILLS_BYTES = 524_288


def pull_public_skills(store: Store, config: Config, now: datetime) -> str:
    """Pull skills directly from the configured public URL (independent mode).

    Runs at most once per UTC day. Skipped when enterprise mode is configured,
    when no public URL is set, or when the URL is not https. Returns a closed
    PULL_CODES value. The fetched
    artifact must pass the SAME parse_skills_artifact as any other source, so a
    public registry can never push unsafe copy or a runnable payload."""
    if config.api_base_url:
        return "skipped_enterprise_configured"
    url = (config.skills_source_url or "").strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"  # https only; no plaintext / file scheme
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_SKILLS_PULL_DAY) == today:
        return "skipped_already_today"
    document = _get_catalog_json(url, "skills")
    if isinstance(document, str):
        return document
    if parse_skills_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _accept_skills_artifact(store, catalog_dir / SKILLS_FILE_NAME, document, url)
    store.meta_set(_META_LAST_SKILLS_PULL_DAY, today)
    return "pulled"


def pull_public_news(store: Store, config: Config, now: datetime) -> str:
    """Pull validated curated news at most once per fifteen minutes.

    Enterprise gating, refined 2026-08-13. The news-curation decision stands:
    a direct public-URL pull may coexist with an enterprise server, so that
    curated news stays fresh despite the org catalog's version discipline —
    but only when someone actually CHOSE it. The url defaults to the public
    host, so without this source check every enterprise endpoint was calling
    that host on a 15-minute cadence by default, silently (NFR-SEC-5). Now:
    explicitly configured news URL -> pulls, enterprise or not; the untouched
    default on an enterprise endpoint -> skipped."""
    if config.api_base_url and config.news_source_url_source == "default":
        return "skipped_enterprise_configured"
    url = config.news_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    if not store.meta_claim_interval(
        _META_LAST_NEWS_ATTEMPT_AT, now, NEWS_PULL_INTERVAL_S
    ):
        return "skipped_recently"

    document = _get_catalog_json(url, "news")
    if isinstance(document, str):
        return document
    if parse_news_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _accept_editorial(config.data_dir, "news", document, url, now)
    _archive_artifact(
        config.data_dir, "news", now.astimezone(UTC).date().isoformat(), document
    )
    return "pulled"


def pull_public_build_ideas(store: Store, config: Config, now: datetime) -> str:
    """Pull the validated build-ideas edition at most once per fifteen
    minutes. Same enterprise gating as news: an explicitly configured URL
    pulls anywhere; the untouched default on an enterprise endpoint is
    skipped (NFR-SEC-5)."""
    if config.api_base_url and config.build_ideas_source_url_source == "default":
        return "skipped_enterprise_configured"
    url = config.build_ideas_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    if not store.meta_claim_interval(
        _META_LAST_BUILD_IDEAS_ATTEMPT_AT, now, NEWS_PULL_INTERVAL_S
    ):
        return "skipped_recently"

    document = _get_catalog_json(url, "build-ideas")
    if isinstance(document, str):
        return document
    if parse_build_ideas_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _accept_editorial(config.data_dir, "build-ideas", document, url, now)
    _archive_artifact(
        config.data_dir, "build-ideas", now.astimezone(UTC).date().isoformat(), document
    )
    return "pulled"


def pull_public_community(store: Store, config: Config, now: datetime) -> str:
    """Pull the validated community edition at most once per fifteen
    minutes. Same enterprise gating as news: an explicitly configured URL
    pulls anywhere; the untouched default on an enterprise endpoint is
    skipped (NFR-SEC-5)."""
    if config.api_base_url and config.community_source_url_source == "default":
        return "skipped_enterprise_configured"
    url = config.community_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    if not store.meta_claim_interval(
        _META_LAST_COMMUNITY_ATTEMPT_AT, now, NEWS_PULL_INTERVAL_S
    ):
        return "skipped_recently"

    document = _get_catalog_json(url, "community")
    if isinstance(document, str):
        return document
    if parse_community_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _accept_editorial(config.data_dir, "community", document, url, now)
    _archive_artifact(
        config.data_dir, "community", now.astimezone(UTC).date().isoformat(), document
    )
    return "pulled"


def pull_public_models(store: Store, config: Config, now: datetime) -> str:
    """Pull validated coding-model guidance daily in standalone mode."""
    if config.api_base_url and config.models_source_url_source == "default":
        return "skipped_enterprise_configured"
    url = config.models_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_MODELS_PULL_DAY) == today:
        return "skipped_already_today"

    document = _get_catalog_json(url, "models")
    if isinstance(document, str):
        return document
    if parse_model_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _accept_editorial(config.data_dir, "models", document, url, now)
    store.meta_set(_META_LAST_MODELS_PULL_DAY, today)
    return "pulled"


def pull_public_docs(store: Store, config: Config, now: datetime) -> str:
    """Download the active profile's shelf, independently of app releases."""
    return _pull_profile_catalog(
        store, config, now, "docs", config.docs_source_url, config.docs_source_url_source,
    )


def pull_public_model_catalog(store: Store, config: Config, now: datetime) -> str:
    """Download the active profile's model and effort guidance."""
    return _pull_profile_catalog(
        store, config, now, "model-catalog", config.model_catalog_source_url,
        config.model_catalog_source_url_source,
    )


def _pull_profile_catalog(
    store: Store, config: Config, now: datetime, channel: str, base_url: str, source: str,
) -> str:
    if config.api_base_url and source == "default":
        return "skipped_enterprise_configured"
    if not base_url.strip():
        return "skipped_unconfigured"
    profile = read_prefs(config.data_dir).profile
    try:
        url = profile_url(base_url.strip(), profile)
    except ValueError:
        return "invalid_artifact"
    if not url.startswith("https://"):
        return "invalid_artifact"
    if profile == "productivity":
        channel += "-productivity"
    # A profile or source change must not be delayed by another lane's attempt.
    key = f"content_attempt_{channel}_{content_digest(url)[:16]}"
    if not store.meta_claim_interval(key, now, NEWS_PULL_INTERVAL_S):
        return "skipped_recently"
    document = _get_catalog_json(url, channel)
    if isinstance(document, str):
        return document
    if CHANNELS[channel].parse(document) is None:
        return "invalid_artifact"
    _accept_editorial(config.data_dir, channel, document, url, now)
    return "pulled"


# The two harness repos whose releases the tools page watches. Pinned
# constants, https-only, one unauthenticated API read per repo per day —
# the same daily catalog cadence as everything else here.
HARNESS_RELEASE_REPOS: tuple[tuple[str, str], ...] = (
    ("claude_code", "anthropics/claude-code"),
    ("codex", "openai/codex"),
)
_META_LAST_RELEASES_PULL_DAY = "harness_releases_pull_day"
_RELEASE_TAG = re.compile(r"[A-Za-z-]{0,12}v?([0-9]+(?:\.[0-9]+){1,3}[0-9A-Za-z.+-]{0,24})\Z")


def _safe_release_url(document: object, repo: str) -> str:
    url = document.get("html_url") if isinstance(document, dict) else None
    if (
        isinstance(url, str)
        and url.startswith(f"https://github.com/{repo}/releases/")
        and len(url) <= 200
    ):
        return url
    return f"https://github.com/{repo}/releases/latest"


def pull_public_releases(store: Store, config: Config, now: datetime) -> str:
    """Fetch each harness's newest release tags once per UTC day — BOTH
    channels. Codex ships its actual work as prereleases (rust-v0.150.0-
    alpha.N) while /releases/latest only ever answers the last stable, so a
    person on the alpha channel was compared against the wrong ladder
    (field report 2026-08-22). One list request per repo yields the newest
    stable AND the newest release of any kind; the viewmodel compares the
    installed version against its own channel.

    Like the update manifest — and unlike the content catalogs — this is
    NOT gated on standalone mode: an enterprise install still wants to know
    its harnesses are out of date, and the tag tells the fleet nothing.
    Only tags and release page URLs are kept, validated against closed
    shapes; release bodies never touch the store. Failures are per-repo and
    fail-open: a repo that answers badly keeps its previous reading."""
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_RELEASES_PULL_DAY) == today:
        return "skipped_already_today"

    pulled_any = False
    failed_any = False
    for tool, repo in HARNESS_RELEASE_REPOS:
        document = _get_json_any(
            f"https://api.github.com/repos/{repo}/releases?per_page=10",
            timeout_s=10.0,
            # Release lists are asset-metadata heavy (Codex: ~2.8MB for
            # ten) — a widened, still-bounded fetch from the pinned host.
            max_bytes=6_291_456,
            allow_list=True,
        )
        if not isinstance(document, list):
            failed_any = True
            continue
        stable: tuple[str, str] | None = None
        newest: tuple[str, str] | None = None
        for entry in document:
            if not isinstance(entry, dict) or entry.get("draft") is True:
                continue
            raw_tag = entry.get("tag_name")
            match = (
                _RELEASE_TAG.fullmatch(raw_tag) if isinstance(raw_tag, str) else None
            )
            if match is None:
                continue
            candidate = (match.group(1), _safe_release_url(entry, repo))
            if newest is None:
                newest = candidate
            if stable is None and entry.get("prerelease") is not True:
                stable = candidate
            if newest is not None and stable is not None:
                break
        if newest is None:
            failed_any = True
            continue
        anchor = stable or newest
        store.meta_set(f"harness_latest_version_{tool}", anchor[0])
        store.meta_set(f"harness_latest_url_{tool}", anchor[1])
        store.meta_set(f"harness_newest_version_{tool}", newest[0])
        store.meta_set(f"harness_newest_url_{tool}", newest[1])
        pulled_any = True
    if pulled_any:
        store.meta_set(_META_LAST_RELEASES_PULL_DAY, today)
        return "pulled"
    return "http_error" if failed_any else "server_unavailable"


def load_harness_releases(store: Store) -> dict[str, tuple[str, str, str, str]]:
    """tool -> (stable version, stable url, newest-any version, newest-any
    url) for every tool with a cached reading. Values were validated at
    write time; a store written before the channel split falls back to the
    stable reading for both."""
    releases: dict[str, tuple[str, str, str, str]] = {}
    for tool, repo in HARNESS_RELEASE_REPOS:
        version = store.meta_get(f"harness_latest_version_{tool}")
        url = store.meta_get(f"harness_latest_url_{tool}")
        if not version:
            continue
        fallback_url = url or f"https://github.com/{repo}/releases/latest"
        newest = store.meta_get(f"harness_newest_version_{tool}") or version
        newest_url = store.meta_get(f"harness_newest_url_{tool}") or fallback_url
        releases[tool] = (version, fallback_url, newest, newest_url)
    return releases


def pull_public_ratecard(store: Store, config: Config, now: datetime) -> str:
    """Pull the served rate card once per UTC day in standalone mode.

    Model prices move faster than releases do: the bundled card was stale six
    days after publication (GPT-5.6 shipped 2026-07-09), and a stale card does
    not fail loudly — it prices a new generation at an old rate and shows the
    result with full confidence. Serving the card as an artifact turns a
    client-release problem into a publish, so a price change reaches every
    install the next day.

    Safety rests on three existing mechanisms, none of them new here:
    `parse_rate_card_artifact` is a closed schema that returns None on ANY
    deviation (never fail-open into garbage pricing); the pulled card replaces
    the bundled one only after validating; and `history.ingest` already
    re-prices all history whenever the active card version changes, so a
    corrected price retroactively fixes past estimates instead of leaving a
    seam in the middle of the record.
    """
    if config.api_base_url:
        return "skipped_enterprise_configured"
    url = config.ratecard_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_RATECARD_PULL_DAY) == today:
        return "skipped_already_today"

    document = _get_catalog_json(url, "rate-card")
    if isinstance(document, str):
        return document
    if parse_rate_card_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(catalog_dir / RATE_CARD_FILE_NAME, document)
    store.meta_set(_META_LAST_RATECARD_PULL_DAY, today)
    return "pulled"


UPDATE_FILE_NAME = "update.json"
_META_LAST_UPDATE_PULL_DAY = "update_manifest_pull_day"


def pull_update_manifest(store: Store, config: Config, now: datetime) -> str:
    """Fetch the signed release manifest once per UTC day.

    Unlike every other catalog pull, this one is NOT gated on standalone mode:
    an enterprise install still wants to know its client is out of date, and
    the manifest tells it nothing about the fleet. What does gate it is the
    signature — the document is cached only after `parse_update_manifest`
    verifies it against the key compiled into this build, so a repointed URL
    (the local-config-tamper path the 2026-07-19 review flagged as CRITICAL)
    yields a rejected document rather than a trusted one.

    The cached copy is a convenience for the render path; the render re-parses
    and re-verifies it every time, so a tampered cache file is inert too.
    """
    from practicegraph import __version__
    from practicegraph.analysis.update import (
        parse_update_manifest,
        update_public_key,
    )

    key = update_public_key()
    if key is None:
        return "skipped_unconfigured"
    url = config.update_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_UPDATE_PULL_DAY) == today:
        return "skipped_already_today"

    document = _get_json(url, timeout_s=10.0)
    if isinstance(document, str):
        return document
    # Verify BEFORE caching. An unverified manifest never touches the disk,
    # so there is no window in which a bad document is sitting where a later
    # read might trust it.
    if parse_update_manifest(document, key, __version__) is None:
        # Not necessarily an attack: the common case is "already current",
        # which is a correctly signed manifest that is simply not newer. Both
        # are non-events, and neither is cached.
        store.meta_set(_META_LAST_UPDATE_PULL_DAY, today)
        return "unchanged"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(catalog_dir / UPDATE_FILE_NAME, document)
    store.meta_set(_META_LAST_UPDATE_PULL_DAY, today)
    return "pulled"


def load_update_offer(data_dir: Path, current: str) -> UpdateOffer | None:
    """The cached manifest, re-verified at read time, or None.

    Re-verification is the point: the cache lives in a directory the review
    found to be world-readable, so the file is treated as untrusted input on
    every single read rather than trusted because we wrote it once."""
    import json as _json

    from practicegraph.analysis.update import (
        parse_update_manifest,
        update_public_key,
    )

    key = update_public_key()
    if key is None:
        return None
    path = data_dir / CATALOG_DIR_NAME / UPDATE_FILE_NAME
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parse_update_manifest(raw, key, current)


def pull_public_advisor(store: Store, config: Config, now: datetime) -> str:
    """Pull the curated Advisor artifact (market rows + verdict cards) once
    per UTC day in standalone mode — the models-pull discipline exactly."""
    if config.api_base_url:
        return "skipped_enterprise_configured"
    url = config.advisor_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.lower().startswith("https://"):
        return "invalid_artifact"
    today = now.astimezone(UTC).date().isoformat()
    if store.meta_get(_META_LAST_ADVISOR_PULL_DAY) == today:
        return "skipped_already_today"

    document = _get_catalog_json(url, "advisor")
    if isinstance(document, str):
        return document
    if parse_advisor_artifact(document) is None:
        return "invalid_artifact"

    catalog_dir = config.data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(catalog_dir / ADVISOR_FILE_NAME, document)
    store.meta_set(_META_LAST_ADVISOR_PULL_DAY, today)
    return "pulled"


def _accept_skills_artifact(
    store: Store,
    target: Path,
    artifact: dict[str, object],
    source: str,
) -> None:
    """Bind source provenance only to the artifact whose swap completed."""
    store.meta_set(_META_SKILLS_ACCEPTED_SOURCE, _SKILLS_SOURCE_INVALIDATED)
    _atomic_write(target, artifact)
    store.meta_set(_META_SKILLS_ACCEPTED_SOURCE, source)


def _atomic_write(target: Path, artifact: dict[str, object]) -> None:
    staging = target.with_suffix(".json.tmp")
    staging.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    staging.replace(target)  # atomic swap: readers never see a partial file


def _archive_artifact(
    data_dir: Path, channel: str, day: str, artifact: dict[str, object]
) -> None:
    """Keep a dated copy of a just-validated editorial artifact and prune the
    shelf to HISTORY_KEEP_DAYS. Only files named like a day are ever deleted,
    and only inside the channel's own history folder — an unknown file is
    left alone, never tidied."""
    if channel not in _HISTORY_CHANNELS or not _HISTORY_DAY_RE.match(day):
        return
    shelf = data_dir / CATALOG_DIR_NAME / HISTORY_DIR_NAME / channel
    shelf.mkdir(parents=True, exist_ok=True)
    # One edition is archived once, even if fetched on many subsequent days.
    for previous in shelf.glob("*.json"):
        try:
            if json.loads(previous.read_text(encoding="utf-8")) == artifact:
                return
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    _atomic_write(shelf / f"{day}.json", artifact)
    keep = sorted(
        (f for f in shelf.glob("*.json") if _HISTORY_DAY_RE.match(f.stem)),
        key=lambda f: f.stem,
        reverse=True,
    )
    for stale in keep[HISTORY_KEEP_DAYS:]:
        with contextlib.suppress(OSError):
            stale.unlink()


def list_artifact_days(
    data_dir: Path, channel: str, now: datetime | None = None
) -> tuple[str, ...]:
    """The PREVIOUS days the channel's shelf holds, newest first.

    Today's own key is excluded here rather than in the page: the shelf is
    keyed by the UTC day the pull happened on, so a page comparing it to
    the reader's LOCAL day left today's edition sitting on the shelf as a
    duplicate for part of every day outside UTC (2026-08-24 review). The
    engine owns the key, so the engine drops it.

    Fail-closed: an unreadable folder is an empty shelf, and a file whose
    name is not a day is not a day."""
    if channel not in _HISTORY_CHANNELS:
        return ()
    shelf = data_dir / CATALOG_DIR_NAME / HISTORY_DIR_NAME / channel
    try:
        names = [f.stem for f in shelf.glob("*.json") if _HISTORY_DAY_RE.match(f.stem)]
    except OSError:
        return ()
    today = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()
    # Older clients archived the same edition on every download day. Collapse
    # those copies in the reading without deleting anyone's existing cache.
    seen: set[str] = set()
    distinct: list[str] = []
    for name in sorted(names, reverse=True):
        try:
            value = json.loads((shelf / f"{name}.json").read_text(encoding="utf-8"))
            identity = json.dumps(value, sort_keys=True, separators=(",", ":"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if identity in seen:
            continue
        seen.add(identity)
        if name != today:
            distinct.append(name)
    return tuple(distinct)


EMPTY_BUILD_EDITION = BuildEdition(ideas=(), repos=())


def load_build_ideas(data_dir: Path) -> BuildEdition:
    """The active build-ideas edition (ideas + the GitHub shelf): the pulled
    artifact when valid, else an empty edition until a valid download arrives."""
    path = data_dir / CATALOG_DIR_NAME / BUILD_IDEAS_FILE_NAME
    try:
        parsed = parse_build_ideas_artifact(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else EMPTY_EDITION


def load_build_ideas_for_day(data_dir: Path, day: str) -> BuildEdition:
    """One archived day of build ideas, through the same strict parse."""
    if not _HISTORY_DAY_RE.match(day):
        return EMPTY_BUILD_EDITION
    path = (
        data_dir / CATALOG_DIR_NAME / HISTORY_DIR_NAME / "build-ideas"
        / f"{day}.json"
    )
    try:
        parsed = parse_build_ideas_artifact(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else EMPTY_BUILD_EDITION


def load_news_for_day(data_dir: Path, day: str) -> tuple[NewsItem, ...]:
    """One archived day of curated news, through the same strict parse as the
    live pull — the shelf is our own cache and still untrusted."""
    if not _HISTORY_DAY_RE.match(day):
        return ()
    path = data_dir / CATALOG_DIR_NAME / HISTORY_DIR_NAME / "news" / f"{day}.json"
    try:
        parsed = parse_news_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else ()


def load_practices(data_dir: Path) -> tuple[object, ...]:
    """The active practices: the pulled catalog artifact when valid, else the
    bundled defaults (FR-EMT-4 fallback discipline)."""
    from practicegraph.analysis.practices import BUNDLED_PRACTICES

    path = data_dir / CATALOG_DIR_NAME / PRACTICES_FILE_NAME
    try:
        parsed = parse_practices_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else BUNDLED_PRACTICES


def load_skills(data_dir: Path) -> tuple[object, ...]:
    """The active skill registry: the pulled catalog artifact when valid, else
    the bundled starter set (same FR-EMT-4 fallback discipline as practices)."""
    from practicegraph.analysis.skills import BUNDLED_SKILLS

    path = data_dir / CATALOG_DIR_NAME / SKILLS_FILE_NAME
    try:
        parsed = parse_skills_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else BUNDLED_SKILLS


def active_skills_source(data_dir: Path, store: Store) -> SkillSource:
    """Classify the source only when the local skill artifact is valid."""
    path = data_dir / CATALOG_DIR_NAME / SKILLS_FILE_NAME
    try:
        parsed = parse_skills_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    if parsed is None:
        return "bundled"
    accepted = store.meta_get(_META_SKILLS_ACCEPTED_SOURCE)
    if accepted == TEAMLANDI_SKILLS_URL:
        return "teamlandi_public"
    if accepted == SKILLS_SOURCE_ENTERPRISE:
        return "enterprise"
    return "custom_public"


def load_briefings(data_dir: Path) -> tuple[object, ...]:
    """The active news feed: the pulled catalog artifact when valid, else the
    bundled starter set (same FR-EMT-4 fallback discipline)."""
    from practicegraph.analysis.briefings import BUNDLED_BRIEFINGS

    path = data_dir / CATALOG_DIR_NAME / BRIEFINGS_FILE_NAME
    try:
        parsed = parse_briefings_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else BUNDLED_BRIEFINGS


def load_news(data_dir: Path) -> tuple[NewsItem, ...]:
    """Return cached curated news, or an empty tuple when absent or invalid."""
    path = data_dir / CATALOG_DIR_NAME / NEWS_FILE_NAME
    try:
        parsed = parse_news_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed if parsed is not None else ()


def load_models(data_dir: Path) -> ModelArtifact | None:
    """Return the last valid cached model artifact, or None."""
    path = data_dir / CATALOG_DIR_NAME / MODELS_FILE_NAME
    try:
        parsed = parse_model_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed


def _profile_cache_name(base: str, profile: str) -> str:
    """The served-variant naming scheme (PRODUCTIVITY_PROFILE D3, suffix
    files): docs.json / docs-productivity.json and so on. The coding lane
    keeps the unsuffixed name every existing parser already reads."""
    if profile == "coding":
        return base
    stem, dot, extension = base.rpartition(".")
    return f"{stem}-{profile}{dot}{extension}"


def load_docs(data_dir: Path, profile: str = "coding") -> DocsArtifact:
    """The profile's last valid shelf, or an explicit empty state."""
    name = _profile_cache_name(DOCS_FILE_NAME, profile)
    path = data_dir / CATALOG_DIR_NAME / name
    try:
        parsed = parse_docs_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    if parsed is not None:
        return parsed
    return EMPTY_DOCS


def load_model_catalog(data_dir: Path, profile: str = "coding") -> ModelCatalog:
    """The profile's last valid guidance, or an explicit empty state."""
    name = _profile_cache_name(CATALOG_FILE_NAME, profile)
    path = data_dir / CATALOG_DIR_NAME / name
    try:
        parsed = parse_model_catalog(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    if parsed is not None:
        return parsed
    return EMPTY_CATALOG


def pull_public_playbooks(store: Store, config: Config, now: datetime) -> str:
    url = config.playbooks_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.startswith("https://"):
        return "invalid_artifact"
    key = f"content_attempt_harness-playbooks_{content_digest(url)[:16]}"
    if not store.meta_claim_interval(key, now, NEWS_PULL_INTERVAL_S):
        return "skipped_recently"
    document = _get_catalog_json(url, "harness-playbooks")
    if isinstance(document, str):
        return document
    if parse_playbooks(document) is None:
        return "invalid_artifact"
    _accept_editorial(config.data_dir, "harness-playbooks", document, url, now)
    return "pulled"


def pull_public_token_prices(
    store: Store, config: Config, now: datetime, *, manual: bool = False,
) -> str:
    if config.api_base_url and config.token_prices_source_url_source == "default":
        return "skipped_enterprise_configured"
    url = config.token_prices_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.startswith("https://"):
        return "invalid_artifact"
    key = f"content_attempt_token_prices_{content_digest(url)[:16]}"
    # A deliberate retry gets its own short throttle, preserving scheduled polling.
    if manual:
        key += "_manual"
    if not store.meta_claim_interval(key, now, 30 if manual else NEWS_PULL_INTERVAL_S):
        return "skipped_recently"
    document = _get_catalog_json(url, "token-prices")
    if isinstance(document, str):
        return document
    if parse_token_prices(document) is None:
        return "invalid_artifact"
    previous = load_token_prices(config.data_dir)
    if previous is not None:
        try:
            check_transition(document, previous)
        except (ValueError, TypeError, KeyError):
            return "invalid_artifact"
    _accept_editorial(config.data_dir, "token-prices", document, url, now)
    return "pulled"


def load_token_prices(data_dir: Path) -> dict[str, Any] | None:
    path = data_dir / CATALOG_DIR_NAME / "token-prices.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PLAINTEXT_BYTES + 1)
        if len(raw) > MAX_PLAINTEXT_BYTES:
            return None
        return parse_token_prices(json.loads(raw))
    except (OSError, ValueError, RecursionError):
        return None



def pull_public_training(store: Store, config: Config, now: datetime) -> str:
    if config.api_base_url and config.training_source_url_source == "default":
        return "skipped_enterprise_configured"
    url = config.training_source_url.strip()
    if not url:
        return "skipped_unconfigured"
    if not url.startswith("https://"):
        return "invalid_artifact"
    key = f"content_attempt_training_{content_digest(url)[:16]}"
    if not store.meta_claim_interval(key, now, NEWS_PULL_INTERVAL_S):
        return "skipped_recently"
    document = _get_catalog_json(url, "training")
    if isinstance(document, str):
        return document
    if parse_training(document) is None:
        return "invalid_artifact"
    _accept_editorial(config.data_dir, "training", document, url, now)
    return "pulled"


def load_training(data_dir: Path) -> TrainingEdition | None:
    path = data_dir / CATALOG_DIR_NAME / "training.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PLAINTEXT_BYTES + 1)
        if len(raw) > MAX_PLAINTEXT_BYTES:
            return None
        return parse_training(json.loads(raw))
    except (OSError, ValueError, RecursionError):
        return None


def load_playbooks(data_dir: Path) -> PlaybookEdition | None:
    path = data_dir / CATALOG_DIR_NAME / "harness-playbooks.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(524289)
        if len(raw) > 524288:
            return None
        return parse_playbooks(json.loads(raw))
    except (OSError, ValueError, RecursionError):
        return None


def load_advisor(data_dir: Path) -> AdvisorArtifact | None:
    """Return the last valid cached Advisor artifact, or None (the fusion
    engine renders the honest market-unverified state on None)."""
    path = data_dir / CATALOG_DIR_NAME / ADVISOR_FILE_NAME
    try:
        parsed = parse_advisor_artifact(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        parsed = None
    return parsed


def catalog_status(data_dir: Path, store: Store) -> dict[str, str]:
    """Closed status labels for doctor."""
    path = data_dir / CATALOG_DIR_NAME / RATE_CARD_FILE_NAME
    return {
        "artifact": "present" if path.is_file() else "absent",
        "coordination_version": store.meta_get(_META_CATALOG_VERSION) or "none",
    }


def load_community(data_dir: Path) -> CommunityEdition:
    """Load only a validated edition; empty is honest when none has arrived."""
    path = data_dir / CATALOG_DIR_NAME / COMMUNITY_FILE_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return EMPTY_COMMUNITY_EDITION
    return parse_community_artifact(raw) or EMPTY_COMMUNITY_EDITION


def _accept_editorial(
    data_dir: Path, channel: str, document: dict[str, object], url: str, now: datetime,
) -> None:
    """Bind source information to the accepted bytes; never infer it from config."""
    edition_version(channel, document)
    catalog_dir = data_dir / CATALOG_DIR_NAME
    catalog_dir.mkdir(parents=True, exist_ok=True)
    target = catalog_dir / f"{channel}.json"
    digest = content_digest(document)
    try:
        unchanged = content_digest(json.loads(target.read_text(encoding="utf-8"))) == digest
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        _atomic_write(target, document)
    _atomic_write(catalog_dir / f"{channel}.receipt.json", {
        "sha256": digest, "source_url": public_source_url(url),
        "checked_at": now.astimezone(UTC).isoformat(),
    })


def feed_status(
    data_dir: Path, channel: str, *, profile: str = "coding", now: datetime | None = None,
) -> dict[str, str | None]:
    """Age is based on the edition date, never on a repeated download date.

    Receipts are local provenance, not cryptographic publisher authentication.
    A legacy cache without a matching receipt has an explicitly unknown source.
    """
    if channel in ("docs", "model-catalog") and profile == "productivity":
        channel += "-productivity"
    if channel not in CHANNELS:
        raise ValueError("unknown editorial channel")
    empty: dict[str, str | None] = {
        "version": None, "edition_date": None, "cached_at": None,
        "checked_at": None, "source_url": None, "source_label": None, "state": "missing",
    }
    path = data_dir / CATALOG_DIR_NAME / f"{channel}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = edition_version(channel, raw)
        dated = edition_date(channel, raw)
        today = (now or datetime.now(UTC)).astimezone(UTC).date()
        age = (today - datetime.strptime(dated, "%Y-%m-%d").date()).days if dated else -1
        state = "date_unknown" if age < 0 else (
            "stale" if age > CHANNELS[channel].review_after_days else "current"
        )
        status = {**empty, "version": version, "edition_date": dated, "state": state,
                  "source_label": "Cached catalog · source not recorded",
                  "cached_at": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()}
        receipt_path = path.with_suffix(".receipt.json")
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(receipt, dict) and receipt.get("sha256") == content_digest(raw):
                source = receipt.get("source_url")
                source = public_source_url(source) if isinstance(source, str) else None
                checked = receipt.get("checked_at")
                if isinstance(checked, str):
                    checked = datetime.fromisoformat(checked)
                    if checked.tzinfo is not None:
                        status["checked_at"] = checked.astimezone(UTC).isoformat()
                status["source_url"] = source
                if source:
                    official = source.startswith(DEFAULT_CONTENT_BASE_URL + "/")
                    status["source_label"] = (
                        "Curated by TeamLandi" if official else "Custom catalog"
                    )
        except (OSError, ValueError):
            pass
        return status
    except (OSError, ValueError):
        return empty
