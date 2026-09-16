"""Catalog pull (FR-EMT-4): closed failure codes, once-per-day cadence,
strict artifact validation, and rate-card activation that never blocks
local analysis."""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import practicegraph.catalog as catalog_module
from practicegraph.analysis.ratecard import (
    BUNDLED_RATE_CARD,
    activate_rate_card_from,
    active_rate_card,
    estimate_cost_micro_usd,
    parse_rate_card_artifact,
)
from practicegraph.analysis.skills import skills_artifact
from practicegraph.catalog import (
    PULL_CODES,
    TEAMLANDI_SKILLS_URL,
    active_skills_source,
    load_advisor,
    load_models,
    load_news,
    pull_catalog,
    pull_public_advisor,
    pull_public_models,
    pull_public_news,
    pull_public_ratecard,
    pull_public_skills,
)
from practicegraph.config import (
    DEFAULT_NEWS_SOURCE_URL,
    DEFAULT_RATECARD_SOURCE_URL,
    Config,
    resolve,
)
from practicegraph.events import TokenCounts
from practicegraph.store import Store
from practicegraph_server.app import make_server
from practicegraph_server.config import ServerConfig
from practicegraph_server.storage import MemoryStorage

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def catalog_server() -> Iterator[str]:
    config = ServerConfig(
        bind="127.0.0.1", port=0, org_id="acme-eng", org_token="tok",
        db_path=":memory:", k_threshold=2, access_log=False, serve_dashboard=False,
    )
    server = make_server(config, MemoryStorage())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _config(tmp_path: Path, api_base_url: str | None) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        data_dir_source="env",
        api_base_url=api_base_url,
        api_base_url_source="env" if api_base_url else "default",
        org_id="acme-eng",
        org_id_source="env",
        org_token_present=False,
        org_token_source="default",
        config_file_state="absent",
    )


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "data" / "state.db")
    store.migrate()
    return store


def _news_artifact() -> dict[str, object]:
    return {
        "news_version": "reader-test-v1",
        "items": [
            {
                "id": "reader-test",
                "kind": "release",
                "title": "A smaller control deck",
                "hook": "A compact control surface keeps common agent actions close.",
                "summary": "The project maps a small keyboard to frequent agent controls.",
                "why": "It is a concrete example of shaping tools around repeated work.",
                "url": "https://example.org/control-deck",
                "source": "Example source",
            }
        ],
    }


def _models_artifact() -> dict[str, object]:
    return {
        "schema": "practicegraph.model-intelligence/1",
        "artifact_version": "models-aa-coding-v1.1-2026-07-03",
        "published_on": "2026-07-03",
        "source": {
            "name": "Artificial Analysis",
            "results_url": "https://artificialanalysis.ai/agents/coding-agents",
            "methodology_url": (
                "https://artificialanalysis.ai/methodology/coding-agents-benchmarking"
            ),
            "attribution": "Coding-agent benchmark data: Artificial Analysis",
        },
        "benchmark": {
            "name": "Artificial Analysis Coding Agent Index",
            "version": "1.1",
            "token_unit": "average_total_tokens_per_task",
        },
        "variants": [
            {
                "id": "codex-test",
                "tool": "codex",
                "model": "GPT Test",
                "effort": "medium",
                "index_tenths": 800,
                "total_tokens_per_task": 2_000_000,
            },
            {
                "id": "claude-test",
                "tool": "claude_code",
                "model": "Claude Test",
                "effort": "adaptive",
                "index_tenths": 810,
                "total_tokens_per_task": 3_000_000,
            },
        ],
    }


def test_pull_codes_are_pinned() -> None:
    assert PULL_CODES == (
        "pulled", "unchanged", "skipped_unconfigured", "skipped_already_today",
        "skipped_recently", "skipped_enterprise_configured",
        "server_unavailable", "timeout", "http_error", "invalid_artifact",
        "transport_error",
    )


def test_pull_lifecycle_against_real_server(
    tmp_path: Path, catalog_server: str
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, catalog_server)

    assert pull_catalog(store, config, NOW) == "pulled"
    artifact_path = tmp_path / "data" / "catalog" / "rate-card.json"
    assert artifact_path.is_file()
    assert parse_rate_card_artifact(
        json.loads(artifact_path.read_text(encoding="utf-8"))
    ) is not None
    # Practices ride the same pull (optional artifact, same coordination).
    assert (tmp_path / "data" / "catalog" / "practices.json").is_file()

    # Once per day (frequency cap).
    assert pull_catalog(store, config, NOW) == "skipped_already_today"
    # Next day, same coordination version: nothing to fetch.
    assert pull_catalog(store, config, NOW + timedelta(days=1)) == "unchanged"


