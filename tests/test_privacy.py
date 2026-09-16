"""Scanner behavior tests (NFR-PRV-1, FR-FOC-8). The scanners themselves are
exercised against every rendered surface in the report/doctor/CLI tests."""

from __future__ import annotations

from practicegraph.privacy import FORBIDDEN_LEXICON, leak_findings, lexicon_violations


def test_forbidden_lexicon_is_pinned() -> None:
    assert FORBIDDEN_LEXICON == (
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


def test_lexicon_violations_case_insensitive() -> None:
    assert lexicon_violations("Boost your Dopamine with this NEURO trick") == [
        "dopamine",
        "neuro",
    ]
    assert lexicon_violations("Your longest focus block was 92 minutes.") == []


def test_leak_findings_detects_seeded_leaks() -> None:
    assert "windows_absolute_path" in leak_findings(r"see C:\Users\alice\notes.txt")
    assert "unix_home_path" in leak_findings("stored under /home/alice/logs")
    assert "url" in leak_findings("visit https://internal.example.net/x")
    assert "email_address" in leak_findings("mail bob@example.org today")
    assert "api_key_shape" in leak_findings("token sk-abc123def456ghi789")
    assert "long_hex_blob" in leak_findings("id 0123456789abcdef0123456789abcdef")


def test_leak_findings_clean_on_product_copy() -> None:
    clean = (
        "PRACTICEGRAPH DAILY REPORT\n"
        "Estimated spend: $4.20 (estimate, rate card bundled-2026-07-01)\n"
        "Sessions: 3 | Assistant turns: 6\n"
    )
    assert leak_findings(clean) == []
