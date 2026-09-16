from __future__ import annotations

import json
from pathlib import Path

from practicegraph.config import (
    DEFAULT_MODELS_SOURCE_URL,
    DEFAULT_NEWS_SOURCE_URL,
    resolve,
    resolve_models_url,
)


def test_invalid_billing_overrides_keep_valid_choices(tmp_path: Path) -> None:
    from practicegraph.config import read_prefs

    (tmp_path / "config.json").write_text(json.dumps({
        "billing_mode": "subscription",
        "billing_by_tool": {"codex": "mixed", "claude_code": [], "untrusted": "api"},
    }), encoding="utf-8")
    prefs = read_prefs(tmp_path)
    assert prefs.billing_mode == "subscription"
    assert prefs.billing_by_tool == {"codex": "mixed"}


def test_news_source_defaults_to_public_site(tmp_path: Path) -> None:
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})

    assert DEFAULT_NEWS_SOURCE_URL == (
        "https://practicegraph.dev/news.json"
    )
    assert config.news_source_url == DEFAULT_NEWS_SOURCE_URL
    assert config.news_source_url_source == "default"


def test_news_source_resolves_file_then_environment(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"news_source_url": "https://file.example/news.json"}),
        encoding="utf-8",
    )

    from_file = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    assert from_file.news_source_url == "https://file.example/news.json"
    assert from_file.news_source_url_source == "file"

    from_env = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_NEWS_URL": "https://env.example/news.json",
        }
    )
    assert from_env.news_source_url == "https://env.example/news.json"
    assert from_env.news_source_url_source == "env"


def test_models_source_defaults_to_public_site(tmp_path: Path) -> None:
    config = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})

    assert config.models_source_url == DEFAULT_MODELS_SOURCE_URL
    assert config.models_source_url_source == "default"
    assert resolve_models_url(config) == DEFAULT_MODELS_SOURCE_URL


def test_models_source_resolves_file_then_environment(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"models_source_url": "https://file.example/models.json"}),
        encoding="utf-8",
    )

    from_file = resolve({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    assert from_file.models_source_url == "https://file.example/models.json"
    assert from_file.models_source_url_source == "file"

    from_env = resolve(
        {
            "PRACTICEGRAPH_DATA_DIR": str(tmp_path),
            "PRACTICEGRAPH_MODELS_URL": "https://env.example/models.json",
        }
    )
    assert from_env.models_source_url == "https://env.example/models.json"
    assert from_env.models_source_url_source == "env"
