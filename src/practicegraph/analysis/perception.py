"""Legacy local daily-feel note retained for historical continuity only."""

from __future__ import annotations

from dataclasses import dataclass

from practicegraph.store import Store

WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "checkin",
    "rating",
    "perception",
    "felt",
    "legacy_daily_feel",
)
LEGACY_MEASURE_ID = "legacy_daily_feel/v1"
RATING_MIN = 1
RATING_MAX = 5
_META_PREFIX = "checkin:"


@dataclass(frozen=True, slots=True)
class LegacyDailyFeel:
    measure_id: str
    rating: int


def record_checkin(store: Store, day: str, rating: int) -> bool:
    """Record one local historical note for a local calendar day."""
    if not RATING_MIN <= rating <= RATING_MAX:
        return False
    store.meta_set(f"{_META_PREFIX}{day}", str(rating))
    return True


def read_legacy_checkin(store: Store, day: str) -> LegacyDailyFeel | None:
    raw = store.meta_get(f"{_META_PREFIX}{day}")
    try:
        rating = int(raw) if raw is not None else 0
    except ValueError:
        return None
    if not RATING_MIN <= rating <= RATING_MAX:
        return None
    return LegacyDailyFeel(LEGACY_MEASURE_ID, rating)
