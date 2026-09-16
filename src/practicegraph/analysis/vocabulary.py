"""The words on this page, taught once.

Every reading this product publishes is built on six terms, and a person
opening it for the first time reliably knows two of them. Until now the page
answered that with tooltips on individual cards — an explanation you can only
find if you already suspected there was something to look up.

The gap is real rather than assumed. A separate usage report built for the
same problem (reviewed 2026-08-06) devotes an entire first view to teaching
vocabulary before it shows a single number, on the reasoning that you would
not act on an instrument you cannot read. That matches what the architecture
assessment concluded from the other direction: the constraint on acting well
is not motivation, it is not knowing what the numbers are made of.

Three shapes per term, in the order a person actually needs them:

  gloss      what it means, in a sentence, assuming nothing
  like       what it is like, borrowed from ordinary working life — never
             athletic imagery, which this product uses sparingly and never
             to lead (owner call 2026-07-10)
  mechanics  the exact machinery, for whoever wants it and nobody else

Plus ``seen_in``: where on this page the word turns up. A definition with no
referent is trivia; the cross-reference is what turns it into a reading.

The copy is a closed catalog here rather than literals in the client, so the
lexicon and leak scanners cover it like every other user-facing string.
"""

from __future__ import annotations

from dataclasses import dataclass

from practicegraph.store import Store

# One-time surface, dismissed permanently — the same contract as the economy
# first-open retrospective (AM-3): meta table only, never on the wire.
VOCABULARY_META_KEY = "vocabulary_dismissed:v1"

# Closed copy catalog (NFR-QLT-3, lexicon-scanned). Ordered so each term can
# be read using only the terms above it: a session holds context, context is
# what caching discounts, spend is what the discount applies to, tier is the
# choice that moves spend most, and a unit is what spend is divided by.
VOCABULARY_COPY: dict[str, str] = {
    "eyebrow": "The words on this page",
    "title": "Six words worth knowing",
    "intro": (
        "Each one in plain words first, then what it is like, then the exact "
        "machinery for whoever wants it."
    ),
    "dismiss": "Got it",
    "footer": (
        "Got it puts this card away for good. Every word stays explained "
        "under the small i beside each card title."
    ),
    "session-term": "Session",
    "session-gloss": (
        "One continuous conversation with the tool, from the first message to "
        "the last. Close it and begin again and that is a second session, "
        "even when the task has not changed."
    ),
    "session-like": (
        "Like one meeting rather than the project it belongs to: the project "
        "runs for weeks, while each meeting has its own start, its own end "
        "and its own record."
    ),
    "session-mechanics": (
        "Each tool writes one log file per session on this computer. This "
        "app reads those files for counts, timings and totals, and never for "
        "what you or the model actually wrote."
    ),
    "session-seen": "The session receipt, which reports your most recent one.",
    "context-term": "Context",
    "context-gloss": (
        "Everything the tool is holding for the session so far — your "
        "messages, its replies, and every file it opened — all of which it "
        "re-reads before writing each new reply."
    ),
    "context-like": (
        "Like being handed the whole case file before every question instead "
        "of the latest page, where most of the file is material the tool went "
        "and fetched rather than anything you typed."
    ),
    "context-mechanics": (
        "Measured in tokens, each roughly three-quarters of a word. Reading "
        "volume rather than writing volume dominates the arithmetic of agent "
        "work, which is why a long session costs more than a short one even "
        "when the replies themselves are brief."
    ),
    "context-seen": (
        "The carried-context line on the session receipt, and the context "
        "hygiene reading."
    ),
    "cache-term": "Cache reuse",
    "cache-gloss": (
        "The share of that re-reading the provider recognises as material it "
        "has seen recently, and charges at a fraction of the first-read rate."
    ),
    "cache-like": (
        "Like a retainer rate on familiar documents: the second reading of "
        "the same file is billed at a fraction of what the first one cost."
    ),
    "cache-mechanics": (
        "Repeated input typically prices near a tenth of fresh input. It is "
        "the main reason a long session does not cost in proportion to its "
        "length, and the reason a session left idle long enough for the cache "
        "to lapse pays the full rate again when it resumes."
    ),
    "cache-seen": (
        "Cache reuse on the economy card, and the re-cache share in the "
        "churn line."
    ),
    "spend-term": "Model spend",
    "spend-gloss": (
        "What the work would come to at published rates: the tokens counted "
        "from your own logs, priced per model. It is deliberately not called "
        "cost, because on most plans nobody sends you a bill for it."
    ),
    "spend-like": (
        "Like costing a job at standard rates rather than reading an invoice "
        "— sound for comparing one week against another, and not a statement "
        "of what you were charged."
    ),
    "spend-mechanics": (
        "On a subscription the marginal token carries no separate charge at "
        "all, so these figures are capability accounting rather than money "
        "owed. Totals are a floor: models this app cannot price are reported "
        "as withheld, never quietly counted as zero."
    ),
    "spend-seen": (
        "Every dollar figure on the page, and the note under the economy card "
        "that names which of the two you are looking at."
    ),
    "tier-term": "Model tier",
    "tier-gloss": (
        "Which model does the work. The tiers differ several-fold in rate, "
        "and the choice is made per task rather than once."
    ),
    "tier-like": (
        "Like staffing grades on a job: the same brief at a very different "
        "rate. You would not put the most senior reviewer on reformatting a "
        "table, and you would not handle a delicate migration with the "
        "cheapest hand available."
    ),
    "tier-mechanics": (
        "Rates differ on both reading and writing, and writing prices several "
        "times higher than reading on every tier. A switch applies to the "
        "next message rather than the one in flight."
    ),
    "tier-seen": (
        "The routing verdict, on the weeks one fires, and the premium share "
        "on the economy card."
    ),
    "unit-term": "Work unit",
    "unit-gloss": (
        "One stretch of work on one thing. Neither tool has a name for this, "
        "so the term is ours: sessions get abandoned and picked up again, and "
        "a unit follows the work across them."
    ),
    "unit-like": (
        "Like an item on a board rather than the hours booked against it — "
        "the same piece of work can span several sittings and still be one "
        "thing you were doing."
    ),
    "unit-mechanics": (
        "Built from timestamps alone: turns sharing a directory and branch, "
        "chained across session boundaries, and divided wherever a gap of "
        "thirty minutes or more falls. The thresholds are frozen, so the same "
        "history always divides the same way."
    ),
    "unit-seen": (
        "The work units card, where spend is divided by units of work instead "
        "of by days."
    ),
}

