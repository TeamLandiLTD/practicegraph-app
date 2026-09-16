"""The daily restyle: a local model may rewrite the WORDING of the reflection
band, never its FACTS. These tests pin the safety contract — a rewrite that
moves, adds, or drops a number is rejected; unclean copy is rejected; the
cache is fingerprinted to the facts so it never re-spends or goes stale into a
false claim; and every failure degrades to the deterministic sentence.

No real model is spawned — a fake runner stands in for the CLI so the whole
validation pipeline is exercised deterministically."""

from __future__ import annotations

import json

import practicegraph.report.restyle as restyle_mod
from practicegraph.report.restyle import (
    STYLE_META_KEY,
    STYLE_PROVIDERS,
    STYLE_SCHEMA,
    _accept,
    _run_cli,
    build_style_cache,
    cached_styled,
    facts_fingerprint,
    restyle_reflections,
)

SOURCE = [
    {
        "id": "late-pattern",
        "tone": "watch",
        "text": (
            "You worked past 22:00 on 24 of the last 28 days — 17% of "
            "everything this month happened at night."
        ),
    },
    {
        "id": "waiting",
        "tone": "watch",
        "text": (
            "You spent 65h 15m waiting on responses this month. The wait is "
            "only wasted when the answer does not get read."
        ),
    },
    {
        "id": "agent-share",
        "tone": "steady",
        "text": (
            "57% of the timeline was agents working on their own. Everything "
            "above is about the other 43% — the part only you can do."
        ),
    },
]

PROVIDER = STYLE_PROVIDERS["claude"]


def _runner(reply: str):
    def run(argv, prompt, timeout_s):
        return reply

    return run


def test_free_paraphrases_fall_back_even_with_identical_numbers() -> None:
    # Faithful rewrites: same numbers, same order, fresh words.
    reply = (
        "1. Past 22:00 on 24 of the last 28 days — night work was 17% of "
        "the whole month.\n"
        "2. Responses kept you waiting 65h 15m this month; that time only "
        "goes to waste when the reply goes unread.\n"
        "3. Agents ran on their own for 57% of the timeline. The rest — the "
        "other 43% — is the part only you can do.\n"
    )
    styled = restyle_reflections(SOURCE, PROVIDER, runner=_runner(reply))
    assert [line["id"] for line in styled] == [line["id"] for line in SOURCE]
    assert [line["tone"] for line in styled] == [line["tone"] for line in SOURCE]
    # Every line was genuinely reworded, and every number survived intact.
    for source_line, styled_line in zip(SOURCE, styled, strict=True):
        assert styled_line["text"] == source_line["text"]


def test_a_moved_statistic_is_rejected_line_falls_back() -> None:
    """The model changes 24 -> 25 on line 1 and invents a number on line 3;
    those lines must snap back to the source. Line 2 is faithful and stays."""
    reply = (
        "1. You worked past 22:00 on 25 of the last 28 days — 17% at night.\n"
        "2. You spent 65h 15m waiting on responses this month; wasted only "
        "when the answer is not read.\n"
        "3. Agents worked alone 57% of the time, across all 9 of your "
        "projects — the other 43% is yours.\n"
    )
    styled = restyle_reflections(SOURCE, PROVIDER, runner=_runner(reply))
    assert styled[0]["text"] == SOURCE[0]["text"]  # 25 != 24 -> rejected
    assert styled[2]["text"] == SOURCE[2]["text"]  # invented "9" -> rejected
    assert styled[1]["text"] == SOURCE[1]["text"]  # meaning is not mechanically proven


def test_dropped_number_is_rejected() -> None:
    assert (
        _accept(SOURCE[0]["text"], "You worked late on 24 of the last 28 days.") is None
    )  # dropped the 17%
    assert (
        _accept(SOURCE[1]["text"], "You spent time waiting on responses this month.") is None
    )  # dropped 65h 15m


def test_echoed_tone_tag_is_stripped() -> None:
    """The prompt shows each line's tone as '[watch] ...'; a model that echoes
    the tag back must not leak it into the rendered sentence."""
    reply = (
        "1. [watch] Past 22:00 on 24 of the last 28 days — 17% of the month "
        "was at night.\n"
        "2. [watch] Waiting on responses cost 65h 15m this month; wasted only "
        "when the answer is not read.\n"
        "3. [steady] Agents ran alone 57% of the timeline — the other 43% is "
        "yours.\n"
    )
    styled = restyle_reflections(SOURCE, PROVIDER, runner=_runner(reply))
    for line in styled:
        assert not line["text"].startswith("[")
        assert "[watch]" not in line["text"]
        assert "[steady]" not in line["text"]
    # ...and the rewrite still counts as accepted (not a fallback).
    assert styled[0]["text"] == SOURCE[0]["text"]


