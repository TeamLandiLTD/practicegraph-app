"""Skill registry: a curated catalog of reusable prompts/workflows an org hosts
and the endpoint matches to the person's own recent work.

Like the field-guide practices, a skill is a *catalog artifact* (FR-EMT-4): the
bundled set below always exists, and an org can serve a richer catalog from the
admin backend. The endpoint pulls it through the existing catalog channel and
NEVER scrapes the open web (NFR-SEC-5). Curation is a server-side concern.

Privacy posture (the load-bearing decision): recommendation happens ENTIRELY on
the endpoint. The server hosts the whole catalog, tagged with the usage signals
that make each skill relevant; the machine matches those tags against its own
local picture — the work-type mix and the tools it actually saw — and never
sends a work profile up. This is the practices `relevant_practices()` model,
scaled to a registry: the passive server stays aggregate-only (INV-2), and no
behavioral data crosses the wire (NFR-PRV-6).

A skill is display copy plus a reusable prompt/workflow the user copies into
their agent — NOT executable code the endpoint runs. So the artifact validator
enforces the same rules as every catalog: closed schema, length caps, the
FR-FOC-8 forbidden lexicon, and the no-leak patterns. A server cannot push a
skill the product itself would not be allowed to write, and cannot push runnable
code at all.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, TypeGuard

from practicegraph.analysis.insights import FINDING_IDS, WorkType
from practicegraph.analysis.performance import DIMENSION_IDS
from practicegraph.privacy import leak_findings, lexicon_violations

if TYPE_CHECKING:
    from practicegraph.store import Store

SKILLS_VERSION = "skills-bundled-2026-07-20"

# A skill applies to one or more work-types and one or more tools. "any" is the
# wildcard on each axis. Work-types mirror the WorkType enum (minus "unknown",
# which is not a thing you target a skill at).
SKILL_WORK_TYPES: tuple[str, ...] = (
    "any",
    WorkType.BUILD.value,
    WorkType.INVESTIGATE.value,
    WorkType.CONVERSE.value,
)
SKILL_TOOLS: tuple[str, ...] = ("any", "claude_code", "codex")

MAX_SKILLS = 200  # a registry, not a handful — but bounded (artifact size)
MAX_TITLE_LEN = 90
MAX_SUMMARY_LEN = 200  # the one-line "what it's for"
MAX_PROMPT_LEN = 2000  # the copyable prompt/workflow body

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Skill:
    """One registry entry: what it is, when it is relevant, and the reusable
    prompt/workflow a person copies into Claude Code or Codex. `work_types` and
    `tools` are the local-match tags; `role` is an optional free label shown for
    grouping (never inferred from logs — purely descriptive)."""

    skill_id: str
    title: str
    summary: str  # one line: what this skill is for
    prompt: str  # the reusable prompt / step-by-step workflow to copy
    work_types: tuple[str, ...]  # which work-types this serves ("any" wildcard)
    tools: tuple[str, ...]  # which tools this applies to ("any" wildcard)
    role: str | None = None  # optional descriptive grouping label
    # Richer local-match tags (all optional, all matched on the endpoint):
    # findings this skill answers (a skill for command_friction surfaces when
    # that finding fired today) and maturity dimensions it strengthens (a skill
    # tagged model_economy surfaces when that dimension is weak). These sharpen
    # relevance beyond work-type without any model — still integer-weighted and
    # byte-deterministic. Empty = not gated on that axis.
    findings: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()


# Bundled starter registry. Small on purpose — the org backend carries the full
# catalog; this is the offline/first-run set. Copy is fixed and lexicon-scanned.
BUNDLED_SKILLS: tuple[Skill, ...] = (
    Skill(
        "scope-before-build",
        "Scope a change before writing code",
        "Turn a vague build task into a short written plan you approve first.",
        "Before writing any code, read the relevant files and produce a short "
        "plan: the files you will change, the approach, and the one risk most "
        "likely to bite. Wait for my approval on the plan before editing. Keep "
        "it to a handful of bullet points - I want to steer the approach, not "
        "read an essay.",
        (WorkType.BUILD.value,),
        ("any",),
        role="engineering",
    ),
    Skill(
        "read-the-error-first",
        "Start a fix from the error text",
        "Debug from the actual error instead of guessing at a retry.",
        "A run just failed. Before trying anything, quote the exact error text "
        "and tell me what it literally says is wrong - the file, the line, the "
        "condition. Then propose the single smallest change that addresses that "
        "specific error, and why you expect it to work. No broad rewrites.",
        (WorkType.INVESTIGATE.value,),
        ("any",),
        role="engineering",
        findings=("command_friction", "refire_after_failure"),
        dimensions=("execution_quality",),
    ),
    Skill(
        "explain-it-back",
        "Have the agent explain its own change",
        "Keep yourself in the loop by making the plan legible before you accept.",
        "Before I approve this, explain the change back to me in plain terms: "
        "what it does, what it deliberately does not touch, and how I could tell "
        "in one check whether it worked. If any part of it is a guess rather "
        "than something you verified, say so.",
        ("any",),
        ("any",),
        role="review",
        findings=("approvals_waved_through",),
    ),
    Skill(
        "route-routine-to-midtier",
        "Send routine work to a cheaper model",
        "Cut spend by matching model tier to task difficulty.",
        "For this session, treat model choice as a cost decision: use a smaller "
        "/ cheaper model for routine edits, boilerplate, and mechanical "
        "refactors, and reserve the premium model for genuinely hard reasoning. "
        "Tell me when you switch up a tier and why the task warranted it.",
        (WorkType.BUILD.value,),
        ("any",),
        role="cost",
        findings=("premium_heavy",),
        dimensions=("model_economy",),
    ),
    Skill(
        "keep-context-lean",
        "Keep the working context focused",
        "Reduce carried tokens by pruning context deliberately between tasks.",
        "As we move between tasks, keep the working context lean: when we finish "
        "a piece of work, summarise what matters in a few lines and let the rest "
        "go rather than carrying the whole history forward. Flag when the "
        "context is getting large enough that a fresh session would be cleaner.",
        ("any",),
        ("any",),
        role="context",
        findings=("context_bloat", "low_cache_reuse", "marathon_session"),
        dimensions=("context_hygiene",),
    ),
    Skill(
        "one-thread-at-a-time",
        "Finish one thread before opening the next",
        "Reduce switching cost by closing a task before starting another.",
        "Work one task to a stopping point before opening the next. If you hit "
        "something that needs a separate change, note it as a follow-up and keep "
        "going on the current thread rather than switching. Tell me at each "
        "natural stopping point so I can decide what is next.",
        ("any",),
        ("any",),
        role="focus",
        findings=("interruption_cluster",),
        dimensions=("single_threading",),
    ),
    # Divergence set: the first suggestion tends to set the shape of the
    # solution, so these three keep the person generating and comparing rather
    # than accepting. Deliberately practices, not finding-answers (except the
    # sparring partner, which answers waved-through approvals directly).
    Skill(
        "your-move-first",
        "Say how you would do it before the agent does",
        "Keep the first suggestion from setting the shape of your solution.",
        "Before you propose an approach, ask me how I would do it and wait "
        "for my answer. I will give you a line or two. Then show me yours, "
        "and say plainly where the two differ, what mine gets right, and what "
        "it misses. If you think yours is better, argue for it - but do not "
        "quietly drop mine. I want to compare two approaches, not be handed "
        "one.",
        ("any",),
        ("any",),
        role="design",
    ),
    Skill(
        "three-roads",
        "Ask for three different approaches, not one",
        "Widen the options before you narrow them - use the agent to open the field.",
        "Give me three genuinely different ways to approach this - not three "
        "variations of the same idea. For each one: the core trade-off, what "
        "it would cost to live with, and the kind of project it suits. Do not "
        "recommend one until I ask for a recommendation. If two of them "
        "collapse into the same approach, say so and replace one with a real "
        "alternative.",
        (WorkType.BUILD.value, WorkType.INVESTIGATE.value),
        ("any",),
        role="design",
    ),
    Skill(
        "sparring-partner",
        "Have the agent argue against your design",
        "Stay the author of the idea and use the agent to find the holes in it.",
        "I am going to describe how I plan to build this. Your job is not to "
        "write it or to improve it - it is to attack it. Give me the "
        "strongest case against my approach: where it breaks under load, what "
        "it makes expensive to change later, and the assumption it rests on "
        "that I have not checked. Restate my version first so I know you "
        "understood it, then take it apart.",
        ("any",),
        ("any",),
        role="review",
        findings=("approvals_waved_through",),
        dimensions=("execution_quality",),
    ),
    Skill(
        "converse-to-spec",
        "Turn a discussion into a written spec",
        "Capture a design conversation as a durable spec before building.",
        "We have been talking through an approach. Write it up as a short spec I "
        "can keep: the goal, the decisions we made and why, what is explicitly "
        "out of scope, and the open questions. Plain prose, no code yet - I want "
        "the shared understanding on the record first.",
        (WorkType.CONVERSE.value,),
        ("any",),
        role="planning",
    ),
)


def skills_artifact() -> dict[str, object]:
    """The bundled skills as a versioned catalog artifact (server side)."""
    return {
        "skills_version": SKILLS_VERSION,
        "entries": [
            {
                "id": skill.skill_id,
                "title": skill.title,
                "summary": skill.summary,
                "prompt": skill.prompt,
                "work_types": list(skill.work_types),
                "tools": list(skill.tools),
                **({"role": skill.role} if skill.role else {}),
                **({"findings": list(skill.findings)} if skill.findings else {}),
                **({"dimensions": list(skill.dimensions)} if skill.dimensions else {}),
            }
            for skill in BUNDLED_SKILLS
        ],
    }


def parse_skills_artifact(raw: object) -> tuple[Skill, ...] | None:
    """Strict validation: closed schema, caps, closed tag vocabularies, and the
    lexicon + leak scans over every copy field. None on any deviation — a server
    cannot push a skill the product could not write, nor any runnable payload
    (there is no code field; `prompt` is scanned display text)."""
    if not isinstance(raw, dict) or set(raw.keys()) != {"skills_version", "entries"}:
        return None
    version = raw["skills_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    entries = raw["entries"]
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_SKILLS:
        return None
    skills: list[Skill] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        if not set(entry.keys()) <= {
            "id", "title", "summary", "prompt", "work_types", "tools", "role",
            "findings", "dimensions",
        }:
            return None
        skill_id = entry.get("id")
        title = entry.get("title")
        summary = entry.get("summary")
        prompt = entry.get("prompt")
        work_types = entry.get("work_types")
        tools = entry.get("tools")
        role = entry.get("role")
        findings = entry.get("findings", [])
        dimensions = entry.get("dimensions", [])
        if (
            not isinstance(skill_id, str)
            or not _ID_RE.match(skill_id)
            or skill_id in seen_ids
            or not isinstance(title, str)
            or not 0 < len(title) <= MAX_TITLE_LEN
            or not isinstance(summary, str)
            or not 0 < len(summary) <= MAX_SUMMARY_LEN
            or not isinstance(prompt, str)
            or not 0 < len(prompt) <= MAX_PROMPT_LEN
        ):
            return None
        if not _valid_tags(work_types, SKILL_WORK_TYPES):
            return None
        if not _valid_tags(tools, SKILL_TOOLS):
            return None
        # findings/dimensions are OPTIONAL: absent or [] is fine, but any
        # present value must be a distinct member of the closed id vocabulary.
        if not _valid_optional_tags(findings, _MATCHABLE_FINDING_TAGS):
            return None
        if not _valid_optional_tags(dimensions, _MATCHABLE_DIMENSIONS):
            return None
        findings = _current_findings(findings)
        dimensions = _current_dimensions(dimensions)
        if role is not None and (
            not isinstance(role, str) or not 0 < len(role) <= 40
        ):
            return None
        for text in (title, summary, prompt) + ((role,) if role else ()):
            if lexicon_violations(text) or leak_findings(text):
                return None
        seen_ids.add(skill_id)
        skills.append(
            Skill(
                skill_id=skill_id,
                title=title,
                summary=summary,
                prompt=prompt,
                work_types=tuple(work_types),
                tools=tuple(tools),
                role=role,
                findings=tuple(findings),
                dimensions=tuple(dimensions),
            )
        )
    return tuple(skills)


def _valid_tags(value: object, allowed: tuple[str, ...]) -> TypeGuard[list[str]]:
    """A non-empty list of distinct values, each from the closed vocabulary.
    A TypeGuard so callers narrow the validated value to list[str]."""
    if not isinstance(value, list) or not value:
        return False
    seen: set[str] = set()
    for item in value:
        if item not in allowed or item in seen:
            return False
        seen.add(str(item))
    return True


def _valid_optional_tags(value: object, allowed: tuple[str, ...]) -> TypeGuard[list[str]]:
    """Like _valid_tags but an EMPTY list is valid (the tag axis is optional).
    A non-list, or any member outside the vocabulary / duplicated, is invalid."""
    if not isinstance(value, list):
        return False
    seen: set[str] = set()
    for item in value:
        if item not in allowed or item in seen:
            return False
        seen.add(str(item))
    return True


# Match scoring bands (transparent integer priorities, no model). A skill that
# answers a finding happening NOW outranks one that merely strengthens a weak
# dimension, which outranks a plain work-type fit. Bands are spaced so a
# stronger reason always wins regardless of the (bounded) work-type session
# count that refines within a band.
# Finding aliases: the bridge between a NEW finding and a registry whose tag
# vocabulary predates it. A registry is a published artifact with a closed tag
# set; a finding this endpoint learns to detect after that artifact shipped
# would otherwise fire and surface nothing — worse than not firing at all,
# because the person sees a problem named and no way to act on it.
#
# `context_carried` (the W2.0 session-cohort reading) is answered by the same
# skills that answer `context_bloat`: both are "the working set is heavier than
# it needs to be". It deliberately does NOT alias to `low_cache_reuse` — carried
# weight and cache misses are different failures, and someone whose cache is
# serving 96% of their prompt tokens must not be told to keep their cache warm.
#
# Aliases are additive and harmless once a registry tags the finding directly.
FINDING_ALIASES: dict[str, tuple[str, ...]] = {
    "context_carried": ("context_bloat",),
}

# Dimension ids a PUBLISHED registry may still be using. Renaming a closed
# vocabulary that served artifacts key on is a breaking change, and this one
# was made without noticing: `recovery` became `working_pattern` on 2026-07-26,
# and the public registry (published 2026-07-09) still tags two skills with the
# old id. Because artifact validation is deliberately all-or-nothing - a server
# must not be able to push a partially malformed catalog - those two stale tags
# were dropping ALL FIFTY skills, silently, as `skills_pull=invalid_artifact`.
# Found on the first macOS run.
#
# So retired ids stay accepted and are rewritten to the current one on the way
# in. The alternative - relaxing validation to skip bad entries - would trade a
# real security property for a naming mistake.
RETIRED_DIMENSIONS: dict[str, str] = {
    "recovery": "working_pattern",
}

_MATCHABLE_DIMENSIONS: tuple[str, ...] = (*DIMENSION_IDS, *RETIRED_DIMENSIONS)

# The same trap, one vocabulary over. `rework_heavy` was retired from
# FINDING_IDS on 2026-08-13 (its signal never fires — see insights.py), and 13
# of the 50 published skills tag it. Validation is all-or-nothing by design, so
# dropping the id without this would have rejected the ENTIRE catalog as
# `skills_pull=invalid_artifact` — the exact incident RETIRED_DIMENSIONS above
# exists to document.
#
# Retired findings differ from renamed dimensions in one way that matters: a
# rename has a successor to rewrite to, a retirement does not. Inventing one
# would silently re-tag 13 skills as answers to a problem they were not
# written for. So these ids are ACCEPTED and then DROPPED — the skill stays
# valid, keeps its other tags, and simply stops matching on the dead one.
RETIRED_FINDINGS: frozenset[str] = frozenset({"rework_heavy"})

_MATCHABLE_FINDING_TAGS: tuple[str, ...] = (*FINDING_IDS, *RETIRED_FINDINGS)


def _current_dimensions(dimensions: object) -> tuple[str, ...]:
    """Registry dimension tags with retired ids rewritten to current ones, so
    nothing downstream ever sees a vocabulary that no longer exists."""
    if not isinstance(dimensions, list | tuple):
        return ()
    return tuple(
        RETIRED_DIMENSIONS.get(str(tag), str(tag)) for tag in dimensions
    )


def _current_findings(findings: object) -> tuple[str, ...]:
    """Registry finding tags with retired ids dropped. Nothing downstream ever
    sees an id that no longer has a detector behind it."""
    if not isinstance(findings, list | tuple):
        return ()
    return tuple(
        str(tag) for tag in findings if str(tag) not in RETIRED_FINDINGS
    )


def matchable_findings(active_findings: set[str]) -> set[str]:
    """Active findings expanded through FINDING_ALIASES — what the registry's
    tags are matched against."""
    expanded = set(active_findings)
    for finding_id in active_findings:
        expanded.update(FINDING_ALIASES.get(finding_id, ()))
    return expanded


SCORE_FINDING = 1_000_000  # per matched active finding
SCORE_WEAK_DIM = 10_000  # per matched weak (low-scoring) dimension
WEAK_DIM_MAX_SCORE = 55  # a dimension is "weak" at or below this 0-100 score

# Diversity caps: one finding (or one role) must not flood the shelf with
# near-duplicates — a 59-skill registry that only ever shows one topic wastes
# its breadth. Applied greedily over the ranked list, so the strongest skill
# per topic still wins its slot.
MAX_PER_ROLE = 2
MAX_PER_FINDING = 2

# Copy ledger: a skill the person copied recently has been delivered — it
# retires from the shelf for a while instead of sitting there for weeks.
# Local meta only (NFR-PRV-6); the id list never rides an emit.
SKILLS_COPIED_META_KEY = "skills_copied:v1"
SKILL_COPY_RETIRE_DAYS = 21


def record_skill_copy(store: Store, skill_id: str, day: date) -> bool:
    """Tick the copy ledger for one skill (the uiserver write path). Prunes
    entries past the retire window so the meta value stays bounded."""
    if not _ID_RE.match(skill_id):
        return False
    raw = store.meta_get(SKILLS_COPIED_META_KEY)
    try:
        entries = json.loads(raw) if raw else {}
    except ValueError:
        entries = {}
    if not isinstance(entries, dict):
        entries = {}
    entries[skill_id] = day.isoformat()
    cutoff = (day - timedelta(days=SKILL_COPY_RETIRE_DAYS)).isoformat()
    kept = {
        key: value
        for key, value in entries.items()
        if isinstance(value, str) and value >= cutoff
    }
    store.meta_set(SKILLS_COPIED_META_KEY, json.dumps(kept, sort_keys=True))
    return True


def recently_copied_skills(store: Store, today: date) -> set[str]:
    """Skill ids copied within the retire window — excluded from the shelf."""
    raw = store.meta_get(SKILLS_COPIED_META_KEY)
    try:
        entries = json.loads(raw) if raw else {}
    except ValueError:
        return set()
    if not isinstance(entries, dict):
        return set()
    cutoff = (today - timedelta(days=SKILL_COPY_RETIRE_DAYS)).isoformat()
    return {
        key
        for key, value in entries.items()
        if isinstance(value, str) and value >= cutoff
    }


@dataclass(frozen=True, slots=True)
class SkillMatch:
    """A ranked skill plus WHY it surfaced — all locally derived. `reasons` are
    render-ready tags ('answers command friction', 'build work', 'strengthens
    model economy') the UI can show so a recommendation is never a black box."""

    skill: Skill
    score: int
    reasons: tuple[str, ...]


def _tie_key(skill_id: str, day: date | None) -> str:
    """Deterministic tie-break. With a day, ties rotate by ISO week (a hash
    of week + id) so same-band skills take turns across weeks instead of the
    alphabet deciding forever — a registry author must not win the shelf by
    naming a skill 'aaa-*'. Without a day, plain id order (legacy callers)."""
    if day is None:
        return skill_id
    iso_year, iso_week, _ = day.isocalendar()
    seed = f"{iso_year}-{iso_week:02d}:{skill_id}".encode()
    return hashlib.sha256(seed).hexdigest()


def relevant_skills(
    skills: tuple[Skill, ...],
    tools_observed: set[str],
    work_mix: dict[str, int],
    active_findings: set[str] | None = None,
    weak_dimensions: set[str] | None = None,
    limit: int = 6,
    day: date | None = None,
    recently_copied: set[str] | None = None,
) -> list[SkillMatch]:
    """Rank skills against the endpoint's OWN recent work — locally, never on
    the wire — using a fully deterministic, integer-weighted score:

    - a skill is a CANDIDATE when it applies to a tool the person used (or is
      tool-agnostic) AND either serves a work-type they did / is work-type-
      agnostic, OR answers an active finding, OR strengthens a weak dimension;
    - it SCORES highest for answering a finding that fired now, next for
      strengthening a measured-weak dimension, then for fitting the dominant
      work-type (refined by that work-type's session count);
    - the ranked list is then DIVERSIFIED: at most MAX_PER_ROLE per role and
      MAX_PER_FINDING per answered finding, so one hot finding cannot fill
      the whole shelf with near-duplicates;
    - skills copied within the retire window (`recently_copied`) are
      delivered goods and sit the shelf out.

    Returns SkillMatch(skill, score, reasons) highest score first; `reasons`
    explain the surfacing. Deterministic: ties rotate weekly when `day` is
    given (see _tie_key), else break by skill id. `active_findings` and
    `weak_dimensions` are locally-derived sets (finding ids that fired,
    dimension ids at/below WEAK_DIM_MAX_SCORE); both default to empty.
    """
    # Expand through the alias bridge so a finding newer than the registry's
    # tag vocabulary still reaches the skills that answer it.
    active_findings = matchable_findings(active_findings or set())
    weak_dimensions = weak_dimensions or set()
    recently_copied = recently_copied or set()
    active_types = {
        work_type for work_type, sessions in work_mix.items() if sessions > 0
    }
    ranked: list[tuple[SkillMatch, tuple[str, ...]]] = []
    for skill in skills:
        if skill.skill_id in recently_copied:
            continue
        if not ("any" in skill.tools or set(skill.tools) & tools_observed):
            continue
        score = 0
        reasons: list[str] = []

        finding_hits = sorted(set(skill.findings) & active_findings)
        if finding_hits:
            score += SCORE_FINDING * len(finding_hits)
            reasons.extend(f"answers {hit.replace('_', ' ')}" for hit in finding_hits)

        dim_hits = sorted(set(skill.dimensions) & weak_dimensions)
        if dim_hits:
            score += SCORE_WEAK_DIM * len(dim_hits)
            reasons.extend(f"strengthens {hit.replace('_', ' ')}" for hit in dim_hits)

        work_type_fit = False
        if "any" in skill.work_types:
            work_type_fit = True
        else:
            matched = active_types & set(skill.work_types)
            if matched:
                work_type_fit = True
                score += max(work_mix.get(work_type, 0) for work_type in matched)
                reasons.extend(
                    f"{work_type.replace('_', ' ')} work" for work_type in sorted(matched)
                )

        # A skill must connect on at least one axis: a work-type fit, an active
        # finding, or a weak dimension. A tool-only match with none of these is
        # not "relevant to your work", just "runnable" — so it is dropped.
        if not (work_type_fit or finding_hits or dim_hits):
            continue
        ranked.append(
            (
                SkillMatch(skill=skill, score=score, reasons=tuple(reasons)),
                tuple(finding_hits),
            )
        )

    ranked.sort(
        key=lambda item: (-item[0].score, _tie_key(item[0].skill.skill_id, day))
    )

    # Greedy diversification over the ranked order: the strongest skill per
    # topic keeps its slot; the third near-duplicate yields to the next topic.
    chosen: list[SkillMatch] = []
    role_counts: dict[str, int] = {}
    finding_counts: dict[str, int] = {}
    for match, matched_findings in ranked:
        if len(chosen) >= limit:
            break
        role = match.skill.role or "other"
        if role_counts.get(role, 0) >= MAX_PER_ROLE:
            continue
        if any(
            finding_counts.get(hit, 0) >= MAX_PER_FINDING
            for hit in matched_findings
        ):
            continue
        role_counts[role] = role_counts.get(role, 0) + 1
        for hit in matched_findings:
            finding_counts[hit] = finding_counts.get(hit, 0) + 1
        chosen.append(match)
    return chosen
