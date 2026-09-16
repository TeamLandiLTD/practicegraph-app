"""Human-performance profile: closed behavioral dimensions scored from the
same local heuristics that power focus, rhythm, and efficiency findings.

Six dimensions — deep work, working pattern, single-threading, context
hygiene, model economy, execution quality — each an integer 0-100 from
named-constant formulas over closed counters (spec §0 preamble, INV-6). The
"working pattern" dimension was called "recovery" until 2026-07-25; it scores
*when work lands and whether it runs unbroken*, which is observable, and never
claimed to see rest or strain, which is not. No content is ever
read, and the whole surface is local-only forever like every focus surface
(NFR-PRV-6): no symbol defined here may appear in a wire payload, enforced by
the same field-name scan. Copy follows FR-FOC-8 — behavioral observations
against your own history, never a judgment, never a comparison against
anyone else.

Per-day behavior is computed once (`behavior_by_day`) and shared by the
28-day scoring windows and the 12-week trajectory series, so report renders
stay one pass over the marks table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from practicegraph.analysis.attention import (
    AttentionDay,
    AttentionWindow,
    attention_by_day,
    summarize_attention,
)
from practicegraph.analysis.focus import (
    BATCH_MIN_CONCURRENT,
    DEEP_BLOCK_MIN,
    NUDGE_STREAK_MIN,
    block_counters,
    compute_metrics,
    interactive_marks,
)
from practicegraph.analysis.insights import (
    CONTEXT_BLOAT_AVG_PROMPT_TOKENS,
    LOW_CACHE_MIN_PROMPT_TOKENS,
    PREMIUM_HEAVY_MIN_SHARE_PCT,
)
from practicegraph.analysis.schedule import (
    ScheduleProfile,
    classify_time,
    compatibility_utc_schedule,
)
from practicegraph.report.format import percent
from practicegraph.store import ContributionRow, MarkRow, Store
from practicegraph.wire import PREMIUM_FAMILIES, model_family

PERFORMANCE_WINDOW_DAYS = 28  # scoring window (named constant)
TRAJECTORY_WEEKS = 12  # weekly trend series length (named constant)

# Performance data never reaches the wire (NFR-PRV-6) — same contract as the
# focus scan, enforced together by the wire field-name test.
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "performance",
    "score",
    "dimension",
    "working_pattern",
    "recovery",
    "execution",
    "rework",
    "command",
    "velocity",
)

DIMENSION_IDS: tuple[str, ...] = (
    "deep_work",
    "working_pattern",
    "single_threading",
    "context_hygiene",
    "model_economy",
    "execution_quality",
)

DIMENSION_LABELS: dict[str, str] = {
    "deep_work": "Deep work",
    "working_pattern": "Working pattern",
    "single_threading": "Attention continuity",
    "context_hygiene": "Context hygiene",
    "model_economy": "Model economy",
    "execution_quality": "Review & judgment",
}

# Closed level vocabulary (positive, behavioral).
LEVEL_STRONG = "strong"
LEVEL_STEADY = "steady"
LEVEL_BUILDING = "building"
STRONG_MIN_SCORE = 70
STEADY_MIN_SCORE = 40

NEUTRAL_SCORE = 60  # used when a window has too little signal to judge

# Deep work: weight of deep-block-day share vs the longest single block.
DEEP_SHARE_WEIGHT_PCT = 70
LONGEST_BLOCK_TARGET_MIN = 120

# Working pattern: penalties and the timer-break credit.
QUIET_HOURS_PENALTY_MAX = 30
NO_PAUSE_PENALTY_MAX = 40
BREAK_CREDIT_EACH = 2
BREAK_CREDIT_MAX = 10

# Single-threading: switching is the cost; parallel sessions on one task are
# fine — only 3+ concurrent sessions count as a parallel day.
SWITCH_PENALTY_PER_AVG_DAILY = 3
SWITCH_PENALTY_MAX = 45
PARALLEL_DAYS_PENALTY_MAX = 35
BURST_PENALTY_PER_AVG_DAILY = 7
BURST_PENALTY_MAX = 20

# Context hygiene: cache share is the base; oversized prompts subtract.
BLOAT_PENALTY = 20

# Model economy: only the share above the premium-heavy line subtracts.
UNPRICED_PENALTY_MAX = 15

# Execution quality (P1): command failures, slow waits, and TWO-SIDED rework.
# Rework is engagement, not waste — a neutral band carries no penalty; only
# heavy churn above it subtracts, and *zero* rework at real volume takes a
# small deduction (unreviewed acceptance is the risk, per the science notes).
EXEC_MIN_COMMANDS = 20  # neutral gate: below this AND low edit volume
EXEC_MIN_EDIT_VOLUME = 20  # tool results in the window (rework denominator)
EXEC_FAIL_PENALTY_MAX = 35
EXEC_SLOW_PENALTY_MAX = 15
REWORK_NEUTRAL_LOW_PCT = 3  # under this at volume: the no-review deduction
REWORK_HEAVY_PCT = 30  # over this: churn territory, penalty grows
REWORK_PENALTY_MAX = 20
NO_REVIEW_DEDUCTION = 8
# Is the rework channel even alive here? Rework arrives from Claude Code's
# `toolUseResult.userModified` (set only by the IDE-extension diff flow) and
# Codex's rejected `patch_apply_end`. A CLI/desktop user never emits the
# first, so "zero rework" for them means UNOBSERVED, not unreviewed — and
# deducting for it accuses someone of something the logs cannot see.
# Measured 2026-07-25 on 113 days of real history: 4 rework events in 47,621
# tool calls, with `userModified` false in all 5,518 observations across 1,337
# session files. Below this floor the window has no evidence the channel emits
# at all, so rework leaves the score and the evidence entirely (withheld, not
# zero). A session that genuinely reviews through the IDE clears it in a day.
REWORK_CHANNEL_MIN_OBSERVED = 5


def level_for(score: int) -> str:
    if score >= STRONG_MIN_SCORE:
        return LEVEL_STRONG
    if score >= STEADY_MIN_SCORE:
        return LEVEL_STEADY
    return LEVEL_BUILDING


def _clamp(score: int) -> int:
    return max(0, min(100, score))


@dataclass(frozen=True, slots=True)
class DayBehavior:
    """One day's closed behavioral counters, computed once and reused."""

    deep_block: bool
    no_pause: bool
    parallel: bool
    switches: int
    bursts: int
    longest_block_min: int
    events: int
    outside_preferred_events: int | None
    quiet_hours_events: int | None
    on_preferred_day: bool | None


