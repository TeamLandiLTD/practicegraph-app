"""The practice check-in: four pillars read from your own record.

This is the composer behind the "Your training" view. It reads the same local
signals the rest of the product computes (the 6 performance dimensions plus the
28-day rhythm/focus stats) and produces, per PILLAR, a short read: where you
are, and one cue. The forbidden-lexicon rule (FR-FOC-8) binds every line, so
nothing here reads as clinical; the lexicon suite scans this catalog like any
other.

**The working-pattern rule (2026-07-25).** This pillar used to be "recovery"
and spoke the language of rest, sustainable load and "the pace that lasts".
That was a wellbeing claim resting on timestamps, and felt load is precisely
what timestamps cannot see (Mark et al., CHI 2008: interrupted work finished
*faster* while stress and effort rose). Kluger & DeNisi makes it worse than
merely unfounded — over a third of feedback interventions reduce performance,
and the harm mechanism is feedback aimed at the self. So the pillar now states
when work landed and how continuously it ran, and stops there.

Four pillars, mapped to what we actually measure:
  - pattern   : the shape of the day — quiet-hours share, unbroken stretches
  - focus     : form — deep blocks, one thread at a time, not reacting on reflex
  - discipline: staying in the loop — reviewing handoffs, fixing not re-firing
  - craft     : mastery of the tools — context hygiene and model economy

Determinism (INV-6): closed copy, integer thresholds, sorted iteration — same
signals, same coaching. Local-only forever (NFR-PRV-6): coaching rides the view
model, never an emit. Descriptive/forward only — it never diagnoses or alarms.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta

from practicegraph.analysis.focus import RhythmStats, rhythm_stats
from practicegraph.analysis.performance import (
    DimensionReading,
    gather_performance,
)
from practicegraph.analysis.schedule import (
    ScheduleProfile,
    classify_time,
    compatibility_utc_schedule,
)
from practicegraph.store import Store

# A dimension at/below this 0-100 score is a "work-on-this" pillar; at/above the
# strong mark it is a "keep it" strength. Calibrated to the same weak/strong
# bands the rest of the product uses.
PILLAR_WEAK_MAX = 55
PILLAR_STRONG_MIN = 80

# Load: how intense the recent training has been. Derived from rhythm — long
# unbroken stretches and late/weekend hours are "heavy load"; a spread of
# deep-block days at a sane hour is "steady".
LOAD_HEAVY_STREAK_DAYS = 6  # days with a 120+ min no-pause stretch, in 28
LOAD_HEAVY_LATE_PCT = 20
LOAD_LIGHT_ACTIVE_DAYS = 6  # a quiet fortnight

# Each pillar draws its status from one or two dimensions plus a rhythm signal.
PILLARS: tuple[str, ...] = ("pattern", "focus", "discipline", "craft")

PILLAR_LABEL: dict[str, str] = {
    "pattern": "Working pattern",
    "focus": "Focus",
    "discipline": "Review & judgment",
    "craft": "Craft",
}

# What each pillar measures — the stable "on hover, tell me what this is"
# description (independent of today's reading). Observational, lexicon-clean.
PILLAR_MEASURES: dict[str, str] = {
    "pattern": (
        "The shape of the working day: what share of the work lands in your "
        "confirmed quiet hours, and how many long stretches ran without a "
        "pause. When the work happened, not how it felt."
    ),
    "focus": (
        "Form: how much of the work lands in protected, uninterrupted blocks on "
        "one task, versus short scattered stretches with frequent switching."
    ),
    "discipline": (
        "Staying in the loop: whether you read the handoff and fix from the "
        "error, rather than approving fast or retrying before reading it."
    ),
    "craft": (
        "Tool mastery: keeping the working context lean and matching the model "
        "tier to the task, so effort and spend stay deliberate."
    ),
}

# Which performance dimension(s) anchor each pillar's score.
PILLAR_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "pattern": ("working_pattern",),
    "focus": ("deep_work", "single_threading"),
    "discipline": ("execution_quality",),
    "craft": ("context_hygiene", "model_economy"),
}

# Closed coaching copy — scanned by the lexicon suite (NFR-QLT-3 reviewed diffs).
# Three registers per pillar: watch (load/weakness worth working on), steady
# (holding well), and a neutral base when there is not enough signal either way.
COACH_COPY: dict[str, str] = {
    # working pattern — the shape of the day, stated and left alone
    "pattern-heavy": (
        "{streak_days} of the last 28 days ran two hours or more with no "
        "15-minute gap. Whether that suits you is yours to judge; this is only "
        "what the timestamps show."
    ),
    "pattern-late": (
        "{late_pct}% of recent activity landed inside the quiet hours you "
        "confirmed. Intentional or not is not something a log can tell."
    ),
    "pattern-steady": (
        "Deep blocks on {deep_days} of {active_days} active days, and little "
        "activity in your quiet hours. That is the shape this month took."
    ),
    "pattern-base": (
        "This is when the work happened and how continuously it ran - the "
        "shape of the day, not a reading of how it went."
    ),
    # focus
    "focus-strong": (
        "Strong form — a 45-minute uninterrupted block on {deep_days} of "
        "{active_days} active days. Deep work is where the hard problems give in; "
        "keep protecting that first block."
    ),
    "focus-scattered": (
        "Focus is getting split — the work is landing in short stretches with "
        "frequent switching. One task carried to a stopping point beats three "
        "half-carried; pick the next single thread."
    ),
    "focus-base": (
        "Form is focus: one task at a time, a protected block for the hard piece. "
        "Reserve the first stretch of the day for the most demanding work."
    ),
    # discipline (staying in the loop — the habit, not a verdict on decisions)
    "discipline-strong": (
        "Review stayed in the loop — handoffs were read and fixes started from "
        "the error rather than a quick retry. Keep that judgment step visible."
    ),
    "discipline-watch": (
        "Watch the loop: several long runs were approved fast, or a retry went "
        "out before the error was read. A breath to read the handoff first is "
        "where the real fix usually starts."
    ),
    "discipline-base": (
        "You are the review step. A minute on the handoff, and a fix that starts "
        "in the error text, keep you in the loop."
    ),
    # craft
    "craft-strong": (
        "Sharp tool craft — context stays lean and model spend is deliberate. "
        "That is the mastery that compounds; keep the working set tight."
    ),
    "craft-watch": (
        "Room to sharpen the craft: context is running heavy or premium models "
        "are carrying routine work. Prune between tasks and match the tier to the "
        "task — small habits, real leverage."
    ),
    "craft-base": (
        "Craft is knowing the tools: a lean context, the right model for the "
        "task. Small, deliberate habits here pay off every session."
    ),
}

# The one-line version shown by default; the full COACH_COPY cue reveals on
# hover. Same ids, same numbers, so the summary and the detail agree. Keep each
# to a short clause — this is the calm surface, the cue is the depth behind it.
COACH_SUMMARY: dict[str, str] = {
    "pattern-heavy": "{streak_days} days ran 2h+ with no 15-minute gap.",
    "pattern-late": "{late_pct}% of activity landed in your quiet hours.",
    "pattern-steady": "Deep blocks on most active days, quiet hours mostly quiet.",
    "pattern-base": "When the work happened, and how continuously it ran.",
    "focus-strong": "Strong deep-block form — keep protecting the first block.",
    "focus-scattered": "Focus is split — carry one thread to a stopping point.",
    "focus-base": "One task at a time; a protected block for the hard piece.",
    "discipline-strong": "Reading handoffs and fixing from the error — hold it.",
    "discipline-watch": "Read the handoff before approving or retrying.",
    "discipline-base": "A minute on the handoff keeps you in the loop.",
    "craft-strong": "Lean context, deliberate spend — the mastery that compounds.",
    "craft-watch": "Prune context and match the model tier to the task.",
    "craft-base": "A lean context and the right model, every session.",
}

# ---- acknowledgment ledger (A3): the coach remembers last week's cue ----------
# The relationship feature. A cue shown one week, recorded with its baseline; the
# next week, if that pillar's score actually rose, the coach ACKNOWLEDGES it —
# once, warmly, with the two numbers. Silence otherwise; never a negative line,
# never a count of cues followed (that would be scorekeeping, and the streak
# dark-pattern PRINCIPLES.md forbids). Deterministic and local-only.

LEDGER_META_KEY = "coach_ledger:v1"
LEDGER_MAX_ENTRIES = 12  # oldest dropped; a rolling few months, not a history
ACK_MIN_DELTA = 8  # points of improvement worth acknowledging (calibrated)
# The acknowledgment looks back at last week's entry: at least a full week old
# (so a real week has passed) and no older than three (so it stays about the
# recent cue, not ancient history).
ACK_MIN_AGE_DAYS = 7
ACK_MAX_AGE_DAYS = 21

# Closed acknowledgment copy, one per pillar, {from_score}/{to_score} integers
# only. Warm and observational — it notices, it does not tally. Lexicon-scanned,
# and pinned free of streak/row/chain tokens (the acknowledgment-not-scorekeeping
# rule is a test, not a hope).
ACK_COPY: dict[str, str] = {
    "pattern": (
        "Working pattern changed from {from_score} to {to_score} after last "
        "week's cue. That is a change in the record, not proof of causation."
    ),
    "focus": (
        "Focus changed from {from_score} to {to_score} after last week's cue. "
        "That timing is an association, not proof of causation."
    ),
    "discipline": (
        "Review and judgment changed from {from_score} to {to_score} after last "
        "week's cue. The record does not establish causation."
    ),
    "craft": (
        "Craft changed from {from_score} to {to_score} after last week's cue. "
        "That is an observed association, not a causal conclusion."
    ),
}


@dataclasses.dataclass(frozen=True, slots=True)
class CoachPillar:
    """One pillar's coaching read — all locally derived, render-ready. `summary`
    is the one-line shown by default; `cue` is the full coaching sentence
    revealed on hover; `measures` is the stable "what this pillar measures"."""

    pillar: str
    label: str
    score: int
    tone: str  # "watch" | "steady" | "base"
    summary: str
    cue: str
    measures: str


def _score_for(
    scores: dict[str, int], dimensions: tuple[str, ...]
) -> int:
    """The pillar's score = the lowest of its anchor dimensions that are present
    (a pillar is only as strong as its weakest supporting habit). -1 if none."""
    present = [scores[d] for d in dimensions if d in scores]
    return min(present) if present else -1


def training_load(rhythm: RhythmStats | None) -> str:
    """A one-word load reading for the header: heavy / steady / light / unknown.
    Heavy when long no-pause stretches or late hours dominate; light on a quiet
    fortnight; steady otherwise."""
    if rhythm is None or rhythm.active_days == 0:
        return "unknown"
    if (
        rhythm.long_streak_days >= LOAD_HEAVY_STREAK_DAYS
        or (
            rhythm.quiet_hours_activity_pct is not None
            and rhythm.quiet_hours_activity_pct >= LOAD_HEAVY_LATE_PCT
        )
    ):
        return "heavy"
    if rhythm.active_days <= LOAD_LIGHT_ACTIVE_DAYS:
        return "light"
    return "steady"


# Each cue picker returns (tone, cue_id, values) so the composer can build the
# summary line AND the full cue from the same id, keeping them in sync.
def _pattern_cue(
    rhythm: RhythmStats | None, score: int
) -> tuple[str, str, dict[str, object]]:
    if rhythm is not None and rhythm.long_streak_days >= LOAD_HEAVY_STREAK_DAYS:
        return "watch", "pattern-heavy", {"streak_days": rhythm.long_streak_days}
    if (
        rhythm is not None
        and rhythm.quiet_hours_activity_pct is not None
        and rhythm.quiet_hours_activity_pct >= LOAD_HEAVY_LATE_PCT
    ):
        return "watch", "pattern-late", {"late_pct": rhythm.quiet_hours_activity_pct}
    if rhythm is not None and score >= PILLAR_STRONG_MIN and rhythm.active_days > 0:
        return "steady", "pattern-steady", {
            "deep_days": rhythm.deep_block_days,
            "active_days": rhythm.active_days,
        }
    return "base", "pattern-base", {}


def _focus_cue(
    rhythm: RhythmStats | None, score: int
) -> tuple[str, str, dict[str, object]]:
    if 0 <= score <= PILLAR_WEAK_MAX:
        return "watch", "focus-scattered", {}
    if score >= PILLAR_STRONG_MIN and rhythm is not None and rhythm.active_days > 0:
        return "steady", "focus-strong", {
            "deep_days": rhythm.deep_block_days,
            "active_days": rhythm.active_days,
        }
    return "base", "focus-base", {}


def _discipline_cue(score: int) -> tuple[str, str, dict[str, object]]:
    if 0 <= score <= PILLAR_WEAK_MAX:
        return "watch", "discipline-watch", {}
    if score >= PILLAR_STRONG_MIN:
        return "steady", "discipline-strong", {}
    return "base", "discipline-base", {}


def _craft_cue(score: int) -> tuple[str, str, dict[str, object]]:
    if 0 <= score <= PILLAR_WEAK_MAX:
        return "watch", "craft-watch", {}
    if score >= PILLAR_STRONG_MIN:
        return "steady", "craft-strong", {}
    return "base", "craft-base", {}


def compose_coaching(
    readings: tuple[DimensionReading, ...],
    rhythm: RhythmStats | None,
) -> list[CoachPillar]:
    """The check-in: one CoachPillar per pillar (pattern, focus,
    discipline, craft), each with a status score and a training cue, ordered
    watch-first so what needs work leads. Deterministic; local-only.

    `readings` are the current dimension scores; `rhythm` the 28-day window.
    Both come from the same gather the rest of the view uses — nothing here is
    computed anew or sent anywhere.
    """
    scores = {r.dimension_id: r.score for r in readings}
    pillars: list[CoachPillar] = []
    for pillar in PILLARS:
        score = _score_for(scores, PILLAR_DIMENSIONS[pillar])
        if pillar == "pattern":
            tone, cue_id, values = _pattern_cue(rhythm, score)
        elif pillar == "focus":
            tone, cue_id, values = _focus_cue(rhythm, score)
        elif pillar == "discipline":
            tone, cue_id, values = _discipline_cue(score)
        else:
            tone, cue_id, values = _craft_cue(score)
        pillars.append(
            CoachPillar(
                pillar=pillar,
                label=PILLAR_LABEL[pillar],
                score=score,
                tone=tone,
                summary=COACH_SUMMARY[cue_id].format(**values),
                cue=COACH_COPY[cue_id].format(**values),
                measures=PILLAR_MEASURES[pillar],
            )
        )
    # Watch first (what to work on), then steady, then base — a coach leads with
    # the thing to fix. Stable order within a tone by the fixed PILLARS order.
    tone_rank = {"watch": 0, "steady": 1, "base": 2}
    pillars.sort(key=lambda p: (tone_rank[p.tone], PILLARS.index(p.pillar)))
    return pillars


@dataclasses.dataclass(frozen=True, slots=True)
class AckLine:
    """One acknowledgment: the pillar that improved and the two integer scores.
    `text` is the render-ready line from ACK_COPY, numbers filled."""

    pillar: str
    from_score: int
    to_score: int
    text: str


def _monday_of(day: date) -> date:
    """The Monday (week start) of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def current_pillars(
    store: Store, today: date, schedule: ScheduleProfile | None = None
) -> list[CoachPillar] | None:
    """Recompose today's coaching from the same gather the view uses, so the
    ledger's baseline, the acknowledgment's 'current', and the weekly reading's
    theme all read one identical score path (INV-6). None when there is no
    activity to score yet."""
    schedule = schedule or compatibility_utc_schedule()
    performance = gather_performance(store, today, schedule)
    if performance is None:
        return None
    rhythm = _rhythm_for(store, today, schedule)
    return compose_coaching(performance.readings, rhythm)


