"""Money formatting: integer-only, deterministic, half-up (FR-ANL-1, INV-6)."""

from __future__ import annotations

import pytest

from practicegraph.report.format import count, usd


def test_usd_two_places() -> None:
    assert usd(0) == "$0.00"
    assert usd(39_305) == "$0.04"
    assert usd(994_999) == "$0.99"
    assert usd(995_000) == "$1.00"  # half-up at the boundary
    assert usd(1_234_567_890) == "$1,234.57"


def test_usd_four_places() -> None:
    assert usd(23_880, 4) == "$0.0239"
    assert usd(5_175, 4) == "$0.0052"
    assert usd(90, 4) == "$0.0001"


def test_usd_rejects_out_of_contract_input() -> None:
    with pytest.raises(ValueError):
        usd(-1)
    with pytest.raises(ValueError):
        usd(100, 0)
    with pytest.raises(ValueError):
        usd(100, 7)


def test_count_grouping_is_locale_independent() -> None:
    assert count(0) == "0"
    assert count(1_234_567) == "1,234,567"


def test_compact_tiers() -> None:
    from practicegraph.report.format import compact

    assert compact(981) == "981"
    assert compact(3_140) == "3.1K"
    assert compact(18_400_000) == "18.4M"
    assert compact(10_613_100_000) == "10.6B"


def test_duration_hm() -> None:
    """P4 waiting-time rendering: compact h/m, zero-padded minutes once hours
    appear (mono-column alignment), integer math only."""
    from practicegraph.report.format import duration_hm

    assert duration_hm(0) == "0m"
    assert duration_hm(45) == "45m"
    assert duration_hm(60) == "1h 00m"
    assert duration_hm(65) == "1h 05m"
    assert duration_hm(872) == "14h 32m"
    with pytest.raises(ValueError):
        duration_hm(-1)