def behavior_by_day(
    store: Store,
    start: date,
    end: date,
    schedule: ScheduleProfile | None = None,
) -> dict[str, DayBehavior]:
    """One pass over the marks table: per-day metrics for [start, end].

    P3 behavioral gate: sidechain (non-interactive) marks never feed the
    behavior scores — an agent's own grind is not your deep work, switching,
    or late night. Cost/token accounting reads contributions, not this."""
    schedule = schedule or compatibility_utc_schedule()
    marks_by_day: dict[str, list[MarkRow]] = {}
    outside_by_day: dict[str, int] = {}
    quiet_by_day: dict[str, int] = {}
    preferred_by_day: dict[str, bool] = {}
    for row in store.marks_between(
        (start - timedelta(days=1)).isoformat(),
        (end + timedelta(days=1)).isoformat(),
    ):
        mark: MarkRow = row[1:]
        if not interactive_marks([mark]):
            continue
        context = classify_time(datetime.fromisoformat(mark[1]), schedule)
        if not start <= context.local_day <= end:
            continue
        day = context.local_day.isoformat()
        marks_by_day.setdefault(day, []).append(mark)
        if schedule.confirmed:
            if not context.inside_preferred_hours:
                outside_by_day[day] = outside_by_day.get(day, 0) + 1
            if context.inside_quiet_hours:
                quiet_by_day[day] = quiet_by_day.get(day, 0) + 1
            preferred_by_day[day] = context.on_preferred_day
    behavior: dict[str, DayBehavior] = {}
    for day, marks in marks_by_day.items():
        metrics = compute_metrics(marks)
        behavior[day] = DayBehavior(
            deep_block=metrics.longest_block_min >= DEEP_BLOCK_MIN,
            no_pause=metrics.longest_streak_min >= NUDGE_STREAK_MIN,
            parallel=metrics.max_concurrent_sessions >= BATCH_MIN_CONCURRENT,
            switches=metrics.switch_count,
            bursts=metrics.burst_windows,
            longest_block_min=metrics.longest_block_min,
            events=metrics.event_count,
            outside_preferred_events=(
                outside_by_day.get(day, 0) if schedule.confirmed else None
            ),
            quiet_hours_events=(quiet_by_day.get(day, 0) if schedule.confirmed else None),
            on_preferred_day=(preferred_by_day.get(day) if schedule.confirmed else None),
        )
    return behavior