def test_pull_never_blocks_when_unconfigured_or_unreachable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert pull_catalog(store, _config(tmp_path, None), NOW) == "skipped_unconfigured"
    outcome = pull_catalog(store, _config(tmp_path, "http://127.0.0.1:9"), NOW)
    assert outcome in ("server_unavailable", "timeout", "transport_error")
    assert not (tmp_path / "data" / "catalog").exists()


def test_invalid_artifact_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    responses = iter(
        [
            {"catalog_version": "evil-2026", "rate_card_version": "x",
             "supported_schema_versions": [1]},
            {"rate_card_version": "x", "unit": "elephants", "rates": {}},
        ]
    )
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda url, timeout_s=10.0, **_kwargs: next(responses)
    )
    assert pull_catalog(store, _config(tmp_path, "http://ignored"), NOW) == "invalid_artifact"
    assert not (tmp_path / "data" / "catalog").exists()  # nothing touched disk


def test_artifact_validation_is_strict() -> None:
    good_rate = {"input": 1, "output": 2, "cache_read": 3, "cache_creation": 4,
                 "cache_creation_1h": 8}
    good = {
        "rate_card_version": "cat-2026-08-01",
        "unit": "micro_usd_per_1m_tokens",
        "rates": {"claude-sonnet-4": good_rate},
    }
    assert parse_rate_card_artifact(good) is not None
    for mutation in (
        {**good, "surprise": 1},
        {**good, "unit": "usd"},
        {**good, "rates": {}},
        {**good, "rates": {"BAD PREFIX!": good_rate}},
        {**good, "rates": {"claude-sonnet-4": {**good_rate, "input": -1}}},
        {**good, "rates": {"claude-sonnet-4": {"input": 1}}},
    ):
        assert parse_rate_card_artifact(mutation) is None


def test_activation_changes_estimates_and_falls_back(tmp_path: Path) -> None:
    """FR-EMT-4: a valid pulled card takes effect; a broken one falls back to
    the bundled default and analysis never stops."""
    data_dir = tmp_path / "data"
    catalog_dir = data_dir / "catalog"
    catalog_dir.mkdir(parents=True)
    card = {
        "rate_card_version": "cat-2026-08-01",
        "unit": "micro_usd_per_1m_tokens",
        "rates": {"claude-sonnet-4": {"input": 6_000_000, "output": 30_000_000,
                                      "cache_read": 600_000, "cache_creation": 0,
                                      "cache_creation_1h": 0}},
    }
    (catalog_dir / "rate-card.json").write_text(json.dumps(card), encoding="utf-8")

    assert activate_rate_card_from(data_dir) == "catalog"
    assert active_rate_card().version == "cat-2026-08-01"
    tokens = TokenCounts(input=1000)
    assert estimate_cost_micro_usd(tokens, "claude-sonnet-4-20250514") == 6_000
    # Models absent from the pulled card become unpriced — surfaced, not faked.
    assert estimate_cost_micro_usd(tokens, "gpt-5-codex") is None

    (catalog_dir / "rate-card.json").write_text("garbage{", encoding="utf-8")
    assert activate_rate_card_from(data_dir) == "bundled"
    assert active_rate_card() is BUNDLED_RATE_CARD
    assert estimate_cost_micro_usd(tokens, "claude-sonnet-4-20250514") == 3_000


def test_public_news_pull_is_validated_and_cached_for_fifteen_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    calls: list[str] = []

    def response(url: str, timeout_s: float = 10.0, **_kwargs) -> dict[str, object]:
        calls.append(url)
        return _news_artifact()

    monkeypatch.setattr(catalog_module, "_get_json", response)

    assert pull_public_news(store, config, NOW) == "pulled"
    assert load_news(config.data_dir)[0].news_id == "reader-test"
    assert pull_public_news(
        store, config, NOW + timedelta(minutes=14, seconds=59)
    ) == "skipped_recently"
    assert pull_public_news(
        store, config, NOW + timedelta(minutes=15)
    ) == "pulled"
    assert calls == [config.news_source_url, config.news_source_url]