def _rhythm_for(
    store: Store, today: date, schedule: ScheduleProfile
) -> RhythmStats | None:
    """The 28-day rhythm window ending today, interactive marks only — the same
    window and gate the coach reads (mirrors gather_shell_extras). None when the
    window is empty."""
    from practicegraph.analysis.focus import RHYTHM_WINDOW_DAYS
    from practicegraph.store import MARK_INTERACTIVE, MarkRow

    start_day = today - timedelta(days=RHYTHM_WINDOW_DAYS - 1)
    marks_by_day: dict[str, list[MarkRow]] = {}
    for row in store.marks_between(
        (start_day - timedelta(days=1)).isoformat(),
        (today + timedelta(days=1)).isoformat(),
    ):
        mark: MarkRow = row[1:]
        context = classify_time(datetime.fromisoformat(mark[1]), schedule)
        if not start_day <= context.local_day <= today:
            continue
        if not mark[MARK_INTERACTIVE]:
            continue
        marks_by_day.setdefault(context.local_day.isoformat(), []).append(mark)
    return rhythm_stats(marks_by_day, schedule) if marks_by_day else None


def read_ledger(store: Store) -> list[dict[str, object]]:
    """The acknowledgment ledger as a list of entry dicts (empty when unset or
    unreadable). A list in the meta table — serialized here, never via
    meta_set_json (which is typed for dicts)."""
    raw = store.meta_get(LEDGER_META_KEY)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _write_ledger(store: Store, entries: list[dict[str, object]]) -> None:
    """Persist the ledger (capped, oldest dropped). The list is serialized
    directly — meta_set_json is dict-typed and would fail mypy on a list."""
    capped = entries[-LEDGER_MAX_ENTRIES:]
    store.meta_set(LEDGER_META_KEY, json.dumps(capped, sort_keys=True))


