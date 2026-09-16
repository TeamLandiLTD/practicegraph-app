"""Advice that audits itself, in the person's own numbers.

The skill shelf could always tell you a skill was *taken* (the copy ledger).
Nothing measured what changed afterwards — so the shelf was making claims it
never checked. This closes that loop on the `advisor_ledger` pattern: at copy
time, record the measure the skill actually targets; weeks later, report the
same measure honestly **in both directions**.

**Why it has to report both directions.** Ferraro & Price (N≈100k households)
found technical advice alone was not statistically significant on its own —
roughly the 1% arm. Advice earns an effect when it is tied to *measured
behaviour* rather than offered generically, and the moment a tool only reports
its wins it stops being a measurement and becomes marketing. An unmoved measure
is reported as unmoved.

**Why the metric vocabulary is closed.** Only three measures here are both
derivable from counters we already hold and legible as an outcome of a specific
skill. A skill that answers none of them records nothing rather than being
matched to a number that does not test it — the same "withheld, not zero"
discipline the rest of the product holds.

Local-only forever (NFR-PRV-6): the ledger lives in the meta table and no
symbol here may appear in a wire payload. Determinism (INV-6): pure integer
arithmetic over counters, `today` always passed in.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from practicegraph.analysis.advisor_receipts import ReceiptsWindow
from practicegraph.report.format import percent

if TYPE_CHECKING:
    from practicegraph.store import Store

# Never on the wire — unioned into the field-name scan by the wire suite.
# Specific compounds, not the bare words: `emit` legitimately says "outcomes"
# in prose, and a scan that trips on prose gets weakened rather than obeyed.
WIRE_FORBIDDEN_TERMS: tuple[str, ...] = (
    "skill_ledger",
    "skill_outcome",
    "skill_target",
)

SKILL_LEDGER_KEY = "skill_ledger:v1"

# Windows, mirroring the advisor audit: a measure needs time to mean anything,
# an unmoved one is reported only after patience has been spent, and every
# entry retires so the surface can never accumulate history.
SKILL_AUDIT_MIN_DAYS = 14
SKILL_AUDIT_HOLD_DAYS = 28
SKILL_AUDIT_RETIRE_DAYS = 56
# Movement worth reporting, in percent of the recorded value. Below this the
# measure is called unchanged rather than dressed up as a trend.
SKILL_AUDIT_MIN_MOVE_PCT = 15

# The closed metric vocabulary: finding id -> the measure a skill answering
# that finding is actually trying to move.
METRIC_FOR_FINDING: dict[str, str] = {
    "context_bloat": "prompt_weight",
    "context_carried": "prompt_weight",
    "low_cache": "cache_share",
    "premium_heavy": "premium_share",
}

METRIC_COPY: dict[str, str] = {
    "prompt_weight": "prompt tokens per turn",
    "cache_share": "cache hit rate",
    "premium_share": "premium share of spend",
}

# Which way is "toward what the skill was for". Never framed as good or bad —
# it only decides which sentence is true.
METRIC_LOWER_IS_THE_TARGET: dict[str, bool] = {
    "prompt_weight": True,
    "cache_share": False,
    "premium_share": True,
}

# The line says what moved, in words (COPY_RULES rules 1-2, 5); the note
# beside it carries the caveat for the ⓘ - present, never crowding.
AUDIT_COPY: dict[str, str] = {
    "moved": (
        "Since you took “{title}” on {date}, your {metric} went "
        "from {then} to {now}."
    ),
    "against": (
        "Since you took “{title}” on {date}, your {metric} went "
        "from {then} to {now} - the other way."
    ),
    "unmoved": (
        "Since you took “{title}” on {date}, your {metric} is about where "
        "it was: {then} then, {now} now."
    ),
}

NOTE_COPY: dict[str, str] = {
    "moved": (
        "Measured on your own window; the timing is an association, "
        "not proof the skill caused it."
    ),
    "against": "One window is not a verdict on the skill; worth knowing either way.",
    "unmoved": "Nothing has moved yet.",
}


@dataclass(frozen=True, slots=True)
class SkillOutcome:
    """A rendered audit line plus the numbers behind it."""

    skill_id: str
    line: str
    note: str
    metric_id: str
    then: int
    now: int


def _prompt_weight(receipts: ReceiptsWindow) -> int:
    """Prompt tokens the models read per assistant turn, across the window."""
    turns = sum(f.assistant_turns for f in receipts.families)
    if turns <= 0:
        return 0
    prompt = sum(
        f.tokens.input + f.tokens.cached + f.tokens.cache_creation
        for f in receipts.families
    )
    return prompt // turns


def _cache_share(receipts: ReceiptsWindow) -> int:
    cached = sum(f.tokens.cached for f in receipts.families)
    prompt = sum(
        f.tokens.input + f.tokens.cached + f.tokens.cache_creation
        for f in receipts.families
    )
    return percent(cached, prompt)


def _premium_share(receipts: ReceiptsWindow) -> int:
    if receipts.total_cost_micro_usd <= 0:
        return 0
    premium = sum(f.cost_micro_usd for f in receipts.families if f.premium)
    return percent(premium, receipts.total_cost_micro_usd)


_MEASURES = {
    "prompt_weight": _prompt_weight,
    "cache_share": _cache_share,
    "premium_share": _premium_share,
}


def measure(metric_id: str, receipts: ReceiptsWindow) -> int:
    """Read one closed measure off a receipts window."""
    reader = _MEASURES.get(metric_id)
    return reader(receipts) if reader is not None else 0


def _render(metric_id: str, value: int) -> str:
    if metric_id == "prompt_weight":
        return _about_compact(value)
    return f"{value}%"


def _about_compact(value: int) -> str:
    """Two significant figures with a unit (COPY_RULES rule 5): 232,100 -> 230K."""
    if value < 1000:
        return str(value)
    for unit, size in (("B", 10**9), ("M", 10**6), ("K", 10**3)):
        if value >= size:
            scaled = value / size
            rounded = round(scaled, 1 - math.floor(math.log10(scaled)))
            text = str(int(rounded)) if rounded == int(rounded) else f"{rounded:.1f}"
            return f"{text}{unit}"
    return str(value)


def metric_for_skill(findings: tuple[str, ...]) -> str:
    """The measure a skill targets, or "" when none of its findings map to one.

    Findings are sorted so the choice never depends on registry order."""
    for finding in sorted(findings):
        metric = METRIC_FOR_FINDING.get(finding)
        if metric is not None:
            return metric
    return ""


def _read(store: Store) -> dict[str, object] | None:
    raw = store.meta_get(SKILL_LEDGER_KEY)
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict):
        return None
    for field, kind in (
        ("skill_id", str), ("title", str), ("day", str),
        ("metric_id", str), ("value", int),
    ):
        if not isinstance(entry.get(field), kind):
            return None
    if entry["metric_id"] not in METRIC_COPY:
        return None
    try:
        date.fromisoformat(str(entry["day"]))
    except ValueError:
        return None
    return entry


def record_skill_target(
    store: Store,
    skill_id: str,
    title: str,
    findings: tuple[str, ...],
    receipts: ReceiptsWindow,
    today: date,
) -> bool:
    """Copy-time write: start tracking what this skill is meant to move.

    One entry at a time. A skill copied while another is still inside its audit
    window does not displace it — two half-measured claims are worth less than
    one carried to a conclusion. Returns True when an entry was recorded."""
    metric_id = metric_for_skill(findings)
    if not metric_id:
        return False
    existing = _read(store)
    if existing is not None:
        recorded = date.fromisoformat(str(existing["day"]))
        if (today - recorded).days <= SKILL_AUDIT_RETIRE_DAYS:
            return False
    store.meta_set(
        SKILL_LEDGER_KEY,
        json.dumps(
            {
                "skill_id": skill_id,
                "title": title,
                "day": today.isoformat(),
                "metric_id": metric_id,
                "value": measure(metric_id, receipts),
            },
            sort_keys=True,
        ),
    )
    return True


def skill_audit(
    store: Store, receipts: ReceiptsWindow, today: date
) -> SkillOutcome | None:
    """Pure read: the audit line for the tracked skill, or None.

    Movement toward the target reports as soon as it is decisive and the entry
    is old enough to mean anything. Movement the other way reports on the same
    schedule — the loop is only worth having if it can say that. An unmoved
    measure waits for the hold window before it is called unmoved."""
    entry = _read(store)
    if entry is None:
        return None
    recorded = date.fromisoformat(str(entry["day"]))
    age = (today - recorded).days
    if age < SKILL_AUDIT_MIN_DAYS or age > SKILL_AUDIT_RETIRE_DAYS:
        return None
    metric_id = str(entry["metric_id"])
    then = int(entry["value"])  # type: ignore[call-overload]
    if then <= 0:
        return None
    now = measure(metric_id, receipts)
    move_pct = abs(now - then) * 100 // then
    lower_is_target = METRIC_LOWER_IS_THE_TARGET[metric_id]
    toward = (now < then) if lower_is_target else (now > then)
    if move_pct >= SKILL_AUDIT_MIN_MOVE_PCT:
        key = "moved" if toward else "against"
    elif age >= SKILL_AUDIT_HOLD_DAYS:
        key = "unmoved"
    else:
        return None
    return SkillOutcome(
        skill_id=str(entry["skill_id"]),
        line=AUDIT_COPY[key].format(
            title=str(entry["title"]),
            date=recorded.isoformat(),
            metric=METRIC_COPY[metric_id],
            then=_render(metric_id, then),
            now=_render(metric_id, now),
        ),
        note=NOTE_COPY[key],
        metric_id=metric_id,
        then=then,
        now=now,
    )