def test_public_news_with_enterprise_needs_an_explicit_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The news-curation decision and NFR-SEC-5, both honored. An org that
    explicitly configured a news URL keeps the direct pull (freshness was the
    point of the public-news channel). But the URL defaults to the public
    host, so an UNTOUCHED default on an enterprise endpoint must not phone
    that host every 15 minutes — that was nobody's choice."""
    store = _store(tmp_path)
    config = _config(tmp_path, "https://enterprise.example")
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda *_args, **_kwargs: _news_artifact()
    )

    # An EXPLICITLY configured news URL (config file / env): the pull stands.
    chosen = dataclasses.replace(config, news_source_url_source="file")
    assert pull_public_news(store, chosen, NOW) == "pulled"
    assert load_news(config.data_dir)[0].news_id == "reader-test"

    # The same enterprise endpoint with the UNTOUCHED default url: skipped —
    # silently calling the public host is not a configuration anyone made.
    assert config.news_source_url_source == "default"
    fresh = _store(tmp_path / "fresh")
    assert (
        pull_public_news(fresh, config, NOW) == "skipped_enterprise_configured"
    )


def test_public_news_rejects_plaintext(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plaintext = _config(tmp_path, None)
    plaintext = Config(
        **{
            field: getattr(plaintext, field)
            for field in plaintext.__dataclass_fields__
            if field not in {"news_source_url", "news_source_url_source"}
        },
        news_source_url="http://example.org/news.json",
        news_source_url_source="env",
    )
    assert pull_public_news(store, plaintext, NOW) == "invalid_artifact"


def test_invalid_public_news_preserves_last_valid_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    catalog_dir = config.data_dir / "catalog"
    catalog_dir.mkdir(parents=True)
    path = catalog_dir / "news.json"
    path.write_text(json.dumps(_news_artifact()), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: {"bad": True})

    assert pull_public_news(store, config, NOW) == "invalid_artifact"
    assert pull_public_news(
        store, config, NOW + timedelta(minutes=1)
    ) == "skipped_recently"
    assert path.read_bytes() == before


def test_public_models_pull_is_validated_cached_and_daily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    calls: list[str] = []

    def response(url: str, timeout_s: float = 10.0, **_kwargs) -> dict[str, object]:
        calls.append(url)
        return _models_artifact()

    monkeypatch.setattr(catalog_module, "_get_json", response)

    assert pull_public_models(store, config, NOW) == "pulled"
    assert load_models(config.data_dir) is not None
    assert pull_public_models(store, config, NOW) == "skipped_already_today"
    assert calls == ["https://practicegraph-dev.vercel.app/models.json"]


def test_public_models_skip_enterprise_and_reject_plaintext(tmp_path: Path) -> None:
    store = _store(tmp_path)
    enterprise = _config(tmp_path, "https://enterprise.example")
    assert pull_public_models(store, enterprise, NOW) == "skipped_enterprise_configured"

    plaintext = dataclasses.replace(
        _config(tmp_path, None),
        models_source_url="http://example.org/models.json",
        models_source_url_source="env",
    )
    assert pull_public_models(store, plaintext, NOW) == "invalid_artifact"


def test_invalid_public_models_preserve_last_valid_cache_and_pull_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    catalog_dir = config.data_dir / "catalog"
    catalog_dir.mkdir(parents=True)
    path = catalog_dir / "models.json"
    path.write_text(json.dumps(_models_artifact()), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: {"bad": True})

    assert pull_public_models(store, config, NOW) == "invalid_artifact"
    assert path.read_bytes() == before
    assert store.meta_get("models_public_last_pull_day") is None
    assert load_models(config.data_dir) is not None


def test_independent_config_defaults_to_teamlandi_registry(tmp_path: Path) -> None:
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path / "data")})
    assert config.skills_source_url == TEAMLANDI_SKILLS_URL
    assert config.skills_source_url_source == "default"


def test_public_pull_records_exact_accepted_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = dataclasses.replace(
        _config(tmp_path, None),
        skills_source_url=TEAMLANDI_SKILLS_URL,
        skills_source_url_source="default",
    )
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: skills_artifact())
    assert pull_public_skills(store, config, NOW) == "pulled"
    assert active_skills_source(config.data_dir, store) == "teamlandi_public"
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda *_args, **_kwargs: "server_unavailable"
    )
    assert pull_public_skills(store, config, NOW + timedelta(days=1)) == "server_unavailable"
    assert active_skills_source(config.data_dir, store) == "teamlandi_public"