def record_ledger_entry(
    store: Store, today: date, schedule: ScheduleProfile | None = None
) -> bool:
    """Tick-path write: record this week's watch-first pillar and its baseline
    score, once per week. No-op (returns False) when there is nothing to score
    or the week's entry already exists. Deterministic — derived only from
    (store, today). NEVER call from a view read.
    """
    pillars = current_pillars(store, today, schedule)
    if not pillars:
        return False
    lead = pillars[0]  # watch-first ordering: the pillar the coach is cueing
    if lead.score < 0:
        return False
    week_start = _monday_of(today).isoformat()
    entries = read_ledger(store)
    if any(
        entry.get("week_start") == week_start and entry.get("pillar") == lead.pillar
        for entry in entries
    ):
        return False  # one entry per pillar per week
    entries.append(
        {
            "week_start": week_start,
            "pillar": lead.pillar,
            "cue_key": f"{lead.pillar}-{lead.tone}",
            "baseline_score": lead.score,
        }
    )
    _write_ledger(store, entries)
    return True


def _qualifying_ack(
    entries: list[dict[str, object]],
    current_score: dict[str, int],
    today: date,
) -> tuple[str, int] | None:
    """The (pillar, baseline_score) of the most recent ledger entry that has
    earned an acknowledgment: not yet acked, within the [7, 21]-day window, its
    pillar improved by >= ACK_MIN_DELTA. Pure — no store touch, no mutation —
    and returns validated primitives so the caller needs no re-narrowing."""
    for entry in sorted(
        entries, key=lambda e: str(e.get("week_start", "")), reverse=True
    ):
        if entry.get("acked"):
            continue
        pillar = entry.get("pillar")
        baseline = entry.get("baseline_score")
        week_start_raw = entry.get("week_start")
        if not (isinstance(pillar, str) and isinstance(baseline, int)
                and isinstance(week_start_raw, str)):
            continue
        try:
            age_days = (today - date.fromisoformat(week_start_raw)).days
        except ValueError:
            continue
        if not ACK_MIN_AGE_DAYS <= age_days <= ACK_MAX_AGE_DAYS:
            continue
        current = current_score.get(pillar)
        if current is None or current < 0 or current - baseline < ACK_MIN_DELTA:
            continue
        return pillar, baseline
    return None


