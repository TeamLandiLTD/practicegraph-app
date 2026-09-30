"""Open catalog contracts; editorial editions live outside the application.

Shared by the downloader and release validator. This module contains no picks,
rankings, model recommendations, or private editorial inputs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from practicegraph.analysis.advisor_market import parse_advisor_artifact
from practicegraph.analysis.build_ideas import parse_build_ideas_artifact
from practicegraph.analysis.community import parse_community_artifact
from practicegraph.analysis.docs import parse_docs_artifact
from practicegraph.analysis.harness_playbooks import parse_playbooks
from practicegraph.analysis.model_catalog import parse_model_catalog
from practicegraph.analysis.model_intelligence import parse_model_artifact
from practicegraph.analysis.news import parse_news_artifact
from practicegraph.analysis.ratecard import parse_rate_card_artifact
from practicegraph.analysis.skills import parse_skills_artifact
from practicegraph.analysis.token_prices import parse_token_prices
from practicegraph.analysis.training_catalog import parse_training
from practicegraph.config import DEFAULT_CONTENT_BASE_URL as DEFAULT_CONTENT_BASE_URL

MAX_CONTENT_BYTES = 524_288
VERSION_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")


@dataclass(frozen=True)
class ContentChannel:
    version_key: str
    parse: Callable[[object], object | None]
    review_after_days: int


# Review windows indicate editorial age, not a claim that an old fact is false.
CHANNELS: dict[str, ContentChannel] = {
    "token-prices": ContentChannel("edition_version", parse_token_prices, 2),
    "training": ContentChannel("training_version", parse_training, 30),
    "harness-playbooks": ContentChannel("playbooks_version", parse_playbooks, 30),
    "news": ContentChannel("news_version", parse_news_artifact, 3),
    "build-ideas": ContentChannel("ideas_version", parse_build_ideas_artifact, 7),
    "community": ContentChannel("community_version", parse_community_artifact, 7),
    "models": ContentChannel("artifact_version", parse_model_artifact, 14),
    "model-catalog": ContentChannel("catalog_version", parse_model_catalog, 14),
    "model-catalog-productivity": ContentChannel("catalog_version", parse_model_catalog, 14),
    "docs": ContentChannel("docs_version", parse_docs_artifact, 30),
    "docs-productivity": ContentChannel("docs_version", parse_docs_artifact, 30),
    "advisor": ContentChannel("artifact_version", parse_advisor_artifact, 14),
    "rate-card": ContentChannel("rate_card_version", parse_rate_card_artifact, 14),
    "skills": ContentChannel("skills_version", parse_skills_artifact, 30),
}


def canonical_bytes(document: object) -> bytes:
    """Stable bytes for comparison; a digest is integrity metadata, not a signature."""
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def content_digest(document: object) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def edition_version(channel: str, document: object) -> str:
    contract = CHANNELS[channel]
    if not isinstance(document, dict) or contract.parse(document) is None:
        raise ValueError(f"{channel}: invalid artifact")
    version = document.get(contract.version_key)
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise ValueError(f"{channel}: invalid edition version")
    return version


def edition_date(channel: str, document: object) -> str | None:
    version = edition_version(channel, document)
    assert isinstance(document, dict)
    explicit = document.get("published_on", document.get("as_of"))
    if channel == "token-prices":
        explicit = str(document["observed_at"])[:10]
    match = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", version)
    candidate = explicit if isinstance(explicit, str) else match.group(1) if match else None
    if candidate:
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            pass
    return None


def public_source_url(url: str) -> str | None:
    """UI provenance never displays URL credentials, queries, or fragments."""
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            return None
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        authority = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit(("https", authority, parts.path, "", ""))
    except ValueError:
        return None


def profile_url(url: str, profile: str) -> str:
    """Variant filenames preserve a custom origin, path prefix, and query."""
    if profile != "productivity":
        return url
    parts = urlsplit(url)
    if not parts.path.endswith(".json"):
        raise ValueError("profile catalogs require a .json path")
    path = parts.path[:-5] + "-productivity.json"
    return urlunsplit(parts._replace(path=path))