def test_enterprise_config_fails_closed_after_refresh_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = dataclasses.replace(
        _config(tmp_path, "http://catalog.test"),
        skills_source_url=TEAMLANDI_SKILLS_URL,
        skills_source_url_source="default",
    )
    enterprise_skills = skills_artifact()
    enterprise_skills["skills_version"] = "enterprise-v1"
    responses = iter([
        {
            "catalog_version": "v1",
            "rate_card_version": "rates-v1",
            "supported_schema_versions": [1],
        },
        {
            "rate_card_version": "rates-v1",
            "unit": "micro_usd_per_1m_tokens",
            "rates": {
                "gpt-5": {
                    "input": 1,
                    "output": 2,
                    "cache_read": 1,
                    "cache_creation": 1,
                    "cache_creation_1h": 1,
                }
            },
        },
        None,
        enterprise_skills,
        None,
    ])
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda *_args, **_kwargs: next(responses)
    )
    assert pull_catalog(store, config, NOW) == "pulled"
    skills_path = config.data_dir / "catalog" / "skills.json"
    accepted_bytes = skills_path.read_bytes()
    assert active_skills_source(config.data_dir, store) == "enterprise"

    next_day = NOW + timedelta(days=1)
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda *_args, **_kwargs: "server_unavailable"
    )
    assert pull_catalog(store, config, next_day) == "server_unavailable"

    public_fetches: list[str] = []

    def public_response(url: str, timeout_s: float = 10.0, **_kwargs) -> dict[str, object]:
        public_fetches.append(url)
        public_skills = skills_artifact()
        public_skills["skills_version"] = "public-v1"
        return public_skills

    monkeypatch.setattr(catalog_module, "_get_json", public_response)
    assert pull_public_skills(store, config, next_day) == (
        "skipped_enterprise_configured"
    )
    assert public_fetches == []
    assert skills_path.read_bytes() == accepted_bytes
    assert active_skills_source(config.data_dir, store) == "enterprise"


def test_public_replacement_interruption_invalidates_previous_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = dataclasses.replace(
        _config(tmp_path, None),
        skills_source_url=TEAMLANDI_SKILLS_URL,
        skills_source_url_source="default",
    )
    first = skills_artifact()
    first["skills_version"] = "public-v1"
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: first)
    assert pull_public_skills(store, config, NOW) == "pulled"
    skills_path = config.data_dir / "catalog" / "skills.json"
    accepted_bytes = skills_path.read_bytes()
    assert active_skills_source(config.data_dir, store) == "teamlandi_public"

    second = skills_artifact()
    second["skills_version"] = "public-v2"
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: second)
    source_during_write: list[str] = []

    def interrupt_write(target: Path, artifact: dict[str, object]) -> None:
        source_during_write.append(active_skills_source(config.data_dir, store))
        raise OSError("interrupted before replacement")

    monkeypatch.setattr(catalog_module, "_atomic_write", interrupt_write)
    with pytest.raises(OSError, match="interrupted before replacement"):
        pull_public_skills(store, config, NOW + timedelta(days=1))

    assert source_during_write == ["custom_public"]
    assert skills_path.read_bytes() == accepted_bytes
    assert active_skills_source(config.data_dir, store) == "custom_public"


def test_enterprise_marker_failure_after_replacement_is_prompt_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, "http://catalog.test")
    first = skills_artifact()
    first["skills_version"] = "enterprise-v1"
    first_responses = iter([
        {
            "catalog_version": "v1",
            "rate_card_version": "rates-v1",
            "supported_schema_versions": [1],
        },
        {
            "rate_card_version": "rates-v1",
            "unit": "micro_usd_per_1m_tokens",
            "rates": {
                "gpt-5": {
                    "input": 1,
                    "output": 2,
                    "cache_read": 1,
                    "cache_creation": 1,
                    "cache_creation_1h": 1,
                }
            },
        },
        None,
        first,
        None,
    ])
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda *_args, **_kwargs: next(first_responses)
    )
    assert pull_catalog(store, config, NOW) == "pulled"
    assert active_skills_source(config.data_dir, store) == "enterprise"

    second = skills_artifact()
    second["skills_version"] = "enterprise-v2"
    second_responses = iter([
        {
            "catalog_version": "v2",
            "rate_card_version": "rates-v2",
            "supported_schema_versions": [1],
        },
        {
            "rate_card_version": "rates-v2",
            "unit": "micro_usd_per_1m_tokens",
            "rates": {
                "gpt-5": {
                    "input": 2,
                    "output": 3,
                    "cache_read": 1,
                    "cache_creation": 1,
                    "cache_creation_1h": 1,
                }
            },
        },
        None,
        second,
    ])
    monkeypatch.setattr(
        catalog_module, "_get_json", lambda *_args, **_kwargs: next(second_responses)
    )
    original_meta_set = store.meta_set
    source_marker_writes: list[str] = []

    def fail_source_finalization(key: str, value: str) -> None:
        if key == "skills_catalog_accepted_source":
            source_marker_writes.append(value)
            if value == "enterprise":
                raise RuntimeError("source marker unavailable")
        original_meta_set(key, value)

    monkeypatch.setattr(store, "meta_set", fail_source_finalization)
    with pytest.raises(RuntimeError, match="source marker unavailable"):
        pull_catalog(store, config, NOW + timedelta(days=1))

    skills_path = config.data_dir / "catalog" / "skills.json"
    assert json.loads(skills_path.read_text(encoding="utf-8"))["skills_version"] == (
        "enterprise-v2"
    )
    assert source_marker_writes == ["", "enterprise"]
    assert active_skills_source(config.data_dir, store) == "custom_public"