def test_transposed_numbers_are_rejected() -> None:
    """The subtle one: same numbers, reordered into a false statement. A
    multiset check would pass '28 of 24 days'; the sequence check must not —
    a rewrite may reorder words but never the statistics they carry."""
    swapped = "You worked past 22:00 on 28 of the last 24 days — 17% of the month was after dark."
    assert _accept(SOURCE[0]["text"], swapped) is None
    # And the faithful same-order rewrite of the very same line is accepted,
    # so the guard is not merely rejecting everything.
    faithful = "Past 22:00 on 24 of the last 28 days — 17% of the month happened at night."
    assert _accept(SOURCE[0]["text"], faithful) is None
    assert _accept(SOURCE[0]["text"], SOURCE[0]["text"]) is not None


def test_unclean_or_malformed_rewrites_are_rejected() -> None:
    # Lexicon violation (a forbidden word) — rejected however faithful.
    assert (
        _accept(
            SOURCE[2]["text"],
            "57% was agents; the other 43% is the part your brain must do.",
        )
        is None
    )
    # Empty and runaway lengths — rejected.
    assert _accept(SOURCE[0]["text"], "") is None
    assert _accept(SOURCE[0]["text"], "24 " + "and 28 " * 200) is None


def test_garbled_reply_degrades_to_source() -> None:
    # Wrong line count back from the model -> whole batch falls through.
    styled = restyle_reflections(SOURCE, PROVIDER, runner=_runner("just one line"))
    assert styled == SOURCE

    # A runner that raises -> whole batch falls through.
    def boom(argv, prompt, timeout_s):
        raise RuntimeError("no CLI on PATH")

    assert restyle_reflections(SOURCE, PROVIDER, runner=boom) == SOURCE
    assert restyle_reflections([], PROVIDER, runner=_runner("x")) == []


def test_cache_is_keyed_to_the_facts() -> None:
    """A cache built for one set of sentences only applies to that exact set;
    change any source text and the fingerprint misses (deterministic reuse,
    automatic invalidation — no stale rewrite outlives its facts)."""
    styled = restyle_reflections(
        SOURCE,
        PROVIDER,
        runner=_runner(
            "1. Past 22:00 on 24 of the last 28 days — 17% of the month at "
            "night.\n2. Waiting on responses cost 65h 15m this month.\n"
            "3. Agents solo 57% of the time; the other 43% is yours.\n"
        ),
    )
    doc = build_style_cache(SOURCE, styled, "2026-07-06T00:00:00+00:00")
    assert doc["schema"] == STYLE_SCHEMA
    assert doc["fingerprint"] == facts_fingerprint(SOURCE)
    raw = json.dumps(doc, sort_keys=True)

    # Same facts -> the styled band comes back.
    applied = cached_styled(SOURCE, raw)
    assert applied is not None
    assert applied[0]["text"] == SOURCE[0]["text"]

    # Different facts (one number moved at the source) -> cache miss, and the
    # caller shows the deterministic band.
    moved = [dict(SOURCE[0], text=SOURCE[0]["text"].replace("24", "23")), *SOURCE[1:]]
    assert cached_styled(moved, raw) is None
    assert cached_styled(SOURCE, None) is None
    assert cached_styled(SOURCE, "not json {") is None


def test_cache_revalidates_on_read() -> None:
    """Even a same-fingerprint cache is re-checked line by line on read, so a
    hand-tampered cache entry that moved a number cannot reach a surface."""
    tampered = {
        "schema": STYLE_SCHEMA,
        "fingerprint": facts_fingerprint(SOURCE),
        "generated_at": "2026-07-06T00:00:00+00:00",
        # "26" was never in the source line (it said 24 / 28 / 17%).
        "styled": {"late-pattern": "You worked past 22:00 on 26 of 28 days."},
    }
    applied = cached_styled(SOURCE, json.dumps(tampered))
    assert applied is not None
    assert applied[0]["text"] == SOURCE[0]["text"]  # rejected on read


