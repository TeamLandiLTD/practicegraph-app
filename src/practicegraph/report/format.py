"""Deterministic formatting helpers. Integer math only — no floats, no locale."""

from __future__ import annotations


def usd(micro: int, places: int = 2) -> str:
    """Format integer micro-USD as a dollar string with half-up rounding."""
    if micro < 0:
        raise ValueError("negative amounts are not expected in this product")
    if not 0 < places <= 6:
        raise ValueError("places must be in 1..6")
    scale = 10 ** (6 - places)
    units = (micro + scale // 2) // scale
    digits = str(units).rjust(places + 1, "0")
    whole = f"{int(digits[:-places]):,}"
    return f"${whole}.{digits[-places:]}"


def count(value: int) -> str:
    """Thousands-separated integer (fixed ',' separator, locale-independent)."""
    return f"{value:,}"


def compact(value: int) -> str:
    """Compact token counts ("10.6B", "18.4M", "3.1K", "981"), integer math."""
    if value < 0:
        raise ValueError("negative counts are not expected in this product")
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= threshold:
            whole = value // threshold
            tenth = (value % threshold) * 10 // threshold
            return f"{whole}.{tenth}{suffix}"
    return str(value)


def percent(part: int, total: int) -> int:
    """Whole-number percentage, half-up, 0 when the total is 0."""
    if total <= 0:
        return 0
    return (part * 200 + total) // (2 * total)


def duration_hm(minutes: int) -> str:
    """Minutes as a compact duration ("14h 32m", "45m"), integer math. The
    minute part is zero-padded once hours appear so mono columns stay aligned."""
    if minutes < 0:
        raise ValueError("negative durations are not expected in this product")
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes}m"


def about(value: int) -> str:
    """A count rounded for a sentence (COPY_RULES rule 5): exact under a
    hundred, to the nearest ten under a thousand, nearest hundred under ten
    thousand, nearest thousand beyond. "about 1,300", never "1,280"."""
    number = int(value)
    if number < 100:
        return count(number)
    step = 10 if number < 1_000 else 100 if number < 10_000 else 1_000
    return count(int(round(number / step) * step))