def compose_acknowledgment(
    store: Store, today: date, schedule: ScheduleProfile | None = None
) -> AckLine | None:
    """The read side, PURE (no write): if last week's recorded cue-pillar has
    since improved by at least ACK_MIN_DELTA, return the acknowledgment.
    Otherwise None — silence, never a negative line. Deterministic and
    local-only; safe to call from a view read (double-compose is identical).
    The 'fires once' guarantee is the [7, 21]-day window plus the tick-only
    mark_acknowledgment retirement — this function never mutates the ledger.
    """
    entries = read_ledger(store)
    if not entries:
        return None
    pillars = current_pillars(store, today, schedule)
    if not pillars:
        return None
    current_score = {p.pillar: p.score for p in pillars}
    qualifying = _qualifying_ack(entries, current_score, today)
    if qualifying is None:
        return None
    pillar, baseline = qualifying
    current = current_score[pillar]
    return AckLine(
        pillar=pillar,
        from_score=baseline,
        to_score=current,
        text=ACK_COPY[pillar].format(from_score=baseline, to_score=current),
    )


def mark_acknowledgment(store: Store, today: date) -> bool:
    """Tick-path write: retire ledger entries whose ack window has fully passed
    (older than ACK_MAX_AGE_DAYS) by marking them acked, so a past cue can never
    fire again in a later week. Returns True if anything was retired. NEVER call
    from a view read — this is the only place the ledger's acked flag is set.
    """
    entries = read_ledger(store)
    changed = False
    for entry in entries:
        if entry.get("acked"):
            continue
        week_start_raw = entry.get("week_start")
        if not isinstance(week_start_raw, str):
            continue
        try:
            age_days = (today - date.fromisoformat(week_start_raw)).days
        except ValueError:
            continue
        if age_days > ACK_MAX_AGE_DAYS:
            entry["acked"] = True
            changed = True
    if changed:
        _write_ledger(store, entries)
    return changed


def coaching_as_style_items(pillars: list[CoachPillar]) -> list[dict[str, str]]:
    """Present the cues to the restyle pipeline in its shape ({id, tone, text}),
    so the same local-CLI reword + validate path that freshens the reflection
    band can freshen the coaching cues. The pillar id keys the cache entry; the
    cue is the text to reword (numbers preserved by the restyle validator)."""
    return [
        {"id": p.pillar, "tone": p.tone, "text": p.cue}
        for p in pillars
    ]


def apply_styled_cues(
    pillars: list[CoachPillar], styled_items: list[dict[str, str]]
) -> list[CoachPillar]:
    """Overlay restyled cue text back onto the pillars by id, keeping every other
    field. A pillar with no accepted rewrite keeps its deterministic cue."""
    styled_by_id = {item["id"]: item["text"] for item in styled_items}
    return [
        dataclasses.replace(p, cue=styled_by_id.get(p.pillar, p.cue))
        for p in pillars
    ]
