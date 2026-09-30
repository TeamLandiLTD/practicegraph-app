"""Privacy scanners (NFR-PRV-1, FR-FOC-8).

These functions are used by the automated no-leak test suites and by future
emission-boundary validation. They report *pattern names only* — never the
matched text — so the scanners themselves cannot leak.
"""

from __future__ import annotations

import re

# FR-FOC-8: forbidden clinical/neurological lexicon for all focus-facing copy.
# Case-insensitive substring match; the spec's minimum set plus the science
# review's pathology-language ban (behavioral products must not borrow
# disorder vocabulary). "zone" is
# deliberately not listed — it collides with ordinary phrases like
# "time zone"; reviews watch for it instead.
FORBIDDEN_LEXICON: tuple[str, ...] = (
    "dopamine",
    "adhd",
    "brain",
    "addict",
    "neuro",
    "diagnos",
    "disorder",
    "clinical",
    "compulsi",
    "chasing",
)

# NFR-PRV-1: content/identity patterns that must never appear in any report,
# doctor output, alert text, or wire payload.
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("windows_absolute_path", re.compile(r"[A-Za-z]:\\")),
    ("unix_home_path", re.compile(r"/(?:home|Users)/")),
    ("url", re.compile(r"\bhttps?://", re.IGNORECASE)),
    ("email_address", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
    ("api_key_shape", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")),
    ("long_hex_blob", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
)


def lexicon_violations(text: str) -> list[str]:
    """Return the forbidden-lexicon terms present in ``text`` (case-insensitive)."""
    lowered = text.lower()
    return [term for term in FORBIDDEN_LEXICON if term in lowered]


def leak_findings(text: str) -> list[str]:
    """Return the *names* of leak patterns found in ``text`` (never the match)."""
    return [name for name, pattern in _LEAK_PATTERNS if pattern.search(text)]
