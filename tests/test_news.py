"""The curated news schema: a richer editorial card (hook / summary / why /
thumbnail) over the raw briefings feed. These tests pin the safety contract —
inbound copy is untrusted, so the validator enforces the closed schema, caps,
closed kinds, https-only display url, self-origin-only thumbnail, and the
lexicon + leak scans over every reader-visible field."""

from __future__ import annotations

import json

from practicegraph.analysis.news import (
    MAX_HOOK_LEN,
    MAX_SUMMARY_LEN,
    parse_news_artifact,
)
from practicegraph.privacy import leak_findings, lexicon_violations


def _good() -> dict[str, object]:
    return {
        "news_version": "news-curated-abc123",
        "items": [
            {
                "id": "agent-harness-contracts",
                "kind": "paper",
                "title": "From prompts to contracts: harness engineering for agents",
                "hook": "Treating the agent loop as a testable contract, not a "
                "prompt, is what makes enterprise runs auditable and repeatable.",
                "summary": "The write-up frames the agent harness as an "
                "engineering surface: explicit inputs, checks, and a stopping "
                "rule you can review, rather than an opaque prompt. It walks "
                "through turning a vague build task into a legible plan you "
                "approve before code runs.",
                "why": "Directly useful if you run agents on real work — a "
                "legible harness is the difference between delegating and "
                "gambling.",
                "url": "https://arxiv.org/abs/2607.00000",
                "source": "arXiv cs.AI",
                "thumb": "assets/news/agent-harness-contracts.jpg",
            }
        ],
    }


def test_good_artifact_round_trips_and_is_scan_clean() -> None:
    parsed = parse_news_artifact(_good())
    assert parsed is not None
    assert len(parsed) == 1
    item = parsed[0]
    assert item.kind == "paper"
    assert item.thumb == "assets/news/agent-harness-contracts.jpg"
    for text in (item.title, item.hook, item.summary, item.why, item.source):
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_thumb_is_optional() -> None:
    doc = _good()
    del doc["items"][0]["thumb"]  # type: ignore[index]
    parsed = parse_news_artifact(doc)
    assert parsed is not None
    assert parsed[0].thumb is None


def test_thumb_accepts_data_uri_but_rejects_remote_image() -> None:
    # A self-contained data: URI is fine (served from our own origin).
    ok = _good()
    ok["items"][0]["thumb"] = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="  # type: ignore[index]
    assert parse_news_artifact(ok) is not None
    # A remote image URL must be rejected — it would break the site CSP and leak
    # which items render.
    bad = _good()
    bad["items"][0]["thumb"] = "https://i.ytimg.com/vi/abc/hqdefault.jpg"  # type: ignore[index]
    assert parse_news_artifact(bad) is None
    # An absolute path escaping the site root is rejected too.
    esc = _good()
    esc["items"][0]["thumb"] = "/etc/passwd"  # type: ignore[index]
    assert parse_news_artifact(esc) is None


def test_rejects_structural_deviations() -> None:
    good = _good()
    # Extra top-level key.
    assert parse_news_artifact({**good, "extra": 1}) is None
    # Missing a required copy field.
    miss = json.loads(json.dumps(good))
    del miss["items"][0]["why"]
    assert parse_news_artifact(miss) is None
    # Unknown per-item key.
    extra = json.loads(json.dumps(good))
    extra["items"][0]["runnable"] = "rm -rf /"
    assert parse_news_artifact(extra) is None
    # Bad kind.
    bad_kind = json.loads(json.dumps(good))
    bad_kind["items"][0]["kind"] = "advertisement"
    assert parse_news_artifact(bad_kind) is None
    # Bad id charset.
    bad_id = json.loads(json.dumps(good))
    bad_id["items"][0]["id"] = "Not An Id!"
    assert parse_news_artifact(bad_id) is None


def test_rejects_non_https_url() -> None:
    good = _good()
    http = json.loads(json.dumps(good))
    http["items"][0]["url"] = "http://arxiv.org/abs/2607.00000"
    assert parse_news_artifact(http) is None
    scheme = json.loads(json.dumps(good))
    scheme["items"][0]["url"] = "javascript:alert(1)"
    assert parse_news_artifact(scheme) is None


def test_rejects_lexicon_and_leak_in_any_copy_field() -> None:
    # A forbidden-lexicon word anywhere a reader sees it.
    for field in ("title", "hook", "summary", "why", "source"):
        bad = json.loads(json.dumps(_good()))
        bad["items"][0][field] = "this triggers dopamine loops in your brain"
        assert parse_news_artifact(bad) is None, f"lexicon not caught in {field}"
    # A secret-shaped string is a leak.
    leak = json.loads(json.dumps(_good()))
    leak["items"][0]["summary"] = "paste sk-abcdef0123456789abcdef0123456789 here"
    assert parse_news_artifact(leak) is None


def test_rejects_over_length_fields() -> None:
    long_hook = json.loads(json.dumps(_good()))
    long_hook["items"][0]["hook"] = "x" * (MAX_HOOK_LEN + 1)
    assert parse_news_artifact(long_hook) is None
    long_sum = json.loads(json.dumps(_good()))
    long_sum["items"][0]["summary"] = "y" * (MAX_SUMMARY_LEN + 1)
    assert parse_news_artifact(long_sum) is None


def test_rejects_duplicate_ids() -> None:
    dup = _good()
    dup["items"] = [dup["items"][0], dict(dup["items"][0])]  # type: ignore[index]
    assert parse_news_artifact(dup) is None


def test_empty_or_oversized_item_list_rejected() -> None:
    empty = _good()
    empty["items"] = []
    assert parse_news_artifact(empty) is None