@dataclass(frozen=True, slots=True)
class WindowInputs:
    """Closed counters for one scoring window — heuristics in, no content."""

    window_days: int
    active_days: int
    deep_block_days: int
    longest_block_min: int
    quiet_hours_activity_pct: int | None
    no_pause_days: int
    switch_count: int
    parallel_days: int
    burst_windows: int
    breaks_completed: int
    cached_tokens: int
    fresh_input_tokens: int
    assistant_turns: int
    premium_cost_micro_usd: int
    priced_cost_micro_usd: int
    unpriced_turns: int
    # P1 execution-quality counters. tool_calls is the closest structural
    # edit-volume denominator the counters-only contract allows (no per-edit
    # counter exists; UNTAPPED plan sanctioned deriving from tool_calls).
    tool_calls: int = 0
    rework_edits: int = 0
    commands_run: int = 0
    commands_failed: int = 0
    commands_slow: int = 0
    # Verified human-attention counters. Raw transcript/session volume remains
    # above for descriptive views but never feeds wellbeing scoring.
    attention_active_days: int = 0
    attention_human_events: int = 0
    attention_known_events: int = 0
    attention_known_coverage_pct: int = 0
    attention_switch_count: int = 0
    attention_high_switch_days: int = 0
    attention_coordination_windows: int = 0
    attention_coordination_days: int = 0
    attention_prompt_burst_windows: int = 0
    attention_prompt_burst_days: int = 0
    attention_confident: bool = False


@dataclass(frozen=True, slots=True)
class DimensionReading:
    dimension_id: str
    score: int  # 0-100
    level: str
    evidence: tuple[str, ...]  # the numbers behind the score, ready to render
    delta: int | None = None  # vs the prior window; None without history
    neutral: bool = False  # too little signal to judge (never celebrated/flagged)


@dataclass(frozen=True, slots=True)
class AttentionRecommendation:
    kind: str
    impact: int
    title: str
    body: str
    evidence: str


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    window_days: int
    active_days: int
    overall: int
    delta_vs_prior: int | None  # vs the previous window; None without history
    readings: tuple[DimensionReading, ...]
    attention: AttentionWindow | None = None
    attention_recommendation: AttentionRecommendation | None = None


def _deep_work(window: WindowInputs) -> DimensionReading:
    share = percent(window.deep_block_days, window.active_days)
    block_part = (
        min(window.longest_block_min, LONGEST_BLOCK_TARGET_MIN) * 100
        // LONGEST_BLOCK_TARGET_MIN
    )
    score = _clamp(
        (share * DEEP_SHARE_WEIGHT_PCT + block_part * (100 - DEEP_SHARE_WEIGHT_PCT))
        // 100
    )
    evidence = (
        f"{DEEP_BLOCK_MIN}+ min block on {window.deep_block_days} of "
        f"{window.active_days} active days",
        f"longest {window.longest_block_min} min",
    )
    return DimensionReading("deep_work", score, level_for(score), evidence)


