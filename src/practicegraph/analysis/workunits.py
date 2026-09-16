"""Work units (W4): deterministic cost-per-unit-of-work from stored spans.

The read-time fold over ``work_spans`` (docs/COST_PER_TASK_RESEARCH.md v2).
Three grains, all structural, no content, no LLM:

  span     what the store holds — a maximal run of assistant turns on one
           (session, project, branch) with no internal gap over 30 minutes
  episode  spans on the same (project, branch) chained across sessions and
           files whenever the gap between them stays inside the same 30
           minutes — one sitting on one thing. Session identity is
           deliberately NOT a boundary: 3% of episodes span a session id on
           the calibration store, and a /clear-and-continue is the same work
  unit     the thing that gets priced. A thread (all episodes on one
           project x branch) IS the unit while it stays small; a thread that
           runs past the line-of-work limits (a trunk, a long-lived personal
           branch) is not one task, so its EPISODES become the units

Honesty rules, load-bearing (all from the v2 research):
  - every figure is MODEL SPEND at listed rates — the token bill is one of
    several cost layers, and the copy never says "cost of the task"
  - the reading is a DISTRIBUTION (median, concentration, landed share) —
    same-task variance runs to 30x across runs, so a per-unit price tag is
    a hypothesis and is never rendered as a performance claim
  - "landed" means the unit carried at least one commit ATTEMPT — a gate,
    not a verdict ("merged is not correct"); quality stays with the
    reliance reading
  - semantics never: units are counted and priced, not named. Names, when
    they exist, come from the person (a rep's label), not from the logs.

LOCAL-ONLY forever (NFR-PRV-6): nothing here may feed an emit — the wire
field-name scan pins the vocabulary below. Deterministic (INV-6): every
threshold is a named constant frozen by the 91-day calibration run; changing
one requires a documented re-analysis, because determinism includes
determinism over time.

The card that would render this reading DOES NOT SHIP YET. The validation
gate (research doc §8) runs first: the day-close probe collects felt-vs-
measured unit counts with no unit count ever shown, and the reading surfaces
only if the two agree within the kill criterion. Until then this module
serves the probe's measured side and the dev verification path.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from practicegraph.analysis.schedule import ScheduleProfile, local_day
from practicegraph.store import Store

# The wire field-name scan pins these out of any payload (test_wire).
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "work_unit",
    "episode",
    "per_task",
    "landed",
    "concentration",
)

# Chaining threshold for spans -> episodes. Equal to the stored span grain
# (history.WORK_SPAN_GAP_MIN) by design: within one session the store already
# split at this gap, so chaining mostly rejoins cross-session and cross-file
# pieces. The 91-day calibration showed 30/60/120 all land within cents.
EPISODE_GAP_MIN = 30

# A unit must carry at least this many assistant turns to be substantial —
# below it, greetings and one-shot lookups would swamp the distribution.
WORK_UNIT_MIN_TURNS = 5

# The line-of-work rule (the trunk finding): a thread with more sittings or
# a longer day-span than this is a line of work, not one task — its episodes
# are the units. On the calibration store: 6 such threads carried 40% of
# spend; 373 threads at 1-2 episodes carried 36%.
LINE_OF_WORK_EPISODES = 10
LINE_OF_WORK_DAYS = 7

# Evidence floors (withheld, not zero). The decile line needs enough units
# for a tenth to be a group, not a single outlier.
WORK_UNITS_MIN = 20


@dataclass(frozen=True, slots=True)
class Episode:
    """One sitting on one (project, branch) — chained spans."""

    cwd_hash: str
    branch_hash: str
    first_ts: datetime
    last_ts: datetime
    assistant_turns: int
    cost_micro_usd: int
    unpriced_turns: int
    git_commit_attempts: int
    test_run_attempts: int
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    files_created: int = 0
    doc_files_created: int = 0
    export_writes: int = 0
    files_edited: int = 0
    session_keys: tuple[str, ...] = ()


@dataclass(slots=True)
class _EpisodeAccumulator:
    cwd_hash: str
    branch_hash: str
    first_ts: datetime
    last_ts: datetime
    assistant_turns: int
    input_tokens: int
    cached_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    cost_micro_usd: int
    unpriced_turns: int
    git_commit_attempts: int
    test_run_attempts: int
    files_created: int
    doc_files_created: int
    export_writes: int
    files_edited: int
    session_keys: set[str]


@dataclass(frozen=True, slots=True)
class WorkUnit:
    """One priced unit — a whole small thread, or one episode of a line of
    work. ``kind`` is the closed provenance tag."""

    kind: str  # "thread" | "line_episode"
    first_ts: datetime
    last_ts: datetime
    episodes: int
    assistant_turns: int
    cost_micro_usd: int
    git_commit_attempts: int
    files_created: int = 0

    @property
    def outcome(self) -> str:
        """Closed outcome vocabulary (W4.1): what the unit left behind.
        A commit outranks files; files outrank nothing; none is honest."""
        if self.git_commit_attempts > 0:
            return "commit"
        if self.files_created > 0:
            return "files"
        return "none"


# Closed copy catalog (NFR-QLT-3, lexicon-scanned). Distribution language
# only — never a per-task price tag; "model spend" never "cost"; landing is
# a gate, not a verdict. Slots carry integers or formatted integers.
WORKUNITS_COPY: dict[str, str] = {
    "eyebrow": "Pieces of work",
    "title": "What one piece of work costs",
    "hint": (
        "Your work split into pieces by project, branch and time, each "
        "priced at listed rates. One layer of what work costs, computed "
        "here."
    ),
    "label-units": "pieces this window",
    "label-median": "a typical piece",
    "label-p90": "a costly piece",
    "label-landed": "carried a commit",
    "concentration-line": (
        "The costliest tenth of your pieces took {pct}% of the spend."
    ),
    "landed-line": (
        "{landed} of {units} pieces reached a commit - an attempt, not a "
        "merge - and {files} more left files behind."
    ),
    # The two withheld sentences (W0.4): a floor is explained, never a blank.
    # The page vanishing where a reading should be reads as broken; the why
    # arrives in the exact slot the number will later occupy.
    "withheld-floor": (
        "Shows from {floor} pieces of work; this window holds {units} so "
        "far."
    ),
    # Landed is a GATE on evidence, and sometimes the evidence channel itself
    # is empty: no commit commands ran and no tracked files were written. For
    # work that produces documents through other paths, that is a blind spot
    # of the counting, not a zero - so it is said, not scored.
    "landed-withheld": (
        "Whether these pieces left something behind is unreadable this "
        "window: no commits and no tracked writes were counted."
    ),
    "top-title": "Largest pieces",
    "note": (
        "A piece of work is a branch worked to a stop, or one sitting on a "
        "long-running line. Counted and priced, never named."
    ),
}


@dataclass(frozen=True, slots=True)
class WorkUnitTop:
    """One display row for the largest units — shape only, no identity."""

    started_day: str
    hours_tenths: int  # duration in tenths of an hour (integer, INV-6)
    assistant_turns: int
    cost_micro_usd: int
    outcome: str  # commit | files | none
    kind: str


@dataclass(frozen=True, slots=True)
class WorkUnitsReading:
    """The window's distribution, render-ready. ``available=False`` hides
    everything (withheld, not zero)."""

    available: bool
    window_days: int
    units: int
    landed_units: int
    median_cost_micro_usd: int
    p90_cost_micro_usd: int
    top_decile_share_pct: int
    landed_share_pct: int
    file_units: int  # commit-less units that still left files behind
    line_of_work_units: int  # units that are episodes of a line of work
    concentration_line: str
    landed_line: str
    top: tuple[WorkUnitTop, ...]
    # Why the reading is withheld, when it is ("" when available). The page
    # renders this sentence in the slot the numbers will later occupy —
    # explained absence, never a silent one (W0.4).
    why: str = ""


_UNAVAILABLE = WorkUnitsReading(
    available=False,
    window_days=0,
    units=0,
    landed_units=0,
    median_cost_micro_usd=0,
    p90_cost_micro_usd=0,
    top_decile_share_pct=0,
    landed_share_pct=0,
    file_units=0,
    line_of_work_units=0,
    concentration_line="",
    landed_line="",
    top=(),
)


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def episodes_between(store: Store, from_day: date, to_day: date) -> list[Episode]:
    """Fold stored spans into episodes: one forward pass over the reader's
    (cwd, branch, first_ts) order, merging spans whose gap stays inside
    EPISODE_GAP_MIN or which overlap (parent and subagent transcript files
    carry disjoint turns of the same sitting). Deterministic and total."""
    rows = store.work_spans_between(from_day.isoformat(), to_day.isoformat())
    episodes: list[Episode] = []
    current: _EpisodeAccumulator | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        episodes.append(
            Episode(
                cwd_hash=current.cwd_hash,
                branch_hash=current.branch_hash,
                first_ts=current.first_ts,
                last_ts=current.last_ts,
                assistant_turns=current.assistant_turns,
                cost_micro_usd=current.cost_micro_usd,
                unpriced_turns=current.unpriced_turns,
                git_commit_attempts=current.git_commit_attempts,
                test_run_attempts=current.test_run_attempts,
                input_tokens=current.input_tokens,
                cached_tokens=current.cached_tokens,
                cache_creation_tokens=current.cache_creation_tokens,
                output_tokens=current.output_tokens,
                files_created=current.files_created,
                doc_files_created=current.doc_files_created,
                export_writes=current.export_writes,
                files_edited=current.files_edited,
                session_keys=tuple(sorted(current.session_keys)),
            )
        )
        current = None

    for (
        _session,
        cwd,
        branch,
        first_raw,
        last_raw,
        turns,
        _inp,
        _cached,
        _creation,
        _out,
        cost,
        unpriced,
        commits,
        tests,
        created,
        docs,
        exports,
        _edited,
    ) in rows:
        first = _parse_ts(first_raw)
        last = _parse_ts(last_raw)
        same_group = (
            current is not None
            and current.cwd_hash == cwd
            and current.branch_hash == branch
        )
        if same_group:
            assert current is not None
            gap = (first - current.last_ts).total_seconds() / 60
            if gap <= EPISODE_GAP_MIN:
                current.last_ts = max(current.last_ts, last)
                current.assistant_turns += turns
                current.input_tokens += _inp
                current.cached_tokens += _cached
                current.cache_creation_tokens += _creation
                current.output_tokens += _out
                current.cost_micro_usd += cost
                current.unpriced_turns += unpriced
                current.git_commit_attempts += commits
                current.test_run_attempts += tests
                current.files_created += created
                current.doc_files_created += docs
                current.export_writes += exports
                current.files_edited += _edited
                current.session_keys.add(_session)
                continue
        flush()
        current = _EpisodeAccumulator(
            cwd_hash=cwd,
            branch_hash=branch,
            first_ts=first,
            last_ts=last,
            assistant_turns=turns,
            input_tokens=_inp,
            cached_tokens=_cached,
            cache_creation_tokens=_creation,
            output_tokens=_out,
            cost_micro_usd=cost,
            unpriced_turns=unpriced,
            git_commit_attempts=commits,
            test_run_attempts=tests,
            files_created=created,
            doc_files_created=docs,
            export_writes=exports,
            files_edited=_edited,
            session_keys={_session},
        )
    flush()
    episodes.sort(key=lambda e: (e.first_ts, e.cwd_hash, e.branch_hash))
    return episodes


def work_units(episodes: list[Episode]) -> list[WorkUnit]:
    """Apply the two-tier unit rule: small threads are units whole; a line
    of work contributes its episodes. Only substantial material counts —
    a thread qualifies when its TOTAL turns clear the floor, an episode of a
    line of work when its own turns do."""
    threads: dict[tuple[str, str], list[Episode]] = {}
    for episode in episodes:
        threads.setdefault(
            (episode.cwd_hash, episode.branch_hash), []
        ).append(episode)

    units: list[WorkUnit] = []
    for members in threads.values():
        days = {member.first_ts.date() for member in members}
        day_span = (max(days) - min(days)).days + 1 if days else 0
        is_line = (
            len(members) > LINE_OF_WORK_EPISODES or day_span > LINE_OF_WORK_DAYS
        )
        if is_line:
            for member in members:
                if member.assistant_turns >= WORK_UNIT_MIN_TURNS:
                    units.append(
                        WorkUnit(
                            kind="line_episode",
                            first_ts=member.first_ts,
                            last_ts=member.last_ts,
                            episodes=1,
                            assistant_turns=member.assistant_turns,
                            cost_micro_usd=member.cost_micro_usd,
                            git_commit_attempts=member.git_commit_attempts,
                            files_created=member.files_created,
                        )
                    )
            continue
        total_turns = sum(member.assistant_turns for member in members)
        if total_turns < WORK_UNIT_MIN_TURNS:
            continue
        units.append(
            WorkUnit(
                kind="thread",
                first_ts=min(member.first_ts for member in members),
                last_ts=max(member.last_ts for member in members),
                episodes=len(members),
                assistant_turns=total_turns,
                cost_micro_usd=sum(m.cost_micro_usd for m in members),
                git_commit_attempts=sum(
                    m.git_commit_attempts for m in members
                ),
                files_created=sum(m.files_created for m in members),
            )
        )
    units.sort(key=lambda u: (u.first_ts, -u.cost_micro_usd))
    return units


def compose_work_units(
    store: Store, today: date, window_days: int = 28
) -> WorkUnitsReading:
    """The window's work-unit distribution. Withheld below the unit floor —
    a median of nine numbers is an anecdote, and a "top decile" needs a
    tenth that is a group."""
    start = today - timedelta(days=window_days - 1)
    units = work_units(episodes_between(store, start, today))
    if len(units) < WORK_UNITS_MIN:
        return dataclasses.replace(
            _UNAVAILABLE,
            why=WORKUNITS_COPY["withheld-floor"].format(
                floor=WORK_UNITS_MIN, units=len(units)
            ),
        )
    costs = sorted(unit.cost_micro_usd for unit in units)
    n = len(costs)
    total = sum(costs)
    top = costs[-max(1, n // 10):]
    landed = [u for u in units if u.git_commit_attempts > 0]
    decile_pct = (sum(top) * 100 // total) if total else 0
    file_units = [u for u in units if u.outcome == "files"]
    largest = sorted(units, key=lambda u: -u.cost_micro_usd)[:3]
    # The landed GATE can be structurally empty: zero commit commands AND zero
    # counted file writes across every unit. "0 of N carried a commit" would
    # then read as a verdict about work that simply is not counted this way
    # (a document produced through a script, for one). Unreadable is said as
    # unreadable (red-team finding 2026-08-13).
    landed_readable = bool(landed) or bool(file_units)
    landed_line = (
        WORKUNITS_COPY["landed-line"].format(
            landed=len(landed), units=n, files=len(file_units)
        )
        if landed_readable
        else WORKUNITS_COPY["landed-withheld"]
    )
    return WorkUnitsReading(
        available=True,
        window_days=window_days,
        units=n,
        landed_units=len(landed),
        median_cost_micro_usd=costs[n // 2],
        p90_cost_micro_usd=costs[min(n - 1, (n * 9) // 10)],
        top_decile_share_pct=decile_pct,
        landed_share_pct=len(landed) * 100 // n,
        file_units=len(file_units),
        line_of_work_units=sum(1 for u in units if u.kind == "line_episode"),
        concentration_line=WORKUNITS_COPY["concentration-line"].format(
            pct=decile_pct
        ),
        landed_line=landed_line,
        top=tuple(
            WorkUnitTop(
                started_day=unit.first_ts.date().isoformat(),
                hours_tenths=int(
                    (unit.last_ts - unit.first_ts).total_seconds() // 360
                ),
                assistant_turns=unit.assistant_turns,
                cost_micro_usd=unit.cost_micro_usd,
                outcome=unit.outcome,
                kind=unit.kind,
            )
            for unit in largest
        ),
    )


def measured_units_for_day(
    store: Store, day: date, profile: ScheduleProfile
) -> int:
    """The probe's measured side: how many substantial units STARTED on the
    person's local day. Counted over a window wide enough to catch units
    whose thread reaches back before the day — the unit's start decides its
    day, in the person's own timezone. Never rendered during validation."""
    units = work_units(
        episodes_between(store, day - timedelta(days=14), day + timedelta(days=1))
    )
    return sum(
        1 for unit in units if local_day(unit.first_ts, profile) == day
    )
