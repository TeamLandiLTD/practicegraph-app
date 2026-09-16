"""Server configuration — environment only (FR-API-6)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

ENV_BIND = "PG_SERVER_BIND"
ENV_PORT = "PG_SERVER_PORT"
ENV_ORG_ID = "PG_SERVER_ORG_ID"
ENV_ORG_TOKEN = "PG_SERVER_ORG_TOKEN"
ENV_INGEST_TOKENS = "PG_SERVER_INGEST_TOKENS"
ENV_DB = "PG_SERVER_DB"
ENV_K_THRESHOLD = "PG_SERVER_K_THRESHOLD"
ENV_ACCESS_LOG = "PG_SERVER_ACCESS_LOG"
ENV_SERVE_DASHBOARD = "PG_SERVER_SERVE_DASHBOARD"
ENV_DASHBOARD_PASSWORD = "PG_SERVER_DASHBOARD_PASSWORD"
ENV_RETENTION_DAYS = "PG_SERVER_RETENTION_DAYS"
ENV_SKILLS_URL = "PG_SERVER_SKILLS_URL"
ENV_SKILLS_TTL = "PG_SERVER_SKILLS_TTL"
ENV_NEWS_FEEDS = "PG_SERVER_NEWS_FEEDS"
ENV_NEWS_TTL = "PG_SERVER_NEWS_TTL"

DEFAULT_K_THRESHOLD = 5  # NFR-PRV-3 (configurable, default >= 5)
MAX_INGEST_BYTES = 65536  # NFR-SEC-3 strict payload size limit


@dataclass(frozen=True, slots=True)
class ServerConfig:
    bind: str
    port: int
    org_id: str
    org_token: str
    db_path: str
    k_threshold: int
    access_log: bool
    serve_dashboard: bool
    # Empty disables all aggregate reads. Ingest credentials never grant reads.
    dashboard_password: str = ""
    # Admin-issued opaque source IDs and independent credentials. Source IDs
    # stay server-side; rotating a credential preserves its contributor identity.
    ingest_tokens: tuple[tuple[str, str], ...] = ()
    # Retention: prune stored emits whose day is older than this many days.
    # 0 = keep forever (pilot default).
    retention_days: int = 0
    # Skill catalog source: a GitHub Pages (or any https) URL serving a skills
    # artifact JSON. When set, the server fetches + validates it and re-serves
    # at /v1/catalog/skills so the endpoint still only talks to this server
    # (NFR-SEC-5). Empty = serve the bundled skills. Admins curate skills as a
    # GitHub repo published to Pages.
    skills_url: str = ""
    # How long a fetched catalog is trusted before a re-fetch (seconds).
    skills_ttl_s: int = 900
    # News feeds: RSS/Atom feed specs the server polls, parses, sanitizes, and
    # serves as the news artifact at /v1/catalog/briefings (the endpoint never
    # fetches a feed or parses XML). Format: "url|kind|source" entries separated
    # by newlines or ";". kind is one of release|update|paper|post. Empty =
    # serve the bundled starter news.
    news_feeds: str = ""
    # How long a fetched+parsed feed set is cached before a re-fetch (seconds).
    news_ttl_s: int = 3600

    def __post_init__(self) -> None:
        credentials = [secret for _, secret in self.ingest_tokens]
        sources = [source for source, _ in self.ingest_tokens]
        if (
            len(set(credentials)) != len(credentials)
            or len(set(sources)) != len(sources)
            or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source) for source in sources)
            or any(len(secret) < 32 or len(secret) > 4096 for secret in credentials)
            or (
                self.dashboard_password
                and self.dashboard_password in [self.org_token, *credentials]
            )
        ):
            raise ValueError("invalid or overlapping server credentials")


def _ingest_tokens(raw: str) -> tuple[tuple[str, str], ...]:
    if not raw:
        return ()
    try:

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError
            return result

        values = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(values, dict) or not values or len(values) > 10000:
            raise ValueError
        if not all(isinstance(value, str) for value in values.values()):
            raise ValueError
        return tuple((key, value) for key, value in values.items())
    except (ValueError, TypeError):
        raise ValueError("invalid PG_SERVER_INGEST_TOKENS") from None


def load_config(env: dict[str, str]) -> ServerConfig:
    def _int(key: str, default: int, minimum: int = 1) -> int:
        raw = env.get(key, "")
        try:
            value = int(raw) if raw else default
        except ValueError:
            value = default
        return max(minimum, value)

    return ServerConfig(
        bind=env.get(ENV_BIND, "127.0.0.1"),  # loopback-bindable for pilots
        port=_int(ENV_PORT, 8321),
        org_id=env.get(ENV_ORG_ID, ""),
        org_token=env.get(ENV_ORG_TOKEN, ""),
        db_path=env.get(ENV_DB, "practicegraph-server.db"),
        k_threshold=_int(ENV_K_THRESHOLD, DEFAULT_K_THRESHOLD, minimum=5),
        access_log=env.get(ENV_ACCESS_LOG, "") == "1",
        serve_dashboard=env.get(ENV_SERVE_DASHBOARD, "1") != "0",
        dashboard_password=env.get(ENV_DASHBOARD_PASSWORD, ""),
        ingest_tokens=_ingest_tokens(env.get(ENV_INGEST_TOKENS, "")),
        retention_days=_int(ENV_RETENTION_DAYS, 0, minimum=0),
        skills_url=env.get(ENV_SKILLS_URL, ""),
        skills_ttl_s=_int(ENV_SKILLS_TTL, 900, minimum=30),
        news_feeds=env.get(ENV_NEWS_FEEDS, ""),
        news_ttl_s=_int(ENV_NEWS_TTL, 3600, minimum=300),
    )