def test_meta_roundtrip_via_store(tmp_path) -> None:
    """The cache persists through the store's meta table (no schema
    migration) and reads back as the styled band."""
    from practicegraph.store import Store

    store = Store(tmp_path / "state.db")
    store.migrate()
    styled = restyle_reflections(
        SOURCE,
        PROVIDER,
        runner=_runner(
            "1. 24 of the last 28 days ran past 22:00 — 17% at night.\n"
            "2. 65h 15m spent waiting on responses this month.\n"
            "3. 57% agents alone; 43% only you.\n"
        ),
    )
    doc = build_style_cache(SOURCE, styled, "2026-07-06T00:00:00+00:00")
    store.meta_set(STYLE_META_KEY, json.dumps(doc, sort_keys=True))
    applied = cached_styled(SOURCE, store.meta_get(STYLE_META_KEY))
    assert applied is not None
    assert applied[1]["text"] == SOURCE[1]["text"]


def test_run_cli_skips_when_provider_not_on_path(monkeypatch) -> None:
    """A missing provider CLI is a clean skip (deterministic text stands), never
    a bare-name spawn that could pick up a planted binary."""
    monkeypatch.setattr(restyle_mod.shutil, "which", lambda name: None)
    assert _run_cli(("claude", "-p"), "prompt", 5.0) == ""


def test_run_cli_launches_the_resolved_absolute_path(monkeypatch) -> None:
    """The provider is resolved to an absolute path before launch, so the OS
    never searches the CWD/PATH to LOCATE the executable."""
    resolved = r"C:\tools\claude.exe"
    seen: dict[str, object] = {}

    class _Completed:
        returncode = 0
        stdout = "styled output"

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _Completed()

    monkeypatch.setattr(restyle_mod.shutil, "which", lambda name: resolved)
    monkeypatch.setattr(restyle_mod.subprocess, "run", _fake_run)
    assert _run_cli(("claude", "-p"), "prompt", 5.0) == "styled output"
    assert seen["argv"] == [resolved, "-p"]  # absolute path, not the bare name


def test_the_model_subprocess_is_read_as_utf8() -> None:
    """The bug a real per-user run surfaced on 2026-07-27.

    `subprocess.run(text=True)` decodes with the PLATFORM default, which is
    cp1252 on Windows. The CLI emits UTF-8, so every em-dash came back as
    "â€"" — and because the styled band is cached by a fingerprint of the
    day's facts, it then rendered that to the person until the facts changed.

    It had never fired before because the LocalSystem service SKIPPED restyle
    entirely (`agent._restyle_items` refuses a PATH-resolved spawn under
    SYSTEM). Moving to a per-user install ran this path for the first time, on
    a real machine, and it was visible in the first screenshot."""
    import inspect

    from practicegraph.report import restyle

    source = inspect.getsource(restyle._run_cli)
    assert 'encoding="utf-8"' in source
    # A stray byte must degrade one character, never disable the whole feature
    # by raising inside the tick.
    assert 'errors="replace"' in source


def test_a_mojibake_rewrite_is_refused_even_if_the_numbers_survive() -> None:
    """Defence in depth behind the decode fix. A rewrite carrying UTF-8-as-
    cp1252 wreckage is indistinguishable from a broken product, and it would
    persist in the style cache, so it is refused and the deterministic
    sentence stands."""
    from practicegraph.report.restyle import _accept

    source = "You carved out a 45-minute stretch on 20 of 27 active days."
    clean = "A 45-minute stretch, uninterrupted, on 20 of 27 active days."
    assert _accept(source, clean) is None
    assert _accept(source, source) == source

    for wrecked in (
        "A 45-minute stretch â€” on 20 of 27 active days here.",
        "A 45-minute stretch on 20 of 27 active days � kept up nicely.",
        "Thatâ€™s a 45-minute stretch on 20 of 27 active days.",
    ):
        # The numbers are intact, the lexicon is clean - only the encoding
        # signature separates it from an acceptable rewrite.
        assert _accept(source, wrecked) is None, wrecked


def test_equal_numbers_do_not_allow_reversed_or_invented_claims() -> None:
    source = "Your activity rose by 20% over 28 days."
    assert _accept(source, "Your activity fell by 20% over 28 days.") is None
    assert _accept(source, "Your productivity rose by 20% over 28 days.") is None
    assert _accept(source, source.replace(" ", "  ")) is not None


def test_legacy_cached_paraphrases_are_withheld() -> None:
    source = [{
        "id": "activity", "tone": "watch", "text": "Your activity rose by 20% over 28 days.",
    }]
    unsafe = [{**source[0], "text": "Your activity fell by 20% over 28 days."}]
    cache = build_style_cache(source, unsafe, "2026-09-05T12:00:00+00:00")
    assert cached_styled(source, json.dumps(cache)) == source