def _working_pattern(window: WindowInputs) -> DimensionReading:
    quiet_hours_penalty = (
        min(QUIET_HOURS_PENALTY_MAX, window.quiet_hours_activity_pct)
        if window.quiet_hours_activity_pct is not None
        else 0
    )
    no_pause_penalty = (
        percent(window.no_pause_days, window.active_days)
        * NO_PAUSE_PENALTY_MAX
        // 100
    )
    credit = min(BREAK_CREDIT_MAX, window.breaks_completed * BREAK_CREDIT_EACH)
    score = _clamp(100 - quiet_hours_penalty - no_pause_penalty + credit)
    evidence = [
        (
            f"activity during quiet hours {window.quiet_hours_activity_pct}%"
            if window.quiet_hours_activity_pct is not None
            else "quiet hours not confirmed"
        ),
        f"{window.no_pause_days} days with 2h+ and no 15-min pause",
    ]
    if window.breaks_completed:
        evidence.append(f"timer breaks taken {window.breaks_completed}")
    return DimensionReading(
        "working_pattern", score, level_for(score), tuple(evidence)
    )


def _single_threading(window: WindowInputs) -> DimensionReading:
    if not _attention_is_confident(window):
        return DimensionReading(
            "single_threading",
            NEUTRAL_SCORE,
            level_for(NEUTRAL_SCORE),
            (
                "not enough verified human attention to judge",
                "Background agent activity was excluded",
            ),
            neutral=True,
        )
    days = window.attention_active_days
    switch_penalty = 45 * window.attention_high_switch_days // days
    burst_penalty = 20 * window.attention_prompt_burst_days // days
    score = _clamp(100 - switch_penalty - burst_penalty)
    evidence = (
        f"{window.attention_high_switch_days} of {days} active days had "
        "3+ cross-workstream moves",
        "Background agent activity was excluded",
        f"nearby prompt bursts on {window.attention_prompt_burst_days} active days",
    )
    return DimensionReading("single_threading", score, level_for(score), evidence)


def _attention_is_confident(window: WindowInputs) -> bool:
    return (
        window.attention_confident
        and window.attention_active_days >= 4
        and window.attention_human_events >= 10
        and window.attention_known_coverage_pct >= 70
    )


def _attention_window(window: WindowInputs) -> AttentionWindow:
    return AttentionWindow(
        active_days=window.attention_active_days,
        human_events=window.attention_human_events,
        known_events=window.attention_known_events,
        known_coverage_pct=window.attention_known_coverage_pct,
        cross_workstream_switches=window.attention_switch_count,
        high_switch_days=window.attention_high_switch_days,
        coordination_windows=window.attention_coordination_windows,
        coordination_days=window.attention_coordination_days,
        prompt_burst_windows=window.attention_prompt_burst_windows,
        prompt_burst_days=window.attention_prompt_burst_days,
        confident=_attention_is_confident(window),
    )


def _attention_recommendation(
    current: WindowInputs, prior: WindowInputs | None
) -> AttentionRecommendation | None:
    days = current.attention_active_days
    if days <= 0:
        return None
    candidates: list[tuple[int, int, AttentionRecommendation]] = []
    switch_share = percent(current.attention_high_switch_days, days)
    baseline_trigger = False
    if prior is not None and _attention_is_confident(prior):
        prior_share = percent(
            prior.attention_high_switch_days, prior.attention_active_days
        )
        baseline_trigger = (
            prior_share > 0 and switch_share * 10 >= prior_share * 15
        )
    if (
        _attention_is_confident(current)
        and current.attention_high_switch_days >= 3
        and (switch_share >= 20 or baseline_trigger)
    ):
        item = AttentionRecommendation(
            kind="cross_workstream",
            impact=switch_share,
            title="Checkpoint one thread before moving",
            body=(
                "A short handoff note makes it easier to return to unfinished work."
            ),
            evidence=(
                f"On {current.attention_high_switch_days} of {days} active days, "
                "you moved between workstreams at least three times with less "
                "than 30 minutes between moves. Background agent activity was "
                "excluded."
            ),
        )
        candidates.append((item.impact, 3, item))

    coordination_share = percent(current.attention_coordination_days, days)
    if current.attention_coordination_days >= 3:
        item = AttentionRecommendation(
            kind="coordination",
            impact=coordination_share,
            title="Review agent results in batches",
            body=(
                "A planned review pass can reduce repeated check-ins while "
                "parallel work continues."
            ),
            evidence=(
                f"On {current.attention_coordination_days} active days, you "
                "responded across at least three agent threads in the same "
                "workstream within 15 minutes."
            ),
        )
        candidates.append((item.impact, 2, item))

    burst_share = percent(current.attention_prompt_burst_days, days)
    if current.attention_prompt_burst_days >= 3:
        item = AttentionRecommendation(
            kind="prompt_burst",
            impact=burst_share,
            title="Batch nearby asks into one turn",
            body=(
                "One composed request can replace several reactive check-ins "
                "when the goal is already clear."
            ),
            evidence=(
                f"On {current.attention_prompt_burst_days} active days, four or "
                "more separate asks landed in one agent thread within 10 minutes."
            ),
        )
        candidates.append((item.impact, 1, item))
    return max(candidates, default=(0, 0, None), key=lambda row: (row[0], row[1]))[2]


