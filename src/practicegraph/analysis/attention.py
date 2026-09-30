"""Content-free human attention continuity derived from local activity marks.

Only records positively classified as human-originated by a source adapter are
eligible. Assistant output and tool traffic are discarded before calculation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from itertools import pairwise

from practicegraph.analysis.schedule import ScheduleProfile, classify_time
from practicegraph.report.format import percent
from practicegraph.store import (
    MARK_BRANCH_HASH,
    MARK_CWD_HASH,
    MARK_HUMAN_INITIATED,
    DayMarkRow,
)

EPISODE_GAP_MIN = 5
SWITCH_GAP_MIN = 30
COORDINATION_WINDOW_MIN = 15
COORDINATION_SESSION_MIN = 3
PROMPT_BURST_WINDOW_MIN = 10
PROMPT_BURST_EVENT_MIN = 4
HIGH_SWITCH_DAY_MIN = 3
MIN_ACTIVE_DAYS = 4
MIN_HUMAN_EVENTS = 10
MIN_KNOWN_COVERAGE_PCT = 70


def quiet_hours_reading(
    marks: list[DayMarkRow], schedule: ScheduleProfile, today: date,
) -> dict[str, object] | None:
    """Two local weeks of positively classified human events, never agent traffic."""
    if not schedule.confirmed:
        return None
    start = today - timedelta(days=6)
    prior_start = start - timedelta(days=7)
    observed: set[date] = set()
    quiet: set[date] = set()
    latest = ""
    for event in _human_events(marks, schedule.zone):
        day = event.timestamp.date()
        if not prior_start <= day <= today:
            continue
        observed.add(day)
        if classify_time(event.timestamp, schedule).inside_quiet_hours:
            quiet.add(day)
        latest = max(latest, event.timestamp.astimezone(UTC).isoformat())
    current = {day for day in observed if day >= start}
    if not current:
        return None
    return {
        "from_day": start.isoformat(), "to_day": today.isoformat(),
        "observed_days": len(current), "quiet_days": len(quiet & current),
        "prior_observed_days": len(observed - current),
        "prior_quiet_days": len(quiet - current),
        "observed_at": latest,
    }


@dataclass(frozen=True, slots=True)
class AttentionEpisode:
    start: datetime
    end: datetime
    local_day: str
    cwd_hash: str
    branch_hash: str
    session_keys: frozenset[str]
    human_events: int

    @property
    def known(self) -> bool:
        return bool(self.cwd_hash)


@dataclass(frozen=True, slots=True)
class AttentionDay:
    day: str
    human_events: int
    known_events: int
    episodes: int
    cross_workstream_switches: int
    coordination_windows: int
    prompt_burst_windows: int

    @property
    def high_switch(self) -> bool:
        return self.cross_workstream_switches >= HIGH_SWITCH_DAY_MIN


@dataclass(frozen=True, slots=True)
class AttentionWindow:
    active_days: int
    human_events: int
    known_events: int
    known_coverage_pct: int
    cross_workstream_switches: int
    high_switch_days: int
    coordination_windows: int
    coordination_days: int
    prompt_burst_windows: int
    prompt_burst_days: int
    confident: bool


@dataclass(frozen=True, slots=True)
class _AttentionEvent:
    timestamp: datetime
    local_day: str
    session_key: str
    cwd_hash: str
    branch_hash: str

    @property
    def known(self) -> bool:
        return bool(self.cwd_hash)


def _same_workstream(
    left_cwd: str, left_branch: str, right_cwd: str, right_branch: str
) -> bool:
    if not left_cwd or not right_cwd:
        return False
    if left_cwd != right_cwd:
        return False
    return not left_branch or not right_branch or left_branch == right_branch


def _human_events(
    marks: list[DayMarkRow], zone: tzinfo
) -> list[_AttentionEvent]:
    events: list[_AttentionEvent] = []
    for row in marks:
        mark = row[1:]
        if len(mark) <= MARK_HUMAN_INITIATED or not mark[MARK_HUMAN_INITIATED]:
            continue
        timestamp = datetime.fromisoformat(str(mark[1])).astimezone(zone)
        events.append(
            _AttentionEvent(
                timestamp=timestamp,
                local_day=timestamp.date().isoformat(),
                session_key=str(mark[0]),
                cwd_hash=str(mark[MARK_CWD_HASH]),
                branch_hash=str(mark[MARK_BRANCH_HASH]),
            )
        )
    return sorted(events, key=lambda event: (event.timestamp, event.session_key))


def _episodes(events: list[_AttentionEvent]) -> list[AttentionEpisode]:
    episodes: list[AttentionEpisode] = []
    for event in events:
        previous = episodes[-1] if episodes else None
        close = (
            previous is not None
            and event.timestamp - previous.end <= timedelta(minutes=EPISODE_GAP_MIN)
        )
        same_known = (
            previous is not None
            and _same_workstream(
                previous.cwd_hash,
                previous.branch_hash,
                event.cwd_hash,
                event.branch_hash,
            )
        )
        same_unknown_session = (
            previous is not None
            and not previous.known
            and not event.known
            and event.session_key in previous.session_keys
        )
        if previous is not None and close and (same_known or same_unknown_session):
            branch = previous.branch_hash or event.branch_hash
            episodes[-1] = AttentionEpisode(
                start=previous.start,
                end=event.timestamp,
                local_day=previous.local_day,
                cwd_hash=previous.cwd_hash or event.cwd_hash,
                branch_hash=branch,
                session_keys=previous.session_keys | {event.session_key},
                human_events=previous.human_events + 1,
            )
            continue
        episodes.append(
            AttentionEpisode(
                start=event.timestamp,
                end=event.timestamp,
                local_day=event.local_day,
                cwd_hash=event.cwd_hash,
                branch_hash=event.branch_hash,
                session_keys=frozenset({event.session_key}),
                human_events=1,
            )
        )
    return episodes


def _greedy_prompt_bursts(events: list[_AttentionEvent]) -> list[_AttentionEvent]:
    groups: dict[tuple[str, str, str], list[_AttentionEvent]] = defaultdict(list)
    for event in events:
        if event.known:
            groups[(event.session_key, event.cwd_hash, event.branch_hash)].append(event)
    qualifying: list[_AttentionEvent] = []
    for group in groups.values():
        index = 0
        while index + PROMPT_BURST_EVENT_MIN <= len(group):
            end_index = index + PROMPT_BURST_EVENT_MIN - 1
            if group[end_index].timestamp - group[index].timestamp <= timedelta(
                minutes=PROMPT_BURST_WINDOW_MIN
            ):
                qualifying.append(group[end_index])
                index = end_index + 1
            else:
                index += 1
    return qualifying


def _greedy_coordination(events: list[_AttentionEvent]) -> list[_AttentionEvent]:
    groups: dict[tuple[str, str], list[_AttentionEvent]] = defaultdict(list)
    for event in events:
        if event.known:
            groups[(event.cwd_hash, event.branch_hash)].append(event)
    qualifying: list[_AttentionEvent] = []
    for group in groups.values():
        index = 0
        while index < len(group):
            sessions: set[str] = set()
            matched: int | None = None
            for end_index in range(index, len(group)):
                if group[end_index].timestamp - group[index].timestamp > timedelta(
                    minutes=COORDINATION_WINDOW_MIN
                ):
                    break
                sessions.add(group[end_index].session_key)
                if len(sessions) >= COORDINATION_SESSION_MIN:
                    matched = end_index
                    break
            if matched is None:
                index += 1
            else:
                qualifying.append(group[matched])
                index = matched + 1
    return qualifying


def attention_by_day(
    marks: list[DayMarkRow], *, zone: tzinfo = UTC
) -> dict[str, AttentionDay]:
    """Return day counters from positively identified human-origin marks."""
    events = _human_events(marks, zone)
    if not events:
        return {}

    counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for event in events:
        counters[event.local_day]["human_events"] += 1
        counters[event.local_day]["known_events"] += int(event.known)

    episodes = _episodes(events)
    for episode in episodes:
        counters[episode.local_day]["episodes"] += 1
    for previous, current in pairwise(episodes):
        if (
            previous.known
            and current.known
            and not _same_workstream(
                previous.cwd_hash,
                previous.branch_hash,
                current.cwd_hash,
                current.branch_hash,
            )
            and current.start - previous.end <= timedelta(minutes=SWITCH_GAP_MIN)
        ):
            counters[current.local_day]["cross_workstream_switches"] += 1

    for event in _greedy_coordination(events):
        counters[event.local_day]["coordination_windows"] += 1
    for event in _greedy_prompt_bursts(events):
        counters[event.local_day]["prompt_burst_windows"] += 1

    return {
        day: AttentionDay(
            day=day,
            human_events=value["human_events"],
            known_events=value["known_events"],
            episodes=value["episodes"],
            cross_workstream_switches=value["cross_workstream_switches"],
            coordination_windows=value["coordination_windows"],
            prompt_burst_windows=value["prompt_burst_windows"],
        )
        for day, value in sorted(counters.items())
    }


def summarize_attention(days: Iterable[AttentionDay]) -> AttentionWindow:
    """Aggregate day counters and apply the independent confidence gate."""
    values = list(days)
    active_days = len(values)
    human_events = sum(day.human_events for day in values)
    known_events = sum(day.known_events for day in values)
    coverage = percent(known_events, human_events)
    confident = (
        active_days >= MIN_ACTIVE_DAYS
        and human_events >= MIN_HUMAN_EVENTS
        and coverage >= MIN_KNOWN_COVERAGE_PCT
    )
    return AttentionWindow(
        active_days=active_days,
        human_events=human_events,
        known_events=known_events,
        known_coverage_pct=coverage,
        cross_workstream_switches=sum(
            day.cross_workstream_switches for day in values
        ),
        high_switch_days=sum(day.high_switch for day in values),
        coordination_windows=sum(day.coordination_windows for day in values),
        coordination_days=sum(day.coordination_windows > 0 for day in values),
        prompt_burst_windows=sum(day.prompt_burst_windows for day in values),
        prompt_burst_days=sum(day.prompt_burst_windows > 0 for day in values),
        confident=confident,
    )