def test_custom_and_enterprise_sources_are_not_trusted_for_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    custom = dataclasses.replace(
        _config(tmp_path, None),
        skills_source_url="https://example.test/skills.json",
        skills_source_url_source="env",
    )
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: skills_artifact())
    assert pull_public_skills(store, custom, NOW) == "pulled"
    assert active_skills_source(custom.data_dir, store) == "custom_public"

    next_day = NOW + timedelta(days=1)
    server_config = _config(tmp_path, "http://catalog.test")
    responses = iter([
        {
            "catalog_version": "v1",
            "rate_card_version": "rates-v1",
            "supported_schema_versions": [1],
        },
        {
            "rate_card_version": "rates-v1",
            "unit": "micro_usd_per_1m_tokens",
            "rates": {
                "gpt-5": {
                    "input": 1,
                    "output": 2,
                    "cache_read": 1,
                    "cache_creation": 1,
                    "cache_creation_1h": 1,
                }
            },
        },
        None,
        skills_artifact(),
        None,
    ])
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: next(responses))
    assert pull_catalog(store, server_config, next_day) == "pulled"
    assert active_skills_source(server_config.data_dir, store) == "enterprise"


def test_invalid_local_artifact_reports_bundled_even_with_stale_trusted_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = dataclasses.replace(
        _config(tmp_path, None), skills_source_url=TEAMLANDI_SKILLS_URL
    )
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: skills_artifact())
    assert pull_public_skills(store, config, NOW) == "pulled"
    (config.data_dir / "catalog" / "skills.json").write_text(
        "broken", encoding="utf-8"
    )
    assert active_skills_source(config.data_dir, store) == "bundled"


def _advisor_artifact() -> dict[str, object]:
    return {
        "schema": "practicegraph.advisor/1",
        "artifact_version": "advisor-2026-07-03-abc123def0",
        "as_of": "2026-07-03",
        "expires": "2026-07-17",
        "source": {
            "name": "Artificial Analysis",
            "url": "https://artificialanalysis.ai/models",
            "attribution": "Model benchmark and price data: Artificial Analysis",
        },
        "models": [
            {
                "family": "claude_haiku",
                "aa_slug": "claude-haiku-4.5",
                "released_on": "2025-10-01",
                "coding_index_tenths": 620,
                "agentic_index_tenths": 560,
                "price_in_micro": 1_000_000,
                "price_out_micro": 5_000_000,
                "price_cache_read_micro": 100_000,
                "median_tps_tenths": 1400,
                "ttft_ms": 700,
                "context_window": 200_000,
            }
        ],
        "verdicts": [],
    }


def test_public_advisor_pull_is_validated_cached_and_daily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    calls: list[str] = []

    def response(url: str, timeout_s: float = 10.0, **_kwargs) -> dict[str, object]:
        calls.append(url)
        return _advisor_artifact()

    monkeypatch.setattr(catalog_module, "_get_json", response)

    assert pull_public_advisor(store, config, NOW) == "pulled"
    cached = load_advisor(config.data_dir)
    assert cached is not None and cached.models[0].family == "claude_haiku"
    assert pull_public_advisor(store, config, NOW) == "skipped_already_today"
    assert calls == ["https://practicegraph-dev.vercel.app/advisor.json"]