def _context_hygiene(window: WindowInputs) -> DimensionReading:
    prompt = window.cached_tokens + window.fresh_input_tokens
    if prompt < LOW_CACHE_MIN_PROMPT_TOKENS:
        return DimensionReading(
            "context_hygiene",
            NEUTRAL_SCORE,
            level_for(NEUTRAL_SCORE),
            ("not enough prompt volume in this window to judge",),
            neutral=True,
        )
    cache_pct = percent(window.cached_tokens, prompt)
    avg_prompt = prompt // max(1, window.assistant_turns)
    bloated = avg_prompt >= CONTEXT_BLOAT_AVG_PROMPT_TOKENS
    score = _clamp(cache_pct - (BLOAT_PENALTY if bloated else 0))
    evidence = [f"cache served {cache_pct}% of prompt tokens"]
    if bloated:
        evidence.append("very large prompts per turn")
    return DimensionReading("context_hygiene", score, level_for(score), tuple(evidence))


def _model_economy(window: WindowInputs) -> DimensionReading:
    if window.priced_cost_micro_usd == 0 and window.unpriced_turns == 0:
        return DimensionReading(
            "model_economy",
            NEUTRAL_SCORE,
            level_for(NEUTRAL_SCORE),
            ("no priced activity in this window",),
            neutral=True,
        )
    premium_pct = percent(window.premium_cost_micro_usd, window.priced_cost_micro_usd)
    over = max(0, premium_pct - PREMIUM_HEAVY_MIN_SHARE_PCT)
    unpriced_penalty = min(UNPRICED_PENALTY_MAX, window.unpriced_turns)
    score = _clamp(100 - over - unpriced_penalty)
    evidence = [f"premium models {premium_pct}% of priced spend"]
    if window.unpriced_turns:
        evidence.append(f"{window.unpriced_turns} unpriced turns")
    return DimensionReading("model_economy", score, level_for(score), tuple(evidence))


def _execution_quality(window: WindowInputs) -> DimensionReading:
    if (
        window.commands_run < EXEC_MIN_COMMANDS
        and window.tool_calls < EXEC_MIN_EDIT_VOLUME
    ):
        return DimensionReading(
            "execution_quality",
            NEUTRAL_SCORE,
            level_for(NEUTRAL_SCORE),
            ("not enough command or edit volume in this window to judge",),
            neutral=True,
        )
    fail_penalty = min(
        EXEC_FAIL_PENALTY_MAX, percent(window.commands_failed, window.commands_run)
    )
    slow_penalty = min(
        EXEC_SLOW_PENALTY_MAX, percent(window.commands_slow, window.commands_run)
    )
    rework_pct = percent(window.rework_edits, window.tool_calls)
    rework_observed = window.rework_edits >= REWORK_CHANNEL_MIN_OBSERVED
    rework_penalty = 0
    if rework_pct > REWORK_HEAVY_PCT:
        rework_penalty = min(REWORK_PENALTY_MAX, rework_pct - REWORK_HEAVY_PCT)
    elif (
        rework_observed
        and rework_pct < REWORK_NEUTRAL_LOW_PCT
        and window.tool_calls >= EXEC_MIN_EDIT_VOLUME
    ):
        # Low-touch acceptance at real volume, on a channel that has shown it
        # can fire: a gentle deduction, observed, never celebrated as perfect
        # (two-sided rework by decision).
        rework_penalty = NO_REVIEW_DEDUCTION
    score = _clamp(100 - fail_penalty - slow_penalty - rework_penalty)
    evidence = [
        f"{window.commands_failed} of {window.commands_run} commands "
        "exited nonzero",
    ]
    if rework_observed:
        evidence.append(
            f"you revised {window.rework_edits} of {window.tool_calls} tool results"
        )
    if window.commands_slow:
        evidence.append(f"{window.commands_slow} commands ran 30s or longer")
    return DimensionReading(
        "execution_quality", score, level_for(score), tuple(evidence)
    )


