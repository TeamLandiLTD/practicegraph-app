"""The teaching layer: closed catalog, ordered terms, permanent dismissal."""

from __future__ import annotations

from pathlib import Path

from practicegraph.analysis.vocabulary import (
    VOCABULARY_COPY,
    VOCABULARY_META_KEY,
    VOCABULARY_TERM_IDS,
    build_vocabulary_card,
    record_vocabulary_seen,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.store import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "state.db")
    store.migrate()
    return store


def test_card_carries_every_term_at_three_depths(tmp_path: Path) -> None:
    card = build_vocabulary_card(_store(tmp_path))
    assert card is not None
    assert tuple(t.term_id for t in card.terms) == VOCABULARY_TERM_IDS
    for term in card.terms:
        # A term missing any depth is worse than no term: the reader who
        # needed the plain sentence gets the machinery instead.
        assert term.term and not term.term.endswith("."), term.term_id
        for text in (term.gloss, term.like, term.mechanics, term.seen_in):
            assert len(text) > 40, term.term_id
            assert text.endswith("."), term.term_id
        assert term.like.lower().startswith("like "), term.term_id


def test_terms_are_ordered_so_each_uses_only_the_ones_before_it() -> None:
    """The order is the teaching, not a preference. A session holds context,
    caching discounts that context, spend is what the discount applies to,
    tier is the choice that moves spend most, and a unit is what spend gets
    divided by. Reordering these silently breaks the explanation."""
    assert VOCABULARY_TERM_IDS == (
        "session", "context", "cache", "spend", "tier", "unit",
    )


def test_dismissal_is_permanent_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert build_vocabulary_card(store) is not None
    record_vocabulary_seen(store)
    assert build_vocabulary_card(store) is None
    record_vocabulary_seen(store)  # second press changes nothing
    assert build_vocabulary_card(store) is None
    assert store.meta_get(VOCABULARY_META_KEY) == "1"


def test_copy_passes_lexicon_and_leak_scans() -> None:
    for key, text in sorted(VOCABULARY_COPY.items()):
        assert lexicon_violations(text) == [], key
        assert leak_findings(text) == [], key


def test_copy_catalog_is_closed_in_both_directions() -> None:
    """Every key is reached by a term, and every term finds its key. An orphan
    string is copy nobody reviews; a missing one is a KeyError at render."""
    expected = {"eyebrow", "title", "intro", "dismiss", "footer"}
    for term_id in VOCABULARY_TERM_IDS:
        expected |= {
            f"{term_id}-{part}"
            for part in ("term", "gloss", "like", "mechanics", "seen")
        }
    assert set(VOCABULARY_COPY) == expected


def test_the_register_stays_calm(tmp_path: Path) -> None:
    """PRINCIPLES §1: the page reports, it does not sell or scold. Teaching
    copy is the easiest place to slip into either."""
    card = build_vocabulary_card(tmp_path and _store(tmp_path))
    assert card is not None
    blob = " ".join(
        [card.title, card.intro, card.footer]
        + [t.gloss + t.like + t.mechanics + t.seen_in for t in card.terms]
    ).lower()
    assert "!" not in blob
    for word in ("you should", "you must", "simply", "just ", "obviously",
                 "easy", "wrong", "bad habit", "waste"):
        assert word not in blob, word