# Order is load-bearing: each term is written using only the terms above it.
VOCABULARY_TERM_IDS: tuple[str, ...] = (
    "session",
    "context",
    "cache",
    "spend",
    "tier",
    "unit",
)


@dataclass(frozen=True, slots=True)
class VocabularyTerm:
    """One word, at three depths, plus where it turns up on this page."""

    term_id: str
    term: str
    gloss: str
    like: str
    mechanics: str
    seen_in: str


@dataclass(frozen=True, slots=True)
class VocabularyCard:
    """The teaching surface. Absent once dismissed, and absent for good."""

    eyebrow: str
    title: str
    intro: str
    dismiss: str
    footer: str
    terms: tuple[VocabularyTerm, ...]


def _term(term_id: str) -> VocabularyTerm:
    return VocabularyTerm(
        term_id=term_id,
        term=VOCABULARY_COPY[f"{term_id}-term"],
        gloss=VOCABULARY_COPY[f"{term_id}-gloss"],
        like=VOCABULARY_COPY[f"{term_id}-like"],
        mechanics=VOCABULARY_COPY[f"{term_id}-mechanics"],
        seen_in=VOCABULARY_COPY[f"{term_id}-seen"],
    )


def build_vocabulary_card(store: Store) -> VocabularyCard | None:
    """The card until it is dismissed, then ``None`` for the life of the store.

    Deliberately not gated on how new the install is. Someone who has run this
    for months may still never have been told what carried context is, and a
    card they can put away in one click costs them a glance."""
    if store.meta_get(VOCABULARY_META_KEY) == "1":
        return None
    return VocabularyCard(
        eyebrow=VOCABULARY_COPY["eyebrow"],
        title=VOCABULARY_COPY["title"],
        intro=VOCABULARY_COPY["intro"],
        dismiss=VOCABULARY_COPY["dismiss"],
        footer=VOCABULARY_COPY["footer"],
        terms=tuple(_term(term_id) for term_id in VOCABULARY_TERM_IDS),
    )


def record_vocabulary_seen(store: Store) -> None:
    """Put the card away permanently. Idempotent, local, never on the wire."""
    store.meta_set(VOCABULARY_META_KEY, "1")
