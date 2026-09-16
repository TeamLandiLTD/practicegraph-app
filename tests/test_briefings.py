"""The news / briefings feed: a catalog artifact the endpoint shows in the news
band. Inbound copy is validated exactly like every catalog (closed schema, caps,
closed kind vocabulary, https-only display url, lexicon + leak). No runnable
field; the url is display-only (never fetched here); which items were seen or
dismissed stays local and never reaches the wire."""

from __future__ import annotations

import json

from practicegraph.analysis.briefings import (
    BUNDLED_BRIEFINGS,
    MAX_BODY_LEN,
    briefings_artifact,
    parse_briefings_artifact,
)
from practicegraph.privacy import leak_findings, lexicon_violations


def test_bundled_feed_round_trips_and_is_scan_clean() -> None:
    artifact = briefings_artifact()
    parsed = parse_briefings_artifact(artifact)
    assert parsed is not None
    assert len(parsed) == len(BUNDLED_BRIEFINGS)
    for briefing in BUNDLED_BRIEFINGS:
        for text in (briefing.title, briefing.body):
            assert lexicon_violations(text) == []
            assert leak_findings(text) == []


def test_validator_rejects_bad_artifacts() -> None:
    good = briefings_artifact()

    # Unknown top-level key.
    assert parse_briefings_artifact({**good, "extra": 1}) is None
    # Unknown entry field (e.g. a smuggled action/script).
    bad_field = json.loads(json.dumps(good))
    bad_field["entries"][0]["action"] = "do-something"
    assert parse_briefings_artifact(bad_field) is None
    # Kind outside the closed vocabulary.
    bad_kind = json.loads(json.dumps(good))
    bad_kind["entries"][0]["kind"] = "spam"
    assert parse_briefings_artifact(bad_kind) is None
    # A non-https url is refused (display-only, https only).
    bad_url = json.loads(json.dumps(good))
    bad_url["entries"][0]["url"] = "http://insecure.example/x"
    assert parse_briefings_artifact(bad_url) is None
    bad_scheme = json.loads(json.dumps(good))
    bad_scheme["entries"][0]["url"] = "javascript:alert(1)"
    assert parse_briefings_artifact(bad_scheme) is None
    # Forbidden lexicon in body.
    bad_lex = json.loads(json.dumps(good))
    bad_lex["entries"][0]["body"] = "a dopamine hit every time you ship"
    assert parse_briefings_artifact(bad_lex) is None
    # A secret-shaped string is a leak.
    bad_leak = json.loads(json.dumps(good))
    bad_leak["entries"][0]["body"] = "use sk-abcdef0123456789abcdef0123456789"
    assert parse_briefings_artifact(bad_leak) is None
    # Oversized body.
    bad_len = json.loads(json.dumps(good))
    bad_len["entries"][0]["body"] = "x" * (MAX_BODY_LEN + 1)
    assert parse_briefings_artifact(bad_len) is None
    # Duplicate ids.
    dup = json.loads(json.dumps(good))
    dup["entries"].append(dict(dup["entries"][0]))
    assert parse_briefings_artifact(dup) is None


def test_https_url_is_accepted_and_optional() -> None:
    good = briefings_artifact()
    with_url = json.loads(json.dumps(good))
    with_url["entries"][0]["url"] = "https://example.com/paper"
    parsed = parse_briefings_artifact(with_url)
    assert parsed is not None
    assert parsed[0].url == "https://example.com/paper"
    # url absent is fine (it is optional).
    assert parse_briefings_artifact(good) is not None


def test_source_is_validated_and_optional() -> None:
    good = briefings_artifact()
    # A source label round-trips.
    with_src = json.loads(json.dumps(good))
    with_src["entries"][0]["source"] = "The Changelog"
    parsed = parse_briefings_artifact(with_src)
    assert parsed is not None
    assert parsed[0].source == "The Changelog"
    # A forbidden word in source is rejected.
    bad_src = json.loads(json.dumps(good))
    bad_src["entries"][0]["source"] = "dopamine weekly"
    assert parse_briefings_artifact(bad_src) is None
    # An oversized source is rejected.
    long_src = json.loads(json.dumps(good))
    long_src["entries"][0]["source"] = "x" * 41
    assert parse_briefings_artifact(long_src) is None