_READING_BUILDERS = (
    _deep_work,
    _working_pattern,
    _single_threading,
    _context_hygiene,
    _model_economy,
    _execution_quality,
)


def _readings(window: WindowInputs) -> tuple[DimensionReading, ...]:
    return tuple(build(window) for build in _READING_BUILDERS)


def dimension_scores(window: WindowInputs) -> dict[str, int]:
    """Dimension id -> score for one window (feeds the trajectory series)."""
    return {reading.dimension_id: reading.score for reading in _readings(window)}


def compute_performance(
    current: WindowInputs, prior: WindowInputs | None
) -> PerformanceProfile:
    """Pure and deterministic (INV-6): same counters, same profile, always."""
    readings = _readings(current)
    overall = sum(reading.score for reading in readings) // len(readings)
    delta: int | None = None
    if prior is not None and prior.active_days > 0:
        prior_scores = dimension_scores(prior)
        readings = tuple(
            DimensionReading(
                reading.dimension_id,
                reading.score,
                reading.level,
                reading.evidence,
                delta=reading.score - prior_scores[reading.dimension_id],
                neutral=reading.neutral,
            )
            for reading in readings
        )
        delta = overall - sum(prior_scores.values()) // len(prior_scores)
    return PerformanceProfile(
        window_days=current.window_days,
        active_days=current.active_days,
        overall=overall,
        delta_vs_prior=delta,
        readings=readings,
        attention=_attention_window(current),
        attention_recommendation=_attention_recommendation(current, prior),
    )


def _window_inputs(
    store: Store,
    end_day: date,
    window_days: int,
    behavior: dict[str, DayBehavior],
    attention: dict[str, AttentionDay],
) -> WindowInputs:
    """Aggregate one window from precomputed per-day behavior + usage rows."""
    start = end_day - timedelta(days=window_days - 1)
    days_in_window = [
        (start + timedelta(days=offset)).isoformat() for offset in range(window_days)
    ]
    window_behavior = [behavior[day] for day in days_in_window if day in behavior]
    window_attention = [attention[day] for day in days_in_window if day in attention]
    attention_window = summarize_attention(window_attention)

    quiet_values = [
        day.quiet_hours_events
        for day in window_behavior
        if day.quiet_hours_events is not None
    ]
    quiet_hours_events = sum(quiet_values)
    events_total = sum(day.events for day in window_behavior)

    cached = 0
    fresh_input = 0
    assistant_turns = 0
    premium_cost = 0
    priced_cost = 0
    unpriced_turns = 0
    tool_calls = 0
    rework_edits = 0
    commands_run = 0
    commands_failed = 0
    commands_slow = 0
    for day_iso in store.active_days(start.isoformat(), end_day.isoformat()):
        for _tool, model, values in store.day_usage_rows(day_iso):
            row = ContributionRow.from_values(values)
            assistant_turns += row.assistant_turns
            fresh_input += row.input_tokens
            cached += row.cached_tokens
            priced_cost += row.cost_micro_usd
            unpriced_turns += row.unpriced_turns
            tool_calls += row.tool_calls
            rework_edits += row.rework_edits
            commands_run += row.commands_run
            commands_failed += row.commands_failed
            commands_slow += row.commands_slow
            if model_family(model).value in PREMIUM_FAMILIES:
                premium_cost += row.cost_micro_usd

    breaks_completed = 0
    for day_iso in days_in_window:
        breaks_completed += block_counters(store, day_iso)["break-completed"]

    return WindowInputs(
        window_days=window_days,
        active_days=len(window_behavior),
        deep_block_days=sum(1 for day in window_behavior if day.deep_block),
        longest_block_min=max(
            (day.longest_block_min for day in window_behavior), default=0
        ),
        quiet_hours_activity_pct=(
            percent(quiet_hours_events, events_total) if quiet_values else None
        ),
        no_pause_days=sum(1 for day in window_behavior if day.no_pause),
        switch_count=sum(day.switches for day in window_behavior),
        parallel_days=sum(1 for day in window_behavior if day.parallel),
        burst_windows=sum(day.bursts for day in window_behavior),
        breaks_completed=breaks_completed,
        cached_tokens=cached,
        fresh_input_tokens=fresh_input,
        assistant_turns=assistant_turns,
        premium_cost_micro_usd=premium_cost,
        priced_cost_micro_usd=priced_cost,
        unpriced_turns=unpriced_turns,
        tool_calls=tool_calls,
        rework_edits=rework_edits,
        commands_run=commands_run,
        commands_failed=commands_failed,
        commands_slow=commands_slow,
        attention_active_days=attention_window.active_days,
        attention_human_events=attention_window.human_events,
        attention_known_events=attention_window.known_events,
        attention_known_coverage_pct=attention_window.known_coverage_pct,
        attention_switch_count=attention_window.cross_workstream_switches,
        attention_high_switch_days=attention_window.high_switch_days,
        attention_coordination_windows=attention_window.coordination_windows,
        attention_coordination_days=attention_window.coordination_days,
        attention_prompt_burst_windows=attention_window.prompt_burst_windows,
        attention_prompt_burst_days=attention_window.prompt_burst_days,
        attention_confident=attention_window.confident,
    )


