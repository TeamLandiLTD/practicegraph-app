"""Deterministic insight analysis (FR-ANL-2..6): efficiency detectors,
work-type classification, maturity signals, model-fit advice, and the
suggestion lifecycle (FR-RPT-8).

Everything here is closed vocabulary over counters — rules over event
structure, never content (INV-6). All user-facing copy lives in the closed
catalogs below (NFR-QLT-3) and is scanned by the privacy test suites.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from practicegraph.analysis.aggregate import DailySnapshot
from practicegraph.report.format import percent
from practicegraph.store import MARK_COMPACTIONS, MarkRow, Store
from practicegraph.wire import PREMIUM_FAMILIES, model_family

# ---- efficiency detectors (FR-ANL-2) ----------------------------------------


class Severity(StrEnum):
    INFO = "info"
    OPPORTUNITY = "opportunity"
    ATTENTION = "attention"


# Named, configurable-constant thresholds (spec §0 preamble).
RETRY_STORM_MIN = 3
INTERRUPTION_CLUSTER_MIN = 3
LOW_CACHE_MIN_PROMPT_TOKENS = 20_000
LOW_CACHE_MAX_PCT = 30
CONTEXT_BLOAT_AVG_PROMPT_TOKENS = 60_000
PREMIUM_HEAVY_MIN_SHARE_PCT = 50
PREMIUM_MODEL_FIT_MIN_SHARE_PCT = 40
# P1 execution-quality detectors: high-volume days only, rates not raw counts.
#
# The step-error floor was 10, which was wrong once you know what the counter
# actually counts. `commands_run` increments on EVERY tool_result block of any
# tool (claude_code.py:497-504) — a failed Read, a Glob that matched nothing, a
# WebFetch timeout — so it is a step-error rate, not a shell exit-code rate.
# Measured base rate across real transcripts is ~2% (155 of 7,961). At a floor
# of 10, four unlucky file-not-founds in one short session cleared the gate; at
# 50 a 40% rate is a 20x deviation from base and means something. Raised
# 2026-08-13 after the copy was found to describe shell commands it never saw.
COMMAND_FRICTION_MIN_RUNS = 50
COMMAND_FRICTION_MIN_FAIL_PCT = 40
# P2 marathon gate: compactions in ONE session before the long-haul finding
# fires. Calibrated on real history 2026-07-05 — the largest per-session
# counts were claude 7,3,3,3,2,1… and codex 14,7,2,1,1…, so 3 flags only the
# genuine tail, never a session that merely compacted once or twice.
# Descriptive forever — INFO severity by decision.
MARATHON_MIN_COMPACTIONS = 3
# P4 approaching-quota gate: the day's latest provider rate-limit reading at
# or above this share of the current window. Informational forever (INFO by
# decision) and LOCAL-ONLY — rate-limit data never feeds an emit.
APPROACHING_QUOTA_PCT = 80

FINDING_IDS: tuple[str, ...] = (
    "retry_storm",
    "interruption_cluster",
    "low_cache_reuse",
    "context_bloat",
    "premium_heavy",
    "unpriced_models",
    "late_night_drift",  # P0: vs your own baseline; INFO forever (descriptive)
    # `rework_heavy` was retired 2026-08-13. Its ONLY signal was
    # toolUseResult.userModified (claude_code.py:508), an IDE-side flag that
    # measured false 1,087 times and true zero times across 60 real
    # transcripts — the same dead field already found under the reliance
    # facet, where it had been shipping as an 8-point penalty. A detector for
    # a signal the log does not emit is worse than no detector: it is a
    # promise the page silently never keeps. Published registries still tag
    # it, so the id stays ACCEPTED on the way in (skills.RETIRED_FINDINGS).
    "command_friction",  # P1: steps coming back as errors (any tool, not shell)
    "refire_after_failure",  # P2: fast human retries chase failed runs; INFO
    "marathon_session",  # P2: one session compacting repeatedly; INFO forever
    "approaching_quota",  # P4: provider window filling up; INFO, local-only
    "approvals_waved_through",  # long-run approvals without review; INFO forever
    "context_carried",  # W2.0: long sessions carry more per turn AND pay for it
)

# Closed copy catalog: id -> (title, body). Numbers render separately.
FINDING_COPY: dict[str, tuple[str, str]] = {
    "retry_storm": (
        "Retry storm",
        "Several requests were retried after errors. If this repeats, check tool "
        "connectivity or provider status before large sessions.",
    ),
    "interruption_cluster": (
        "Frequent interruptions",
        "Multiple requests were interrupted mid-turn. Smaller asks or tighter "
        "prompts often reduce the need to cut a response short.",
    ),
    "low_cache_reuse": (
        "Low cache reuse",
        "Most prompt tokens were billed fresh instead of served from cache. "
        "Keeping sessions going instead of restarting preserves the cache.",
    ),
    "context_bloat": (
        "Heavy context per turn",
        "Average prompt size per turn is very large. Pruning stale files from "
        "the working set lowers input tokens without losing quality.",
    ),
    "premium_heavy": (
        "Premium models carry most spend",
        "The majority of estimated spend went to premium models. Routine work "
        "often does just as well on a mid-tier model.",
    ),
    "unpriced_models": (
        "Unpriced models observed",
        "Some turns used models missing from the rate card, so their cost is "
        "not included in estimates. A rate-card update may be available.",
    ),
    "late_night_drift": (
        "Activity during your quiet hours",
        "A larger share of this week's activity landed during the quiet hours "
        "in your confirmed schedule than in your own recent baseline. This "
        "describes timing only; it is not a health judgment.",
    ),
    "command_friction": (
        "Steps coming back as errors",
        "A high share of today's steps came back as an error - a file that "
        "was not there, a search that found nothing, a command that failed. "
        "Worth reading the error before the next attempt; it usually names "
        "the fix.",
    ),
    "refire_after_failure": (
        "Fast retries after failed runs",
        "Many of today's replies landed within minutes of a failed run. A "
        "fixed next step for that moment - read the error in full, or "
        "hand-write the failing test first - usually breaks the loop sooner "
        "than another quick retry.",
    ),
    "marathon_session": (
        "Long-haul session",
        "One session today ran long enough to compact its context several "
        "times. Real stamina - and a natural moment to bank the progress: a "
        "fresh session with a short summary usually reasons more sharply "
        "than a heavily compacted one.",
    ),
    "approaching_quota": (
        "Provider quota running high",
        "Today's latest provider rate-limit reading sits above 80% of the "
        "current window. Heavier runs may queue or fail until it resets - "
        "worth knowing before starting a long task.",
    ),
    "context_carried": (
        "Long sessions cost more per turn",
        "Your longer sessions carried noticeably more prompt weight per turn "
        "than your shorter ones, and the cost followed. Every turn re-sends "
        "the conversation, so context kept past its usefulness is billed "
        "again on each one.",
    ),
    "approvals_waved_through": (
        "Short replies after longer runs",
        "Several longer agent runs were followed quickly by short human replies. "
        "Reply length and timing cannot show whether you reviewed the work. "
        "Use the handoff to check an outcome or a remaining uncertainty before continuing.",
    ),
}


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    severity: Severity
    metric: int  # the one number that triggered it (counter or percent)


def spend_by_family(snapshot: DailySnapshot) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in snapshot.rows:
        if row.cost_micro_usd > 0:
            family = model_family(row.model).value
            totals[family] = totals.get(family, 0) + row.cost_micro_usd
    return dict(sorted(totals.items()))


def detect(snapshot: DailySnapshot) -> list[Finding]:
    """Run every closed detector; deterministic catalog order."""
    findings: list[Finding] = []
    cached = sum(row.tokens.cached for row in snapshot.rows)
    fresh_input = sum(row.tokens.input for row in snapshot.rows)
    prompt_tokens = cached + fresh_input
    cached_pct = percent(cached, prompt_tokens)

    if snapshot.total_retries >= RETRY_STORM_MIN:
        findings.append(Finding("retry_storm", Severity.ATTENTION, snapshot.total_retries))
    if snapshot.total_interruptions >= INTERRUPTION_CLUSTER_MIN:
        findings.append(
            Finding("interruption_cluster", Severity.OPPORTUNITY,
                    snapshot.total_interruptions)
        )
    if prompt_tokens >= LOW_CACHE_MIN_PROMPT_TOKENS and cached_pct < LOW_CACHE_MAX_PCT:
        findings.append(Finding("low_cache_reuse", Severity.OPPORTUNITY, cached_pct))
    if snapshot.total_assistant_turns > 0:
        avg_prompt = prompt_tokens // snapshot.total_assistant_turns
        if avg_prompt >= CONTEXT_BLOAT_AVG_PROMPT_TOKENS:
            findings.append(Finding("context_bloat", Severity.OPPORTUNITY, avg_prompt))
    families = spend_by_family(snapshot)
    premium = sum(families.get(family, 0) for family in PREMIUM_FAMILIES)
    premium_pct = percent(premium, snapshot.total_cost_micro_usd)
    if premium_pct >= PREMIUM_HEAVY_MIN_SHARE_PCT and snapshot.total_cost_micro_usd > 0:
        findings.append(Finding("premium_heavy", Severity.OPPORTUNITY, premium_pct))
    if snapshot.total_unpriced_turns > 0:
        findings.append(
            Finding("unpriced_models", Severity.INFO, snapshot.total_unpriced_turns)
        )
    # P1 execution-quality detectors (contribution sums; rates over volume
    # gates so quiet days can never false-alarm).
    commands_run = sum(row.commands_run for row in snapshot.rows)
    commands_failed = sum(row.commands_failed for row in snapshot.rows)
    fail_pct = percent(commands_failed, commands_run)
    if (
        commands_run >= COMMAND_FRICTION_MIN_RUNS
        and fail_pct >= COMMAND_FRICTION_MIN_FAIL_PCT
    ):
        findings.append(Finding("command_friction", Severity.OPPORTUNITY, fail_pct))
    return findings


def marathon_finding(marks: list[MarkRow]) -> Finding | None:
    """P2 marathon gate over the day's activity timeline: sum compactions per
    session and flag the peak when one session compacted its context
    ``MARATHON_MIN_COMPACTIONS``+ times. INFO forever by decision — the copy
    is descriptive and autonomy-supportive, never a judgment (FR-FOC-8)."""
    by_session: dict[str, int] = {}
    for mark in marks:
        # MARK_* indexed reads (store.py) — the append-only contract makes
        # positional full-tuple unpacks a trap when the row grows.
        compactions = mark[MARK_COMPACTIONS] if len(mark) > MARK_COMPACTIONS else 0
        by_session[mark[0]] = by_session.get(mark[0], 0) + compactions
    peak = max(by_session.values(), default=0)
    if peak < MARATHON_MIN_COMPACTIONS:
        return None
    return Finding("marathon_session", Severity.INFO, peak)


# ---- work-type classification (FR-ANL-3) -------------------------------------


class WorkType(StrEnum):
    """Closed taxonomy, classified per session from event structure only
    (v1 structural heuristics — never content)."""

    BUILD = "build"
    INVESTIGATE = "investigate"
    CONVERSE = "converse"
    UNKNOWN = "unknown"


WORK_TYPE_LABELS: dict[str, str] = {
    "build": "Build",
    "investigate": "Investigate",
    "converse": "Converse",
    "unknown": "Unknown",
}

INVESTIGATE_MIN_DISRUPTIONS = 2
# v2 (heuristic upgrade, still structural): with per-API-call events, nearly
# every agentic turn carries a tool call, so "any tool call = build" saturated
# at 100% Build. The discriminating signal is *who is driving*: a session the
# human steers turn-by-turn (frequent human replies after untooled answers)
# is investigation; a delegated run (tooled turns, few human interjections)
# is build. Human replies are user turns that follow an untooled assistant
# turn in the same session — the same structural rule the reflex metric uses.
INVESTIGATE_HUMAN_SHARE_PCT = 30  # human replies >= 30% of assistant turns


def work_type_mix(marks: list[MarkRow]) -> dict[str, int]:
    """Sessions per work type for one day, from the activity timeline."""
    per_session: dict[str, list[int]] = {}
    previous: dict[str, tuple[str, int]] = {}  # session -> (kind, tool_calls)
    for session_key, _ts, kind, tool_calls, retries, interruptions, *_rest in marks:
        stats = per_session.setdefault(session_key, [0, 0, 0, 0])
        if kind == "assistant_turn":
            stats[0] += 1
        else:
            prior = previous.get(session_key)
            if prior is not None and prior[0] == "assistant_turn" and prior[1] == 0:
                stats[3] += 1  # a human reply to a plain text answer
        stats[1] += tool_calls
        stats[2] += retries + interruptions
        previous[session_key] = (kind, tool_calls)
    mix = dict.fromkeys([w.value for w in WorkType], 0)
    for assistant_turns, tool_calls, disruptions, human_replies in (
        per_session.values()
    ):
        if assistant_turns == 0:
            work_type = WorkType.UNKNOWN
        elif tool_calls == 0:
            work_type = WorkType.CONVERSE
        elif (
            disruptions >= INVESTIGATE_MIN_DISRUPTIONS
            or human_replies * 100 >= assistant_turns * INVESTIGATE_HUMAN_SHARE_PCT
        ):
            work_type = WorkType.INVESTIGATE
        else:
            work_type = WorkType.BUILD
        mix[work_type.value] += 1
    return mix


# ---- maturity signals (FR-ANL-5) ---------------------------------------------


class MaturityLevel(StrEnum):
    NOT_YET = "not_yet"
    EMERGING = "emerging"
    DEVELOPING = "developing"
    LEADING = "leading"


MATURITY_SIGNAL_IDS: tuple[str, ...] = (
    "cache_reuse",
    "tool_usage",
    "multi_tool",
    "consistency",
)

MATURITY_LABELS: dict[str, str] = {
    "cache_reuse": "Cache reuse",
    "tool_usage": "Tool usage",
    "multi_tool": "Multi-tool breadth",
    "consistency": "Consistency",
}


def _level(value: int, emerging: int, developing: int, leading: int) -> MaturityLevel:
    if value >= leading:
        return MaturityLevel.LEADING
    if value >= developing:
        return MaturityLevel.DEVELOPING
    if value >= emerging:
        return MaturityLevel.EMERGING
    return MaturityLevel.NOT_YET


def maturity_signals(
    snapshot: DailySnapshot, active_days_last_7: int
) -> list[tuple[str, MaturityLevel]]:
    cached = sum(row.tokens.cached for row in snapshot.rows)
    prompt = cached + sum(row.tokens.input for row in snapshot.rows)
    cache_pct = percent(cached, prompt)
    tool_pct = percent(snapshot.total_tool_calls, max(1, snapshot.total_assistant_turns))
    tools = len({row.tool for row in snapshot.rows if row.assistant_turns > 0})
    return [
        ("cache_reuse", _level(cache_pct, 1, 40, 70)),
        ("tool_usage", _level(tool_pct, 1, 20, 50)),
        # Levels are (emerging, established, leading) thresholds on the count
        # of distinct tools seen. The Tool enum has exactly two members
        # (events.py:16-20), so LEADING at >=3 was unreachable by anyone,
        # forever — a rung the product drew and nobody could stand on. Two
        # tools IS the ceiling here; leading means using both.
        ("multi_tool", _level(tools, 1, 1, 2)),
        ("consistency", _level(active_days_last_7, 1, 3, 5)),
    ]


# ---- suggestions (FR-ANL-4 model fit + FR-RPT-8 lifecycle) --------------------

SUGGESTION_IDS: tuple[str, ...] = (
    "route-routine-to-midtier",
    "keep-sessions-warm",
    "trim-carried-context",
    "update-rate-card",
)

# Closed copy catalog: id -> (title, body).
SUGGESTION_COPY: dict[str, tuple[str, str]] = {
    "route-routine-to-midtier": (
        "Review premium-model use",
        "Premium models carried a large share of estimated spend. Routing routine "
        "prompts to a mid-tier model by default keeps premium where it earns its "
        "cost.",
    ),
    "keep-sessions-warm": (
        "Keep sessions going to reuse cache",
        "A low share of prompt tokens came from cache. Continuing an existing "
        "session instead of starting fresh lets cached context do the work.",
    ),
    "trim-carried-context": (
        "Trim carried context",
        "Average prompt size per turn is very large. Pruning stale files from the "
        "working set lowers input tokens each turn.",
    ),
    "update-rate-card": (
        "Refresh the rate card",
        "Some models were not in the local rate card, so estimates are incomplete. "
        "Pulling the latest catalog fixes the gap.",
    ),
}

# Suggestion states (FR-RPT-8).
STATE_DISMISSED = "dismissed"
STATE_NEVER = "never_suggest"


@dataclass(frozen=True, slots=True)
class Suggestion:
    suggestion_id: str
    metric: int


def derive_suggestions(snapshot: DailySnapshot) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    families = spend_by_family(snapshot)
    premium = sum(families.get(family, 0) for family in PREMIUM_FAMILIES)
    premium_pct = percent(premium, snapshot.total_cost_micro_usd)
    if premium_pct >= PREMIUM_MODEL_FIT_MIN_SHARE_PCT:
        suggestions.append(Suggestion("route-routine-to-midtier", premium_pct))
    findings = {finding.finding_id: finding for finding in detect(snapshot)}
    if "low_cache_reuse" in findings:
        suggestions.append(
            Suggestion("keep-sessions-warm", findings["low_cache_reuse"].metric)
        )
    if "context_bloat" in findings:
        suggestions.append(
            Suggestion("trim-carried-context", findings["context_bloat"].metric)
        )
    if "unpriced_models" in findings:
        suggestions.append(
            Suggestion("update-rate-card", findings["unpriced_models"].metric)
        )
    return suggestions


def visible_suggestions(store: Store, suggestions: list[Suggestion]) -> list[Suggestion]:
    """One dismissal-filtered list feeds every surface (FR-RPT-8)."""
    states = store.suggestion_states()
    return [s for s in suggestions if states.get(s.suggestion_id) is None]


def dismiss_suggestion(store: Store, suggestion_id: str, never: bool, now: datetime) -> bool:
    if suggestion_id not in SUGGESTION_IDS:
        return False
    store.suggestion_set_state(
        suggestion_id, STATE_NEVER if never else STATE_DISMISSED, now
    )
    return True


# ---- spend pace (FR-ANL-6) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpendPace:
    month_to_date_micro_usd: int
    projected_micro_usd: int
    budget_micro_usd: int

    @property
    def over_budget(self) -> bool:
        return self.projected_micro_usd > self.budget_micro_usd


def spend_pace(store: Store, today_iso: str, budget_micro_usd: int) -> SpendPace:
    """Endpoint-side pace against a configured monthly budget."""
    month_start = today_iso[:8] + "01"
    rows = store.spend_series(month_start, today_iso)
    month_to_date = sum(row[1] for row in rows)
    elapsed_days = int(today_iso[8:10])
    days_in_month = 31  # projection bound: conservative fixed-month constant
    projected = month_to_date * days_in_month // max(1, elapsed_days)
    return SpendPace(month_to_date, projected, budget_micro_usd)
