"""Local interaction diagnostics from recorded events and approval moments.

These ratios describe log structure. They do not measure comprehension,
independent thought, attention, or output quality. Tool implementations and
interaction styles affect the denominators. The current UI folds these details
and excludes the legacy engagement facet; its timing counters remain internal.
"""

from __future__ import annotations

from dataclasses import dataclass

from practicegraph.analysis.focus import FocusMetrics
from practicegraph.report.format import compact, count, duration_hm, percent

# Local-only vocabulary (test_wire unions every module's list into the scan).
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "reliance",
    "dictation",
    "dialogue",
    "handoff",
    "supervision",
)

# Sample floors. Below any of these the facet is dropped rather than guessed —
# "withheld, not zero" applies to judgment as much as to money.
RELIANCE_MIN_MOMENTS = 40  # judgment moments before a share means anything
RELIANCE_MIN_HANDOFFS = 10  # approval moments before grain means anything
RELIANCE_MIN_TOOL_CALLS = 200  # tool calls before a revision rate is readable
# The rework channel only exists for some setups (see `_shaping`). Below this
# many observed revisions the window proves nothing about review, so the facet
# is withheld rather than rendered as a confident zero.
RELIANCE_MIN_REWORK_OBSERVED = 5

# A facet needs at least this many of the three to render at all; one number in
# isolation is a factoid, not a shape.
RELIANCE_MIN_FACETS = 2

RELIANCE_COPY: dict[str, str] = {
    "eyebrow": "How you worked with it",
    "intro": "How the work was shared between you and the agent, from your own counters. Nothing "
    "here reads what was said.",
    "shape-both": "{agent_pct}% of recorded activity marks were noninteractive. Prompt-to-response "
    "intervals totaled {waiting} this month; they do not measure your time away from "
    "other work.",
    "shape-agent": "{agent_pct}% of recorded activity marks were noninteractive. This is an event "
    "share, not a share of elapsed time.",
    "shape-attended": "Every recorded activity mark was interactive. Prompt-to-response intervals "
    "totaled {waiting}; they do not measure attention.",
    "shape-attended-nowait": "Every recorded activity mark was interactive.",
    "grain-label": "Events per recorded handoff",
    "grain-line": "About {turns} activity events per recorded handoff, across {handoffs} handoffs.",
    "grain-measures": "Total activity events divided by recorded approval moments. "
    "This is an event ratio, not a count of completed tasks.",
    "grain-why": "Different tools and interaction styles generate different numbers of events.",
    "grain-paired": "Recorded context averaged {weight} prompt tokens per assistant turn.",
    "engagement-label": "Recorded response timing",
    "engagement-line": "{pct}% of {moments} recorded response moments fell outside the quick-reply "
    "thresholds.",
    "engagement-measures": "The share outside the configured timing and short-reply thresholds.",
    "engagement-why": "Timing does not establish what was read, understood, or reviewed.",
    "engagement-paired": "{refires} replies went out within five minutes of a failed run.",
    "shaping-label": "Recorded revision flags",
    "shaping-line": "Revision flags amounted to {pct}% of {calls} recorded tool calls.",
    "shaping-line-few": "There were {edits} revision flags across {calls} tool calls, under 1%.",
    "shaping-measures": "Observed modification or rejection flags divided by all tool calls. These "
    "channels vary by tool.",
    "shaping-why": "The logs do not capture all human editing or review. This ratio is not a "
    "measure of quality.",
}


@dataclass(frozen=True, slots=True)
class RelianceFacet:
    """One neutral fact about the shape, with the two-sided reading attached.

    `delta` is the movement against the person's OWN prior window of the same
    length — never against anyone else. It carries no tone: a rising handoff
    grain is not a fall from grace, and the arrow is the whole point, because
    a level is nearly meaningless while a change is not. None when there is no
    comparable prior window.

    `paired` is one co-observed number from the same window that the facet
    structurally moves with. Stated as an association the person can check,
    never as a cause — a facet that only ever says "both are valid" gives a
    reader nothing to do with it."""

    facet_id: str  # "grain" | "engagement" | "shaping"
    label: str
    line: str
    measures: str
    why: str
    delta: int | None = None
    paired: str = ""