def test_public_advisor_skips_enterprise_and_rejects_plaintext(tmp_path: Path) -> None:
    store = _store(tmp_path)
    enterprise = _config(tmp_path, "https://enterprise.example")
    assert pull_public_advisor(store, enterprise, NOW) == "skipped_enterprise_configured"

    plaintext = dataclasses.replace(
        _config(tmp_path, None),
        advisor_source_url="http://example.org/advisor.json",
        advisor_source_url_source="env",
    )
    assert pull_public_advisor(store, plaintext, NOW) == "invalid_artifact"


def test_invalid_public_advisor_preserves_last_valid_cache_and_pull_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    catalog_dir = config.data_dir / "catalog"
    catalog_dir.mkdir(parents=True)
    path = catalog_dir / "advisor.json"
    path.write_text(json.dumps(_advisor_artifact()), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(catalog_module, "_get_json", lambda *_args, **_kwargs: {"bad": True})

    assert pull_public_advisor(store, config, NOW) == "invalid_artifact"
    assert path.read_bytes() == before
    assert store.meta_get("advisor_public_last_pull_day") is None
    assert load_advisor(config.data_dir) is not None


def _ratecard_artifact() -> dict[str, object]:
    from practicegraph.analysis.ratecard import rate_card_artifact

    return rate_card_artifact()


def test_public_ratecard_pull_is_validated_cached_and_daily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The served rate card follows the models-pull discipline exactly, so a
    price correction reaches every install the day after it is published
    instead of waiting for a client release."""
    from practicegraph.analysis.ratecard import (
        activate_rate_card_from,
        reset_active_rate_card,
    )

    store = _store(tmp_path)
    config = _config(tmp_path, None)
    calls: list[str] = []

    def response(url: str, timeout_s: float = 10.0, **_kwargs) -> dict[str, object]:
        calls.append(url)
        return _ratecard_artifact()

    monkeypatch.setattr(catalog_module, "_get_json", response)

    assert pull_public_ratecard(store, config, NOW) == "pulled"
    assert (config.data_dir / "catalog" / "rate-card.json").is_file()
    assert pull_public_ratecard(store, config, NOW) == "skipped_already_today"
    # Pinned to the configured default rather than a literal, and separately
    # asserted to be a host that actually serves: a default pointing at an
    # unpointed apex means the card silently never refreshes, which is the one
    # failure this whole feature exists to prevent.
    assert calls == [DEFAULT_RATECARD_SOURCE_URL]
    assert DEFAULT_RATECARD_SOURCE_URL.startswith("https://")
    assert DEFAULT_RATECARD_SOURCE_URL.endswith("/rate-card.json")
    # Same host as the news artifact, which is the one demonstrably serving.
    assert DEFAULT_RATECARD_SOURCE_URL.rsplit("/", 1)[0] == (
        DEFAULT_NEWS_SOURCE_URL.rsplit("/", 1)[0]
    )

    # The pulled card actually activates — this is the whole point.
    try:
        assert activate_rate_card_from(config.data_dir) == "catalog"
    finally:
        reset_active_rate_card()


def test_public_ratecard_skips_enterprise_and_rejects_plaintext(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    enterprise = _config(tmp_path, "https://enterprise.example")
    assert (
        pull_public_ratecard(store, enterprise, NOW)
        == "skipped_enterprise_configured"
    )
    plaintext = dataclasses.replace(
        _config(tmp_path, None),
        ratecard_source_url="http://example.org/rate-card.json",
        ratecard_source_url_source="env",
    )
    assert pull_public_ratecard(store, plaintext, NOW) == "invalid_artifact"


def test_invalid_public_ratecard_never_replaces_a_good_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed served card must not become the pricing basis. Money is the
    one surface where fail-open is unacceptable: a garbage card would silently
    reprice all history."""
    store = _store(tmp_path)
    config = _config(tmp_path, None)
    catalog_dir = config.data_dir / "catalog"
    catalog_dir.mkdir(parents=True)
    path = catalog_dir / "rate-card.json"
    path.write_text(json.dumps(_ratecard_artifact()), encoding="utf-8")
    before = path.read_bytes()

    for bad in ({"bad": True}, {"rate_card_version": "v", "unit": "wrong", "rates": {}}):
        monkeypatch.setattr(
            catalog_module, "_get_json", lambda *_a, **_k: bad  # noqa: B023
        )
        assert pull_public_ratecard(store, config, NOW) == "invalid_artifact"
        assert path.read_bytes() == before
        assert store.meta_get("ratecard_public_last_pull_day") is None
