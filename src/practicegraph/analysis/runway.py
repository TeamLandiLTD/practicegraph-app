"""Quota runway: how much of each provider rate-limit window is spent today.

The daily-utility glance (A1). Providers meter usage over rolling windows — a
5-hour window and a weekly one, most commonly — and we already store each
reading locally (`rate_limit_marks`, P4). This composer turns today's readings
into a small per-window snapshot: where each window sits right now (its latest
reading), how high it reached today (the peak), and a calm register — comfort,
watch, or tight — from named integer thresholds.

Determinism (INV-6): the snapshot is pure arithmetic over stored rows, sorted,
with "today" passed in — compose it twice from one store and the bytes match.
Local-only forever (NFR-PRV-6): rate-limit readings never cross the wire, and
nothing here feeds an emit. "Withheld, not zero": a window with no reading today
is simply absent, and a day with no readings yields an unavailable snapshot the
UI hides — the tile never renders a hollow 0%.
"""

from __future__ import annotations

import dataclasses

# Register bands over the window's used percent (0-100). At or below comfort is
# calm; above comfort through watch is worth a glance; above watch is tight.
# Deliberately generous — this informs the daily glance, it never alarms.
RUNWAY_COMFORT_MAX_PCT = 60
RUNWAY_WATCH_MAX_PCT = 80

# The window lengths we name. Anything else is bucketed under "other" so an
# unexpected provider window still shows honestly rather than vanishing.
WINDOW_5H_MINUTES = 300
WINDOW_WEEK_MINUTES = 10080

# Closed labels for the windows we recognise — the only user-visible strings this
# module mints, scanned by the lexicon suite like every catalog.
RUNWAY_LABEL: dict[str, str] = {
    "5h": "5-hour window",
    "week": "Weekly window",
    "other": "Other window",
}


def _bucket(window_minutes: int) -> str:
    """The label key for a window length — recognised windows by exact match,
    everything else pooled under "other"."""
    if window_minutes == WINDOW_5H_MINUTES:
        return "5h"
    if window_minutes == WINDOW_WEEK_MINUTES:
        return "week"
    return "other"


def _register(pct: int) -> str:
    """The calm register for a used percent: comfort / watch / tight."""
    if pct <= RUNWAY_COMFORT_MAX_PCT:
        return "comfort"
    if pct <= RUNWAY_WATCH_MAX_PCT:
        return "watch"
    return "tight"


@dataclasses.dataclass(frozen=True, slots=True)
class RunwayBucket:
    """One window's runway read for today, render-ready. `latest_pct` is the
    most recent reading; `peak_pct` the day's high — both whole percents.
    `register` is comfort / watch / tight from the named thresholds."""

    bucket: str
    label: str
    latest_pct: int
    peak_pct: int
    register: str
    source_id: str = "unknown"
    observed_at: str = ""
    window_minutes: int = 0
    window_kind: str = ""
    resets_at: str = ""
    account_hash: str = ""
    pool_hash: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class RunwaySnapshot:
    """Today's per-window runway. `available` is False when the day has no
    readings at all — the UI hides the tile then (withheld, not zero). Buckets
    are ordered 5h, week, other, so the render list is stable."""

    available: bool
    buckets: tuple[RunwayBucket, ...]


_BUCKET_ORDER: dict[str, int] = {"5h": 0, "week": 1, "other": 2}


def _tenths_to_pct(tenths: int) -> int:
    """Percent-in-tenths to a whole percent, half-up (the same rounding the
    rest of the product uses for stored tenths)."""
    return (tenths + 5) // 10


def compose_runway(store: object, today: str) -> RunwaySnapshot:
    """The runway snapshot for one UTC day: for each window bucket seen today,
    its latest reading, its peak, and a register. Deterministic and local-only —
    derived only from (store, today), and the rate-limit readings never leave
    the machine.

    No readings today → an unavailable snapshot the UI hides; NEVER a zero tile.
    """
    rows = store.quota_details_for_day(today)  # type: ignore[attr-defined]
    if not rows:
        return RunwaySnapshot(available=False, buckets=())
    # rows arrive ordered by ts_utc; fold into per-bucket latest + peak.
    latest_tenths: dict[tuple[str, int, str, str, str], int] = {}
    peak_tenths: dict[tuple[str, int, str, str, str], int] = {}
    timestamps: dict[tuple[str, int, str, str, str], str] = {}
    resets: dict[tuple[str, int, str, str, str], str] = {}
    for source, ts, used_tenths, window_minutes, kind, reset, account, pool in rows:
        key = (source, window_minutes, kind, account, pool)
        latest_tenths[key] = used_tenths
        timestamps[key] = ts
        peak_tenths[key] = max(peak_tenths.get(key, 0), used_tenths)
        resets[key] = reset
    buckets: list[RunwayBucket] = []
    for key in sorted(latest_tenths, key=lambda k: (k[0], _BUCKET_ORDER[_bucket(k[1])], k[1])):
        source, window_minutes, kind, account, pool = key
        bucket = _bucket(window_minutes)
        latest_pct = _tenths_to_pct(latest_tenths[key])
        peak_pct = _tenths_to_pct(peak_tenths[key])
        buckets.append(
            RunwayBucket(
                bucket=bucket,
                label=RUNWAY_LABEL[bucket],
                latest_pct=latest_pct,
                peak_pct=peak_pct,
                register=_register(latest_pct),
                source_id=source,
                observed_at=timestamps[key],
                window_minutes=window_minutes,
                window_kind=kind,
                resets_at=resets[key],
                account_hash=account,
                pool_hash=pool,
            )
        )
    return RunwaySnapshot(available=True, buckets=tuple(buckets))
