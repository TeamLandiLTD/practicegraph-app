"""Confirmed local schedule semantics for behavioral observations.

UTC remains the storage and accounting contract. This module supplies the
versioned, local-only interpretation layer used by practice surfaces.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tzlocal import get_localzone_name

from practicegraph.config import read_config_object, write_config_updates
from practicegraph.store import MarkRow, Store

WeekendMode = Literal["expected", "exceptional"]
WIRE_FORBIDDEN_TERMS = (
    "schedule_profile",
    "timezone_name",
    "working_days",
    "work_start",
    "work_end",
    "quiet_start",
    "quiet_end",
    "weekend_mode",
)
_CLOCK_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
TZDATA_VERSION = package_version("tzdata")
_PROFILE_FIELDS = {
    "version",
    "confirmed",
    "timezone_name",
    "tzdata_version",
    "working_days",
    "work_start",
    "work_end",
    "quiet_start",
    "quiet_end",
    "weekend_mode",
}


@dataclass(frozen=True, slots=True)
class ScheduleProfile:
    version: int
    confirmed: bool
    timezone_name: str
    tzdata_version: str
    working_days: tuple[int, ...]
    work_start: str
    work_end: str
    quiet_start: str
    quiet_end: str
    weekend_mode: WeekendMode

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


@dataclass(frozen=True, slots=True)
class ScheduleUpdate:
    timezone_name: str
    working_days: tuple[int, ...]
    work_start: str
    work_end: str
    quiet_start: str
    quiet_end: str
    weekend_mode: WeekendMode


@dataclass(frozen=True, slots=True)
class ScheduleContext:
    local_day: date
    local_hour: int
    inside_preferred_hours: bool
    inside_quiet_hours: bool
    on_preferred_day: bool


def compatibility_utc_schedule() -> ScheduleProfile:
    """Confirmed UTC profile for legacy direct analysis calls and fixtures.

    Product entry points always read and pass the local saved/suggested profile.
    """
    return ScheduleProfile(
        version=1,
        confirmed=True,
        timezone_name="UTC",
        tzdata_version=TZDATA_VERSION,
        working_days=(0, 1, 2, 3, 4),
        work_start="09:00",
        work_end="18:00",
        quiet_start="22:00",
        quiet_end="06:00",
        weekend_mode="exceptional",
    )


@lru_cache(maxsize=128)
def _clock(value: str) -> time:
    if not _CLOCK_RE.fullmatch(value):
        raise ValueError("invalid_clock")
    return time.fromisoformat(value)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown_timezone") from exc


def _detected_zone() -> str:
    try:
        name = get_localzone_name()
        _zone(name)
        return name
    except (OSError, ValueError, ZoneInfoNotFoundError):
        return "UTC"


def _suggestion() -> ScheduleProfile:
    return ScheduleProfile(
        version=0,
        confirmed=False,
        timezone_name=_detected_zone(),
        tzdata_version=TZDATA_VERSION,
        working_days=(0, 1, 2, 3, 4),
        work_start="09:00",
        work_end="18:00",
        quiet_start="22:00",
        quiet_end="07:00",
        weekend_mode="exceptional",
    )


def _validated_update(update: ScheduleUpdate) -> ScheduleUpdate:
    _zone(update.timezone_name)
    for value in (
        update.work_start,
        update.work_end,
        update.quiet_start,
        update.quiet_end,
    ):
        _clock(value)
    if (
        not update.working_days
        or len(set(update.working_days)) != len(update.working_days)
        or any(day < 0 or day > 6 for day in update.working_days)
    ):
        raise ValueError("invalid_working_days")
    if update.weekend_mode not in ("expected", "exceptional"):
        raise ValueError("invalid_weekend_mode")
    return dataclasses.replace(update, working_days=tuple(sorted(update.working_days)))


def _profile_from(value: object) -> ScheduleProfile | None:
    if not isinstance(value, dict) or set(value) != _PROFILE_FIELDS:
        return None
    try:
        version = value["version"]
        confirmed = value["confirmed"]
        timezone_name = value["timezone_name"]
        tzdata_version = value["tzdata_version"]
        working_days = value["working_days"]
        weekend_mode = value["weekend_mode"]
        clocks = tuple(value[key] for key in ("work_start", "work_end", "quiet_start", "quiet_end"))
        if not isinstance(version, int) or version < 1 or confirmed is not True:
            return None
        if not isinstance(timezone_name, str) or not isinstance(tzdata_version, str):
            return None
        if not isinstance(working_days, list) or not all(
            isinstance(day, int) for day in working_days
        ):
            return None
        if not all(isinstance(clock, str) for clock in clocks):
            return None
        update = _validated_update(
            ScheduleUpdate(
                timezone_name=timezone_name,
                working_days=tuple(working_days),
                work_start=cast(str, clocks[0]),
                work_end=cast(str, clocks[1]),
                quiet_start=cast(str, clocks[2]),
                quiet_end=cast(str, clocks[3]),
                weekend_mode=cast(WeekendMode, weekend_mode),
            )
        )
    except (KeyError, TypeError, ValueError):
        return None
    return ScheduleProfile(
        version=version,
        confirmed=True,
        timezone_name=update.timezone_name,
        tzdata_version=tzdata_version,
        working_days=update.working_days,
        work_start=update.work_start,
        work_end=update.work_end,
        quiet_start=update.quiet_start,
        quiet_end=update.quiet_end,
        weekend_mode=update.weekend_mode,
    )


def _history(data_dir: Path) -> tuple[list[ScheduleProfile], int] | None:
    raw = read_config_object(data_dir, "schedule")
    profiles_raw = raw.get("profiles")
    current = raw.get("current_version")
    if not raw:
        return [], 0
    if not isinstance(current, int) or not isinstance(profiles_raw, list):
        return None
    profiles = [_profile_from(value) for value in profiles_raw]
    if any(profile is None for profile in profiles):
        return None
    retained = cast(list[ScheduleProfile], profiles)
    if [profile.version for profile in retained] != list(range(1, len(retained) + 1)):
        return None
    if current != len(retained):
        return None
    return retained, current


def read_schedule_profile(data_dir: Path, version: int | None = None) -> ScheduleProfile:
    history = _history(data_dir)
    if history is None or not history[0]:
        if version is not None:
            raise ValueError("unknown_schedule_version")
        return _suggestion()
    profiles, current = history
    requested = current if version is None else version
    if requested < 1 or requested > len(profiles):
        raise ValueError("unknown_schedule_version")
    return profiles[requested - 1]


def save_schedule_profile(data_dir: Path, update: ScheduleUpdate) -> ScheduleProfile:
    clean = _validated_update(update)
    history = _history(data_dir)
    profiles = [] if history is None else history[0]
    profile = ScheduleProfile(
        version=len(profiles) + 1,
        confirmed=True,
        timezone_name=clean.timezone_name,
        tzdata_version=TZDATA_VERSION,
        working_days=clean.working_days,
        work_start=clean.work_start,
        work_end=clean.work_end,
        quiet_start=clean.quiet_start,
        quiet_end=clean.quiet_end,
        weekend_mode=clean.weekend_mode,
    )
    profiles.append(profile)
    write_config_updates(
        data_dir,
        {
            "schedule": {
                "current_version": profile.version,
                "profiles": [dataclasses.asdict(item) for item in profiles],
            }
        },
    )
    return profile


def _inside(value: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= value < end
    return value >= start or value < end


def local_day(timestamp: datetime, profile: ScheduleProfile) -> date:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp_not_timezone_aware")
    return timestamp.astimezone(profile.zone).date()


def classify_time(timestamp: datetime, profile: ScheduleProfile) -> ScheduleContext:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp_not_timezone_aware")
    local = timestamp.astimezone(profile.zone)
    clock = local.timetz().replace(tzinfo=None)
    return ScheduleContext(
        local_day=local.date(),
        local_hour=local.hour,
        inside_preferred_hours=(
            profile.confirmed
            and _inside(clock, _clock(profile.work_start), _clock(profile.work_end))
        ),
        inside_quiet_hours=(
            profile.confirmed
            and _inside(clock, _clock(profile.quiet_start), _clock(profile.quiet_end))
        ),
        on_preferred_day=(
            profile.confirmed and local.weekday() in profile.working_days
        ),
    )


def local_day_bounds_utc(
    day: date, profile: ScheduleProfile
) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=profile.zone).astimezone(UTC)
    end = datetime.combine(
        day + timedelta(days=1), time.min, tzinfo=profile.zone
    ).astimezone(UTC)
    return start, end


def marks_for_local_day(
    store: Store, day: date, profile: ScheduleProfile
) -> list[MarkRow]:
    """Return source marks whose UTC timestamp falls inside one local day."""
    start, end = local_day_bounds_utc(day, profile)
    rows: list[MarkRow] = []
    for stored in store.marks_between(
        (start.date() - timedelta(days=1)).isoformat(),
        (end.date() + timedelta(days=1)).isoformat(),
    ):
        mark: MarkRow = stored[1:]
        timestamp = datetime.fromisoformat(mark[1])
        if start <= timestamp < end:
            rows.append(mark)
    return sorted(rows, key=lambda row: row[1])