@dataclass(frozen=True, slots=True)
class RelianceReading:
    """The composed surface: the job-shape headline plus the facets that
    cleared their gates. `available` is False when too little is readable."""

    available: bool
    headline: str
    facets: tuple[RelianceFacet, ...]
    window_days: int


_UNAVAILABLE = RelianceReading(available=False, headline="", facets=(), window_days=0)


# --- raw measures -----------------------------------------------------------
# Each returns the bare number or None below its gate, so the SAME arithmetic
# serves the current window and the prior one. A delta computed any other way
# would eventually drift from the level it claims to be a delta of.


def _grain_value(daily: dict[str, FocusMetrics]) -> int | None:
    handoffs = sum(m.approval_moments for m in daily.values())
    turns = sum(m.event_count for m in daily.values())
    if handoffs < RELIANCE_MIN_HANDOFFS or turns <= 0:
        return None
    return turns // handoffs


def _engagement_value(daily: dict[str, FocusMetrics]) -> int | None:
    moments = sum(m.assistant_followups + m.approval_moments for m in daily.values())
    if moments < RELIANCE_MIN_MOMENTS:
        return None
    hasty = min(sum(m.reflex_replies + m.waved_through for m in daily.values()), moments)
    return 100 - (hasty * 100 // moments)


def _shaping_value(rework_edits: int, tool_calls: int) -> int | None:
    if tool_calls < RELIANCE_MIN_TOOL_CALLS:
        return None
    if rework_edits < RELIANCE_MIN_REWORK_OBSERVED:
        return None
    return percent(rework_edits, tool_calls)


def _delta(now: int | None, before: int | None) -> int | None:
    """Movement against the person's own prior window, or None when either
    side is unreadable. Zero is a real answer and is kept as 0, not None -
    "unchanged" is information."""
    if now is None or before is None:
        return None
    return now - before


# --- facets -----------------------------------------------------------------


def _grain(
    daily: dict[str, FocusMetrics],
    prior: dict[str, FocusMetrics],
    prompt_tokens_per_turn: int,
) -> RelianceFacet | None:
    """Assistant turns per approval moment - the delegation grain. Field
    evidence (arXiv 2512.14012: 13 observed + 99 surveyed experienced devs)
    puts the professional norm at small verified increments, so neither tail
    is scolded here; the number is simply made visible.

    The paired number is prompt weight per turn, because the two are linked by
    construction rather than by correlation-hunting: work that runs longer
    between checkpoints accumulates more context before anything is pruned,
    and the person pays for that on every turn."""
    turns_per = _grain_value(daily)
    if turns_per is None:
        return None
    handoffs = sum(m.approval_moments for m in daily.values())
    return RelianceFacet(
        facet_id="grain",
        label=RELIANCE_COPY["grain-label"],
        line=RELIANCE_COPY["grain-line"].format(turns=count(turns_per), handoffs=count(handoffs)),
        measures=RELIANCE_COPY["grain-measures"],
        why=RELIANCE_COPY["grain-why"],
        delta=_delta(turns_per, _grain_value(prior)),
        paired=(
            RELIANCE_COPY["grain-paired"].format(weight=compact(prompt_tokens_per_turn))
            if prompt_tokens_per_turn > 0
            else ""
        ),
    )


def _engagement(
    daily: dict[str, FocusMetrics], prior: dict[str, FocusMetrics]
) -> RelianceFacet | None:
    """The deliberate share at judgment moments - the same arithmetic the
    conditioning readout uses for composure, read here as collaboration shape
    rather than pacing. Hasty is capped at the moment count so a noisy day can
    never drive the share negative.

    The paired number is the re-fire count: replies that went out within five
    minutes of a failed run. It is the same question at the sharpest moment -
    when it broke, did you read it or push the button again."""
    considered = _engagement_value(daily)
    if considered is None:
        return None
    moments = sum(m.assistant_followups + m.approval_moments for m in daily.values())
    refires = sum(m.refire_replies for m in daily.values())
    return RelianceFacet(
        facet_id="engagement",
        label=RELIANCE_COPY["engagement-label"],
        line=RELIANCE_COPY["engagement-line"].format(pct=considered, moments=count(moments)),
        measures=RELIANCE_COPY["engagement-measures"],
        why=RELIANCE_COPY["engagement-why"],
        delta=_delta(considered, _engagement_value(prior)),
        paired=(
            RELIANCE_COPY["engagement-paired"].format(refires=count(refires)) if refires > 0 else ""
        ),
    )


def _shaping(
    rework_edits: int, tool_calls: int, prior_rework: int, prior_calls: int
) -> RelianceFacet | None:
    """Hand-revision of what came back. Two-sided by design: the execution
    dimension already treats low rework at real volume as its own signal, so
    this facet reports the rate and declines to grade it.

    Withheld unless the rework channel has shown it can fire in this window.
    It arrives from Claude Code's `toolUseResult.userModified` - set only by
    the IDE-extension diff flow - and Codex's rejected `patch_apply_end`. On
    113 days of real CLI history the channel produced 4 events in 47,621 tool
    calls, so rendering "0% revised" there would state as a finding something
    the logs simply cannot see. Unobserved is not zero (INV: withheld, not
    zero); for an IDE user the same facet is real and renders normally."""
    revised = _shaping_value(rework_edits, tool_calls)
    if revised is None:
        return None
    line = (
        RELIANCE_COPY["shaping-line-few"].format(edits=count(rework_edits), calls=count(tool_calls))
        if revised == 0
        else RELIANCE_COPY["shaping-line"].format(pct=revised, calls=count(tool_calls))
    )
    return RelianceFacet(
        facet_id="shaping",
        label=RELIANCE_COPY["shaping-label"],
        line=line,
        measures=RELIANCE_COPY["shaping-measures"],
        why=RELIANCE_COPY["shaping-why"],
        delta=_delta(revised, _shaping_value(prior_rework, prior_calls)),
    )


def compose_reliance(
    daily: dict[str, FocusMetrics],
    marks_total: int,
    marks_interactive: int,
    waiting_minutes: int,
    rework_edits: int,
    tool_calls: int,
    window_days: int,
    prior_daily: dict[str, FocusMetrics] | None = None,
    prior_rework_edits: int = 0,
    prior_tool_calls: int = 0,
    prompt_tokens_per_turn: int = 0,
) -> RelianceReading:
    """The collaboration-shape reading for one window.

    `daily` is per-day focus metrics over the window (already interactive-gated
    like every behavioural surface). `marks_total`/`marks_interactive` give the
    authorship split — the difference is agents working unattended.

    `prior_daily` is the window of the same length immediately before this
    one. It is what turns a level into a trend, and the trend is the readable
    part: 112 turns per handoff says little alone, while "112, up 22 from your
    own prior four weeks" says the habit moved.

    Unavailable unless at least RELIANCE_MIN_FACETS facets clear their gates:
    a single number is a factoid, and the reading only means something as a
    shape.
    """
    prior = prior_daily or {}
    facets = tuple(
        facet
        for facet in (
            _grain(daily, prior, prompt_tokens_per_turn),
            _engagement(daily, prior),
            _shaping(rework_edits, tool_calls, prior_rework_edits, prior_tool_calls),
        )
        if facet is not None
    )
    if len(facets) < RELIANCE_MIN_FACETS:
        return _UNAVAILABLE
    # The headline frames the facets; it never gates them. An all-attended
    # window (no unattended agent runs at all) is a real shape too, and early
    # history is full of them — withholding the whole reading there would hide
    # a readable shape behind a missing frame.
    agent_pct = percent(marks_total - marks_interactive, marks_total)
    waiting = duration_hm(waiting_minutes)
    if agent_pct <= 0:
        headline = (
            RELIANCE_COPY["shape-attended"].format(waiting=waiting)
            if waiting_minutes > 0
            else RELIANCE_COPY["shape-attended-nowait"]
        )
    elif waiting_minutes > 0:
        headline = RELIANCE_COPY["shape-both"].format(agent_pct=agent_pct, waiting=waiting)
    else:
        headline = RELIANCE_COPY["shape-agent"].format(agent_pct=agent_pct)
    return RelianceReading(
        available=True,
        headline=headline,
        facets=facets,
        window_days=window_days,
    )