def _attention_by_day(
    store: Store, start: date, end: date, schedule: ScheduleProfile
) -> dict[str, AttentionDay]:
    days = attention_by_day(
        store.marks_between(
            (start - timedelta(days=1)).isoformat(),
            (end + timedelta(days=1)).isoformat(),
        ),
        zone=schedule.zone,
    )
    first, last = start.isoformat(), end.isoformat()
    return {day: value for day, value in days.items() if first <= day <= last}


def window_inputs_from_store(
    store: Store,
    end_day: date,
    window_days: int = PERFORMANCE_WINDOW_DAYS,
    schedule: ScheduleProfile | None = None,
) -> WindowInputs:
    """Assemble one window's closed counters from persisted history only."""
    start = end_day - timedelta(days=window_days - 1)
    schedule = schedule or compatibility_utc_schedule()
    return _window_inputs(
        store,
        end_day,
        window_days,
        behavior_by_day(store, start, end_day, schedule),
        _attention_by_day(store, start, end_day, schedule),
    )


def gather_performance(
    store: Store, day: date, schedule: ScheduleProfile | None = None
) -> PerformanceProfile | None:
    """The report-time entry point: current window scored, prior window as
    the trend baseline. None until the window has any activity."""
    schedule = schedule or compatibility_utc_schedule()
    start = day - timedelta(days=2 * PERFORMANCE_WINDOW_DAYS - 1)
    behavior = behavior_by_day(store, start, day, schedule)
    attention = _attention_by_day(store, start, day, schedule)
    current = _window_inputs(store, day, PERFORMANCE_WINDOW_DAYS, behavior, attention)
    if current.active_days == 0:
        return None
    prior = _window_inputs(
        store, day - timedelta(days=PERFORMANCE_WINDOW_DAYS),
        PERFORMANCE_WINDOW_DAYS, behavior, attention,
    )
    return compute_performance(current, prior if prior.active_days > 0 else None)


def weekly_windows(
    store: Store,
    day: date,
    weeks: int = TRAJECTORY_WEEKS,
    schedule: ScheduleProfile | None = None,
) -> list[WindowInputs]:
    """One 7-day window per week, oldest first, the last ending at `day`.
    Feeds the trajectory sparklines and the brief's personal baselines."""
    schedule = schedule or compatibility_utc_schedule()
    start = day - timedelta(days=7 * weeks - 1)
    behavior = behavior_by_day(store, start, day, schedule)
    attention = _attention_by_day(store, start, day, schedule)
    windows: list[WindowInputs] = []
    for week in range(weeks - 1, -1, -1):
        end = day - timedelta(days=7 * week)
        windows.append(_window_inputs(store, end, 7, behavior, attention))
    return windows
