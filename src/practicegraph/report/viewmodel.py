"""The shared view-model: one JSON-able structure behind every surface.

This is the single place where counters become *sentences*. The static
report and the interactive app (INTERACTIVE_UI_PLAN.md) both render from
here, so copy never forks and the machine-text era — `evidence: k · v`
dumps and CLI strings pasted into a human page — ends at this boundary.
Every string produced here is scanned by the lexicon and leak suites like
any other catalog (FR-FOC-8, NFR-QLT-3), and the whole structure is
deterministic (INV-6): same store, same day, same bytes.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from practicegraph.analysis.aggregate import DailySnapshot
from practicegraph.analysis.build_ideas import (
    IDEA_API_LABELS,
    BuildIdea,
    RepoPick,
)
from practicegraph.analysis.calibration import CALIBRATION_COPY
from practicegraph.analysis.capability import CapabilityReading
from practicegraph.analysis.economy import ECONOMY_COPY
from practicegraph.analysis.focus import (
    ACTIVITY_WINDOW_MIN,
    DEEP_BLOCK_MIN,
    SESSION_SPLIT_GAP_MIN,
)
from practicegraph.analysis.harness_features import (
    FEATURE_DEFS,
    WORK_TYPES,
    HarnessFeatures,
)
from practicegraph.analysis.harness_inventory import ToolVersion, is_newer
from practicegraph.analysis.insights import (
    SUGGESTION_COPY,
    WORK_TYPE_LABELS,
)
from practicegraph.analysis.model_intelligence import ModelRecommendation
from practicegraph.analysis.playbook import compose_playbook
from practicegraph.analysis.ratecard import (
    RATE_CARD_STALE_DAYS,
    rate_card_age_days,
)
from practicegraph.analysis.reliance import RELIANCE_COPY
from practicegraph.analysis.schedule import (
    TZDATA_VERSION,
    ScheduleProfile,
    compatibility_utc_schedule,
)
from practicegraph.analysis.sessions import SESSION_COPY
from practicegraph.analysis.sessiontail import SESSIONTAIL_COPY
from practicegraph.analysis.tool_defaults import ToolDefault
from practicegraph.analysis.update import UPDATE_COPY
from practicegraph.analysis.verification import VERIFICATION_COPY
from practicegraph.analysis.workunits import WORKUNITS_COPY
from practicegraph.report.format import compact, count, percent
from practicegraph.store import MarkRow

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from practicegraph.analysis.skills import SkillMatch
    from practicegraph.report.shell import PrivacyStatus, ShellExtras

VIEW_SCHEMA = "practicegraph.view/2"
_WORK_TYPE_COPY = {work_id: (label, doc) for work_id, label, doc in WORK_TYPES}
TEAMLANDI_SKILL_BASE = (
    "https://github.com/TeamLandiLTD/skill-registry/tree/main/skills"
)
# Raw file base for the Claude Code deep link. Both tools speak the Agent
# Skills format — a directory with a SKILL.md (name + description
# frontmatter) — so the registry file installs with one fetch into
# ~/.claude/skills/<id>/.
TEAMLANDI_SKILL_RAW_BASE = (
    "https://raw.githubusercontent.com/TeamLandiLTD/skill-registry/main/skills"
)

# Change queue: suggestion id -> the performance dimension it moves.
SUGGESTION_DIMENSION: dict[str, str | None] = {
    "route-routine-to-midtier": "model_economy",
    "keep-sessions-warm": "context_hygiene",
    "trim-carried-context": "context_hygiene",
    "update-rate-card": None,
}
QUEUE_LIMIT = 3
RATE_CARD_IMPACT = 10  # hygiene item: real but rarely the day's top lever


def skill_card(
    match: SkillMatch, source: str, installed_ids: frozenset[str] = frozenset()
) -> dict[str, object]:
    skill = match.skill
    card: dict[str, object] = {
        "id": skill.skill_id,
        "title": skill.title,
        "summary": skill.summary,
        "prompt": skill.prompt,
        # The registry's feedback loop: the Claude install drops the skill
        # into ~/.claude/skills/<id>, so a directory of that name on disk
        # means this card is already delivered.
        "installed": skill.skill_id in installed_ids,
        **({"role": skill.role} if skill.role else {}),
        "for_work": [
            "any work"
            if work_type == "any"
            else WORK_TYPE_LABELS.get(work_type, work_type)
            for work_type in skill.work_types
        ],
        "reasons": list(match.reasons),
    }
    if source == "teamlandi_public":
        source_url = f"{TEAMLANDI_SKILL_BASE}/{skill.skill_id}"
        card["source_url"] = source_url
        # The CODEX deep link (the `$name` mention is Codex's skill syntax;
        # the registry README's "Add a skill to Codex" flow): Codex's own
        # skill-installer fetches and installs the directory.
        card["install_command"] = f"$skill-installer install {source_url}"
        # The CLAUDE CODE deep link. Both tools speak the same Agent Skills
        # format — a SKILL.md with name/description frontmatter — so the
        # registry file drops straight into ~/.claude/skills/<id>/ as a
        # personal skill. One POSIX line, pasteable into a Claude Code
        # session (which runs it) or any shell; available on next session.
        raw_url = f"{TEAMLANDI_SKILL_RAW_BASE}/{skill.skill_id}/SKILL.md"
        card["claude_install_command"] = (
            f"mkdir -p ~/.claude/skills/{skill.skill_id} && "
            f"curl -fsSL {raw_url} -o ~/.claude/skills/{skill.skill_id}/SKILL.md"
        )
    return card


# The staffing view's role projection. The benchmark recommendation already
# computes the three casts per tool: the balanced pick is the everyday model,
# the save-tokens alternative the fast one, the maximum-capability
# alternative the strongest. A missing alternative means the balanced pick
# already IS that end of the frontier, so the role falls back to it rather
# than vanishing.
_GUIDANCE_TOOL_LABELS = {"claude_code": "Claude Code", "codex": "Codex"}
_release_is_newer = is_newer


_PRERELEASE = re.compile(r"(?i)(alpha|beta|rc|pre|dev|nightly)")


def _version_row(
    version: ToolVersion, releases: Mapping[str, tuple[str, str, str, str]]
) -> dict[str, object]:
    """One tools-page version row, compared against the install's OWN
    channel: an alpha install is measured against the newest release of any
    kind, a stable install against the newest stable — Codex publishes its
    actual work as prereleases, so the stable tag alone was the wrong
    marker for an alpha-channel machine."""
    entry = releases.get(version.tool)
    latest: str | None = None
    notes_url: str | None = None
    channel = "stable"
    if entry is not None:
        stable, stable_url, newest, newest_url = entry
        prerelease_install = bool(
            version.installed and _PRERELEASE.search(version.installed)
        )
        if prerelease_install and is_newer(newest, stable):
            latest, notes_url, channel = newest, newest_url, "prerelease"
        else:
            latest, notes_url = stable, stable_url
    return {
        "tool": version.tool,
        "tool_label": _GUIDANCE_TOOL_LABELS.get(version.tool, version.tool),
        "installed": version.installed,
        "latest": latest,
        "notes_url": notes_url,
        "channel": channel,
        "newer": _release_is_newer(latest, version.installed),
    }


def _default_for(
    defaults: Iterable[ToolDefault], tool: str
) -> tuple[str | None, str | None]:
    for default in defaults:
        if default.tool == tool:
            return default.model, default.effort
    return None, None


def _pin_matches(pin: str | None, catalog_model: str) -> bool:
    """A pinned name matches a catalog family by normalized containment:
    "claude-opus-5" matches "Claude Opus", "gpt-5.6-sol" matches itself."""
    if pin is None:
        return False
    left = "".join(ch.lower() for ch in pin if ch.isalnum())
    right = "".join(ch.lower() for ch in catalog_model if ch.isalnum())
    return bool(left) and bool(right) and (left in right or right in left)


def _mcp_calls_for(
    features: Iterable[HarnessFeatures], tool: str, server: str
) -> int | None:
    """Calls the features scan counted for this declared server. None only
    when the scan produced nothing for the harness (no logs in window) —
    a declared server the scan saw no traffic for is an honest 0."""
    for harness in features:
        if harness.tool != tool:
            continue
        if harness.sessions == 0:
            return None
        for name, calls in harness.mcp_calls:
            if name == server:
                return calls
        return 0
    return None


def _guidance_roles(rec: ModelRecommendation) -> list[dict[str, str]]:
    balanced = {
        "model": rec.model, "effort": rec.effort,
        "index": rec.index, "tokens": rec.tokens,
    }
    by_kind = {alt.kind: alt for alt in rec.alternatives}
    def cast(kind: Literal["save_tokens", "maximum_capability"]) -> dict[str, str]:
        alt = by_kind.get(kind)
        if alt is None:
            return balanced
        return {
            "model": alt.model, "effort": alt.effort,
            "index": alt.index, "tokens": alt.tokens,
        }
    return [
        {"id": "strongest", **cast("maximum_capability")},
        {"id": "everyday", **balanced},
        {"id": "fast", **cast("save_tokens")},
    ]


def trend_phrase(delta: int | None) -> str:
    """A 28-day dimension delta as a human clause (empty when flat/unknown)."""
    if delta is None or delta == 0:
        return ""
    if delta > 0:
        return f" That pattern is {delta} points better than your 28-day norm."
    return f" That pattern is {abs(delta)} points worse than your 28-day norm."


def suggestion_evidence(suggestion_id: str, metric: int) -> str:
    """The number behind a suggestion, as a sentence a person would say."""
    if suggestion_id == "route-routine-to-midtier":
        share = (
            "all of today's priced spend"
            if metric >= 100
            else f"{metric}% of today's priced spend"
        )
        return f"Premium models carried {share}."
    if suggestion_id == "keep-sessions-warm":
        return f"Only {metric}% of today's prompt tokens came from cache."
    if suggestion_id == "trim-carried-context":
        return (
            f"Each assistant turn is carrying about {compact(metric)} prompt "
            "tokens right now."
        )
    if suggestion_id == "update-rate-card":
        return (
            f"{count(metric)} turns today used models the rate card does not "
            "price yet."
        )
    return ""


def queue_entries(extras: ShellExtras) -> list[dict[str, object]]:
    """The change queue as render-ready dicts: findings supply the evidence,
    suggestions the diagnosis, practices the wording — one ranked list,
    sentences only, actions carried as ids (buttons, never CLI strings)."""
    readings = (
        {r.dimension_id: r for r in extras.performance.readings}
        if extras.performance is not None
        else {}
    )
    entries: list[tuple[int, dict[str, object]]] = []
    for suggestion in extras.suggestions:
        dimension = SUGGESTION_DIMENSION.get(suggestion.suggestion_id)
        reading = readings.get(dimension) if dimension else None
        impact = (100 - reading.score) if reading is not None else RATE_CARD_IMPACT
        title, body = SUGGESTION_COPY[suggestion.suggestion_id]
        evidence = suggestion_evidence(
            suggestion.suggestion_id, suggestion.metric
        ) + trend_phrase(reading.delta if reading is not None else None)
        entries.append(
            (
                impact,
                {
                    "title": title,
                    "body": body,
                    "evidence": evidence,
                    "dismiss_id": suggestion.suggestion_id,
                },
            )
        )
    attention = (
        extras.performance.attention_recommendation
        if extras.performance is not None
        else None
    )
    if attention is not None:
        entries.append(
            (
                attention.impact,
                {
                    "title": attention.title,
                    "body": attention.body,
                    "evidence": attention.evidence,
                    "dismiss_id": None,
                },
            )
        )
    entries.sort(key=lambda entry: (-entry[0], str(entry[1]["title"])))
    return [entry for _impact, entry in entries[:QUEUE_LIMIT]]


# ---- day ribbon (the day's visual fingerprint, shared geometry) ---------------

RIBBON_SCALE = 1000  # x/w in tenths of a percent of the local wall-clock day
RIBBON_MIN_WIDTH = 3  # visibility floor so a single mark still draws
RIBBON_MAX_TICKS = 400  # bound the artifact size on very switchy days


@dataclasses.dataclass(frozen=True, slots=True)
class RibbonShape:
    """Render-ready ribbon geometry on the 0-1000 axis: (x, w) activity
    segments, (x, w) deep blocks (45+ min single-session), and the x of every
    session alternation that happened within the activity window."""

    segments: tuple[tuple[int, int], ...]
    deep: tuple[tuple[int, int], ...]
    ticks: tuple[int, ...]


def day_ribbon_shape(
    day: date,
    marks: list[MarkRow],
    schedule: ScheduleProfile | None = None,
) -> RibbonShape:
    """The ONE implementation of the day-ribbon algorithm, shared by the
    static shell SVG and the interactive app (which receives it precomputed
    so it never re-derives behavior): activity segments split at >30-min
    silences, deep blocks where one session ran 45+ minutes uninterrupted,
    ticks where sessions alternated within 15 minutes."""
    if not marks:
        return RibbonShape((), (), ())
    schedule = schedule or compatibility_utc_schedule()
    events = sorted(
        (datetime.fromisoformat(ts), session) for session, ts, *_rest in marks
    )
    def x_of(moment: datetime) -> int:
        local = moment.astimezone(schedule.zone)
        seconds = local.hour * 3600 + local.minute * 60 + local.second
        return max(0, min(RIBBON_SCALE, seconds * RIBBON_SCALE // 86400))

    def rects(
        spans: list[tuple[datetime, datetime]],
    ) -> tuple[tuple[int, int], ...]:
        return tuple(
            (x_of(start), max(RIBBON_MIN_WIDTH, x_of(end) - x_of(start)))
            for start, end in spans
        )

    split = SESSION_SPLIT_GAP_MIN * 60
    spans: list[tuple[datetime, datetime]] = []
    seg_start = previous = events[0][0]
    for ts, _session in events[1:]:
        if (ts - previous).total_seconds() > split:
            spans.append((seg_start, previous))
            seg_start = ts
        previous = ts
    spans.append((seg_start, previous))

    by_session: dict[str, list[datetime]] = {}
    for ts, session in events:
        by_session.setdefault(session, []).append(ts)
    deep_spans: list[tuple[datetime, datetime]] = []
    for times in by_session.values():
        block_start = previous = times[0]
        for ts in times[1:]:
            if (ts - previous).total_seconds() > split:
                if (previous - block_start).total_seconds() >= DEEP_BLOCK_MIN * 60:
                    deep_spans.append((block_start, previous))
                block_start = ts
            previous = ts
        if (previous - block_start).total_seconds() >= DEEP_BLOCK_MIN * 60:
            deep_spans.append((block_start, previous))

    window = ACTIVITY_WINDOW_MIN * 60
    ticks: list[int] = []
    for (prev_ts, prev_session), (ts, session) in itertools.pairwise(events):
        if session != prev_session and (ts - prev_ts).total_seconds() <= window:
            ticks.append(x_of(ts))
            if len(ticks) >= RIBBON_MAX_TICKS:
                break

    return RibbonShape(segments=rects(spans), deep=rects(deep_spans), ticks=tuple(ticks))


def noticed_facts(extras: ShellExtras) -> dict[str, int | None]:
    """The first-impression recognition facts, deterministic plain ints.

    Numbers only — the app owns the wording and the significance rules (it
    skips zeros), so no new sentence is minted here. Sources:
    - waiting/late-night come straight from the 28-day rhythm window;
    - agent share compares the rhythm window's total marks against the
      interactive subset that the P3 gate keeps (gather counts both);
    - attention facts come from positively identified human prompts/responses;
      background model and tool traffic was excluded before aggregation;
    - the marathon peak reuses gather's marathon finding (insights logic,
      gate included): 0 until one session compacted its context enough times;
    - waved/approval-moment counts come straight from today's focus walk
      (calibrated constants in analysis/focus.py).
    """
    rhythm = extras.rhythm
    attention = extras.performance.attention if extras.performance else None
    return {
        "waiting_minutes_28d": rhythm.waiting_minutes if rhythm else 0,
        "agent_share_pct": percent(
            extras.rhythm_marks_total - extras.rhythm_marks_interactive,
            extras.rhythm_marks_total,
        ),
        "attention_high_switch_days_28d": (
            attention.high_switch_days if attention and attention.confident else 0
        ),
        "attention_active_days_28d": (
            attention.active_days if attention and attention.confident else 0
        ),
        "attention_confident": int(bool(attention and attention.confident)),
        "longest_block_min_90d": (
            extras.profile.longest_block_min if extras.profile else 0
        ),
        "quiet_hours_activity_pct": rhythm.quiet_hours_activity_pct if rhythm else 0,
        "outside_preferred_hours_pct": (
            rhythm.outside_preferred_hours_pct if rhythm else None
        ),
        "refires_today": extras.focus.refire_replies if extras.focus else 0,
        # Waved-through approvals (numbers only; the app owns the sentence
        # and its M>=2 / N>=1 significance rule): today's long-run approval
        # moments and the waved subset, straight from the focus walk.
        "waved_through_today": extras.focus.waved_through if extras.focus else 0,
        "approval_moments_today": (
            extras.focus.approval_moments if extras.focus else 0
        ),
        "marathon_compactions_today": next(
            (
                finding.metric
                for finding in extras.findings
                if finding.finding_id == "marathon_session"
            ),
            0,
        ),
    }


def _capability_view(reading: CapabilityReading) -> dict[str, object]:
    pending = (
        {
            "question": "Did the latest piece of work reach the result you wanted?",
            "subject": reading.pending_subject,
            "options": [
                {"id": "yes", "label": "Yes"},
                {"id": "partly", "label": "Partly"},
                {"id": "no", "label": "No"},
                {"id": "skip", "label": "Skip today"},
            ],
        }
        if reading.pending_outcome
        else None
    )
    opportunity = (
        {
            "practice_id": item.practice_id,
            "capability_id": item.capability_id,
            "path": item.path,
            "title": item.title,
            "observation": item.observation,
            "why": item.why,
            "practice": item.practice,
            "codex_url": item.codex_url,
            "confidence": item.confidence,
        }
        if (item := reading.opportunity) is not None
        else None
    )
    result = (
        {
            "practice_id": practice.practice_id,
            "title": practice.title,
            "status": practice.status,
            "comparable_units": practice.comparable_units,
            "line": practice.line,
        }
        if (practice := reading.practice_result) is not None
        else None
    )
    progress = (
        {
            "title": active.title,
            "practice": active.practice,
            "codex_url": active.codex_url,
            "comparable_units": active.comparable_units,
            "context": active.context,
        }
        if (active := reading.practice_progress) is not None
        else None
    )
    return {
        "paths": list(reading.paths),
        "paths_confirmed": reading.paths_confirmed,
        "pending_outcome": pending,
        "opportunity": opportunity,
        "practice_result": result,
        "practice_progress": progress,
        "history": [
            {"title": entry.title, "finished_day": entry.finished_day,
             "feedback": entry.feedback}
            for entry in reversed(reading.history)
        ],
    }


def _community_links(extras: ShellExtras) -> dict[str, list[dict[str, str]]]:
    homes = {
        "retry_storm": "practice", "refire_after_failure": "practice",
        "command_friction": "practice", "approvals_waved_through": "practice",
        "interruption_cluster": "mindfulness", "late_night_drift": "mindfulness",
        "marathon_session": "mindfulness", "context_carried": "practice",
        "low_cache_reuse": "spend", "context_bloat": "spend",
        "premium_heavy": "models", "unpriced_models": "models",
        "approaching_quota": "spend",
    }
    active = {finding.finding_id for finding in extras.findings}
    links: dict[str, list[dict[str, str]]] = {}
    for item in extras.community:
        if item.answers in active and item.answers in homes:
            links.setdefault(homes[item.answers], []).append({
                "id": item.item_id, "title": item.title,
            })
    return links


def view_model(
    snapshot: DailySnapshot,
    extras: ShellExtras,
    generated_at: datetime,
    app_version: str,
    rate_card_version: str,
    *,
    schedule: ScheduleProfile | None = None,
    local_day: date | None = None,
    privacy: PrivacyStatus | None = None,
    wording_provider: str = "off",
) -> dict[str, object]:
    """Everything a surface needs, JSON-able, closed keys. The interactive
    app serves this verbatim from `GET /api/view`."""
    if schedule is None:
        schedule = extras.schedule
    if schedule is None:
        schedule = ScheduleProfile(
            version=0,
            confirmed=False,
            timezone_name="UTC",
            tzdata_version=TZDATA_VERSION,
            working_days=(0, 1, 2, 3, 4),
            work_start="09:00",
            work_end="18:00",
            quiet_start="22:00",
            quiet_end="07:00",
            weekend_mode="exceptional",
        )
    local_today = local_day or extras.local_day or snapshot.day
    return {
        "schema": VIEW_SCHEMA,
        "billing": {
            "default_mode": extras.billing_mode,
            "by_tool": extras.billing_by_tool,
        },
        "quiet_hours": extras.quiet_hours,
        "capability": _capability_view(extras.capability),
        "community": [
            {"id": item.item_id, **dataclasses.asdict(item)} for item in extras.community
        ],
        "feed_status": extras.feed_status,
        "community_links": _community_links(extras),
        "privacy": (
            {
                "sharing_enabled": privacy.consent_enabled,
                "endpoint_configured": privacy.endpoint_configured,
                "sent_count": privacy.queue_counts.get("sent", 0),
                "queued_count": sum(
                    value for key, value in privacy.queue_counts.items()
                    if key in {"pending", "retry"}
                ),
                "wording_provider": (
                    wording_provider if wording_provider in {"claude", "codex"} else "off"
                ),
            } if privacy is not None else None
        ),
        "day": local_today.isoformat(),
        "local_day": local_today.isoformat(),
        "accounting_day_utc": snapshot.day.isoformat(),
        "schedule": {
            "version": schedule.version,
            "confirmed": schedule.confirmed,
            "timezone_name": schedule.timezone_name,
            "tzdata_version": schedule.tzdata_version,
            "working_days": list(schedule.working_days),
            "work_start": schedule.work_start,
            "work_end": schedule.work_end,
            "quiet_start": schedule.quiet_start,
            "quiet_end": schedule.quiet_end,
            "weekend_mode": schedule.weekend_mode,
        },
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "app_version": app_version,
        "rate_card_version": rate_card_version,
        # How old the pricing basis is. A stale card does not error — it prices
        # a new model generation at an old rate and shows it with confidence —
        # so the age is surfaced and the app can caveat rather than mislead.
        # None when the card is an org's curated one (not date-stamped).
        "rate_card_age_days": rate_card_age_days(rate_card_version, local_today),
        "rate_card_stale": (
            (rate_card_age_days(rate_card_version, local_today) or 0)
            > RATE_CARD_STALE_DAYS
        ),
        "totals": {
            "cost_micro_usd": snapshot.total_cost_micro_usd,
            "assistant_turns": snapshot.total_assistant_turns,
            "sessions": snapshot.session_count,
            "unpriced_turns": snapshot.total_unpriced_turns,
        },
        # PRINCIPLES 3: one qualified work-pattern observation leads. The
        # composer (report/observation.py) chooses it by a closed priority and
        # fixed sample gates, and it is the only reading in the product that
        # carries its own period, confidence and caveat. Never null - an
        # "insufficient" observation states why there is no pattern yet, which
        # is the withheld-is-explained rule, not a blank.
        "observation": {
            "id": extras.practice_observation.observation_id,
            "title": extras.practice_observation.title,
            "body": extras.practice_observation.body,
            "period": extras.practice_observation.period,
            "confidence": extras.practice_observation.confidence,
            "caveat": extras.practice_observation.caveat,
        },
        "noticed": noticed_facts(extras),
        # The reflection band: closed-copy sentences from 28-day cohort math
        # (report/reflections.py). Local-only — no emit derives from these.
        "reflections": extras.reflections,
        "ranges": [dataclasses.asdict(summary) for summary in extras.ranges],
        "focus": dataclasses.asdict(extras.focus) if extras.focus else None,
        "rhythm": dataclasses.asdict(extras.rhythm) if extras.rhythm else None,
        "blocks": extras.blocks,
        # The playbook: each detected finding answered with a paste-ready
        # prompt carrying the one number that fired (analysis/playbook.py).
        # The codex_url opens the Codex composer prefilled and sends nothing;
        # copy is the Claude Code lane. Replaces the descriptive findings
        # list — a described problem now always arrives with its handle.
        "playbook": [
            {
                "id": move.move_id,
                "title": move.title,
                "finding": move.finding_title,
                "why": move.why,
                "prompt": move.prompt,
                "codex_url": move.codex_url,
            }
            for move in compose_playbook(
                [(f.finding_id, f.metric) for f in extras.findings],
                register=extras.playbook_register,
            )
        ],
        # Skill registry matches: render-ready cards (closed copy + the copyable
        # prompt), highest-fit first, each with the locally-derived reasons it
        # surfaced. Matched locally; nothing here crossed the wire inbound, and
        # none of it goes out.
        "skills_source": extras.skills_source,
        "skills": [
            skill_card(
                match,
                extras.skills_source,
                frozenset(
                    skill.name
                    for harness in extras.harness_skills
                    if harness.tool == "claude_code"
                    for skill in harness.skills
                ),
            )
            for match in extras.skills
        ],
        # What the last taken skill's own target measure did afterwards. Present
        # only once the entry is old enough to mean something, and it says so
        # when the measure moved the other way.
        # A verified newer release, or None. The client shows a sentence and
        # a link; it never downloads or installs anything itself.
        # Things wrong with the install itself. Ordinary situations with a
        # clear next step, so no alarm tone - but stated, because the page
        # cannot otherwise tell "nothing to report" from "nobody reporting".
        "install_notices": [
            {
                "id": notice.notice_id,
                "label": notice.label,
                "line": notice.line,
                "why": notice.why,
                "action": notice.action,
            }
            for notice in extras.install_notices
        ],
        "update": (
            {
                "label": UPDATE_COPY["label"],
                "version": extras.update.version,
                "published": extras.update.published,
                "url": extras.update.url,
                "notes_url": extras.update.notes_url,
                "sha256": extras.update.sha256,
                "line": UPDATE_COPY["line"].format(
                    version=extras.update.version,
                    current=app_version,
                    published=extras.update.published,
                ),
                "action": UPDATE_COPY["action"],
                "note": UPDATE_COPY["note"],
            }
            if extras.update is not None
            else None
        ),
        # A verified newer release, or None. The client shows a sentence and
        # a link; it never downloads or installs anything itself.
        "skill_outcome": (
            {
                "skill_id": extras.skill_outcome.skill_id,
                "line": extras.skill_outcome.line,
                "note": extras.skill_outcome.note,
            }
            if extras.skill_outcome
            else None
        ),
        "news": [
            {
                "id": item.news_id,
                "kind": item.kind,
                "title": item.title,
                "hook": item.hook,
                "summary": item.summary,
                "why": item.why,
                "url": item.url,
                "source": item.source,
                **({"attention": {
                    "urgency": item.attention.urgency,
                    "reason": item.attention.reason,
                    "starts_at": item.attention.starts_at,
                    "expires_at": item.attention.expires_at,
                }} if item.attention else {}),
            }
            for item in extras.news
        ],
        # The dated shelves behind the editorial feeds: which previous days
        # are readable locally. Items for a chosen day come from
        # /api/feed-day on demand, through the same strict parse.
        "news_days": list(extras.news_days),
        "build_ideas_days": list(extras.build_ideas_days),
        # Let's build: one buildable idea per card from the APIs behind the
        # harnesses. Served or bundled; every field capped and scanned. The
        # url is display-only - official docs a person chooses to open.
        "build_ideas": [
            {
                "id": idea.idea_id,
                "api": idea.api,
                "api_label": IDEA_API_LABELS.get(idea.api, idea.api),
                "feature": idea.feature,
                "title": idea.title,
                "hook": idea.hook,
                "summary": idea.summary,
                "steps": list(idea.steps),
                "why": idea.why,
                "url": idea.url,
                "source": idea.source,
                # The receipt, when the edition carries one: an open-source
                # project that does what the idea describes.
                "repo": idea.repo,
            }
            for idea in extras.build_ideas
            if isinstance(idea, BuildIdea)
        ],
        # The hand-picked GitHub shelf under the ideas. Curated, never
        # ranked by stars; the honest caveat travels with each pick.
        "build_repos": [
            {
                "id": pick.repo_id,
                "name": pick.name,
                "url": pick.url,
                "what": pick.what,
                "why": pick.why,
                "caveat": pick.caveat,
            }
            for pick in extras.build_repos
            if isinstance(pick, RepoPick)
        ],
        # Project folders the one-click model pin may target (Claude Code
        # only). Leafs on the wire; the server re-derives real paths when
        # the action posts, so the page can never name an arbitrary path.
        "pin_projects": list(extras.pin_projects),
        # The Advisor board: at most three verdict takes, receipts lines
        # always before the market line, market claims expiry-stamped and the
        # attribution carried per take. Null when the extras predate the
        # advisor (older callers); the calm default is a real take, never a
        # missing key. Local-only forever (NFR-PRV-6).
        "advisor": (
            {
                "as_of": extras.advisor.as_of,
                "market_state": extras.advisor.market_state,
                "market_as_of": extras.advisor.market_as_of,
                "audit": extras.advisor.audit,
                "takes": [
                    {
                        "id": take.take_id,
                        "tier": take.tier,
                        "tool": take.tool,
                        "verdict": take.verdict,
                        "boundary": take.boundary,
                        "receipts": list(take.receipts),
                        "market": take.market,
                        "steelman": take.steelman,
                        "action": take.action,
                        "experiment": take.experiment,
                        "attribution": take.attribution,
                        "evidence": [dataclasses.asdict(item) for item in take.evidence],
                        "expires": take.expires,
                        # How far this take can be trusted, and why. Exported
                        # deliberately — unlike impact_micro_usd, which ranks
                        # the board and would read as a promise on the page.
                        "confidence": take.confidence,
                        "confidence_note": take.confidence_note,
                    }
                    for take in extras.advisor.takes
                ],
            }
            if extras.advisor is not None
            else None
        ),
        # Quota runway (A1): today's per-window rate-limit runway — one row per
        # window, whole percents, register from named thresholds. Null when no
        # readings today (withheld, not zero); the app hides the tile. Local-only.
        "runway": (
            {
                "buckets": [
                    {
                        "bucket": b.bucket,
                        "label": b.label,
                        "latest_pct": b.latest_pct,
                        "peak_pct": b.peak_pct,
                        "register": b.register,
                        "source_id": b.source_id,
                        "observed_at": b.observed_at,
                        "window_minutes": b.window_minutes,
                        "window_kind": b.window_kind,
                        "resets_at": b.resets_at,
                        "account_hash": b.account_hash,
                        "pool_hash": b.pool_hash,
                    }
                    for b in extras.runway.buckets
                ]
            }
            if extras.runway is not None and extras.runway.available
            else None
        ),
        # The economy reading (W1.1): capability per dollar/window over the
        # receipts window — headline integers, the one lever (advisor-first,
        # calm as a real state), dual denomination by billing mode (AM-1), and
        # the first-open retrospective until dismissed (AM-3). Sentences are
        # minted from the closed ECONOMY_COPY catalog; money stays integer
        # micro-USD and the app formats it. Null when the evidence floor is
        # unmet (withheld, not zero). Local-only forever (NFR-PRV-6).
        "economy": (
            {
                "billing_mode": extras.economy.billing_mode,
                "title": ECONOMY_COPY[f"title-{extras.economy.billing_mode}"],
                "window_days": extras.economy.window_days,
                "active_days": extras.economy.active_days,
                "cost_micro_usd": extras.economy.cost_micro_usd,
                "priced_turns": extras.economy.priced_turns,
                "cost_per_priced_turn_micro_usd": (
                    extras.economy.cost_per_priced_turn_micro_usd
                ),
                "cache_hit_pct": extras.economy.cache_hit_pct,
                "wow_cost_delta_pct": extras.economy.wow_cost_delta_pct,
                "week_window_pct": extras.economy.week_window_pct,
                # W1.2: the reasoning slice of output spend — an attribution
                # of money already inside the total, never an extra charge.
                "reasoning": (
                    {
                        "share_pct": extras.economy.reasoning.share_pct,
                        "line": extras.economy.reasoning.line,
                    }
                    if extras.economy.reasoning is not None
                    else None
                ),
                # W3.2: the cache-spend split — reading context back versus
                # re-writing it after restarts/idle expiries. Same attribution
                # framing as reasoning; null when cache spend is thin.
                "churn": (
                    {
                        "share_pct": extras.economy.churn.share_pct,
                        "line": extras.economy.churn.line,
                    }
                    if extras.economy.churn is not None
                    else None
                ),
                # W3.1: the person's own denominator — cost per commit
                # attempt, minted line carrying the prior window when it
                # clears the same floor. Null below the commit floor. In the
                # productivity profile the figure is withheld outright
                # (PRODUCTIVITY_PROFILE P0): it counts git commits, which
                # this audience does not make — showing it would read as a
                # judgment of work that simply is not commit-shaped.
                "commit_cost": (
                    {
                        "per_commit_micro_usd": (
                            extras.economy.denominator.per_commit_micro_usd
                        ),
                        "line": extras.economy.denominator.line,
                    }
                    if extras.economy.denominator is not None
                    and extras.audience != "productivity"
                    else None
                ),
                # Why the per-commit figure is absent, when it is — the
                # explained-absence sentence (W0.4). "" when it is present.
                "commit_cost_note": (
                    "Hidden in the productivity profile — it prices git "
                    "commits, which is not how this work lands."
                    if extras.audience == "productivity"
                    else extras.economy.denominator_note
                ),
                "labels": {
                    "spend": ECONOMY_COPY["label-spend"],
                    "per_turn": ECONOMY_COPY["label-per-turn"],
                    "cache": ECONOMY_COPY["label-cache"],
                    "week_window": ECONOMY_COPY["label-week-window"],
                    "wow": ECONOMY_COPY["label-wow"],
                    "reasoning": ECONOMY_COPY["label-reasoning"],
                    "churn": ECONOMY_COPY["label-churn"],
                    "commit_cost": ECONOMY_COPY["label-denominator"],
                },
                "note": (
                    ECONOMY_COPY["note-api-equivalent"]
                    if extras.economy.billing_mode == "subscription"
                    else ECONOMY_COPY["note-estimate"]
                ),
                "lever": (
                    {
                        "id": extras.economy.lever.lever_id,
                        "title": extras.economy.lever.title,
                        "body": extras.economy.lever.body,
                    }
                    if extras.economy.lever is not None
                    else None
                ),
                "retro": (
                    {
                        "title": ECONOMY_COPY["retro-title"],
                        "line": extras.economy.retro.line,
                    }
                    if extras.economy.retro is not None
                    else None
                ),
            }
            if extras.economy is not None and extras.economy.available
            else None
        ),
        # Session grain (W2.0): the latest session's receipt — the post-flight
        # "what did that cost me" — and the context-tax cohorts (how much more
        # prompt weight a long session carries per turn, and whether that lands
        # on the bill or the cache absorbs it). Null when the window holds no
        # substantial session. Session IDENTITY never appears here: only
        # derived facts and closed sentences. Local-only forever (NFR-PRV-6).
        "sessions": (
            {
                "receipt": (
                    {
                        "day": extras.sessions.receipt.day,
                        "tool": extras.sessions.receipt.tool_label,
                        "title": SESSION_COPY["receipt-title"],
                        "line": extras.sessions.receipt.line,
                        "detail": list(extras.sessions.receipt.detail),
                        "minutes": extras.sessions.receipt.minutes,
                        "assistant_turns": (
                            extras.sessions.receipt.assistant_turns
                        ),
                        "cost_micro_usd": (
                            extras.sessions.receipt.cost_micro_usd
                        ),
                        "cache_hit_pct": extras.sessions.receipt.cache_hit_pct,
                        "prompt_tokens_per_turn": (
                            extras.sessions.receipt.prompt_tokens_per_turn
                        ),
                    }
                    if extras.sessions.receipt is not None
                    else None
                ),
                "tax": (
                    {
                        "title": SESSION_COPY["tax-title"],
                        "register": extras.sessions.tax.register,
                        "line": extras.sessions.tax.line,
                        "basis": extras.sessions.tax.basis,
                        "caveat": extras.sessions.tax.caveat,
                    }
                    if extras.sessions.tax is not None
                    else None
                ),
            }
            if extras.sessions is not None and extras.sessions.available
            else None
        ),
        # How you worked with it: the collaboration-shape reading (Phase 1).
        # A job-shape headline plus the facets that cleared their sample gates.
        # Deliberately NOT a score and NOT an over/under-reliance label —
        # labelling reliance needs per-instance ground truth that content-blind
        # logs cannot supply. Every facet is two-sided; the person judges.
        # Local-only forever (NFR-PRV-6).
        "reliance": (
            {
                "eyebrow": RELIANCE_COPY["eyebrow"],
                "intro": RELIANCE_COPY["intro"],
                "headline": extras.reliance.headline,
                "window_days": extras.reliance.window_days,
                "facets": [
                    {
                        "id": facet.facet_id,
                        "label": facet.label,
                        "line": facet.line,
                        "measures": facet.measures,
                        "why": facet.why,
                        # Movement against the person's own prior window of
                        # the same length. Carries no tone — a rising handoff
                        # grain is not a fall from grace — but it is the part
                        # worth reading, because a level alone says little.
                        "delta": facet.delta,
                        "delta_unit": (
                            "events per handoff" if facet.facet_id == "grain"
                            else "percentage points"
                        ),
                        "paired": facet.paired,
                    }
                    for facet in extras.reliance.facets
                ],
            }
            if extras.reliance is not None and extras.reliance.available
            else None
        ),
        # Where the day ends: the session-tail reading — twelve weekly
        # medians of the last-prompt clock, the recent-vs-baseline drift
        # line, and the concurrency cross with both denominators (per day
        # AND per active hour, so "more work" and "just more hours" stay
        # distinguishable). Chart geometry ships with the data; surfaces
        # never re-derive an axis or the quiet band. Late-evening activity,
        # never a sleep claim. Local-only forever (NFR-PRV-6).
        "session_tail": (
            {
                "eyebrow": SESSIONTAIL_COPY["eyebrow"],
                "title": SESSIONTAIL_COPY["title"],
                "intro": SESSIONTAIL_COPY["intro"],
                "headline": extras.session_tail.headline,
                "drift_line": extras.session_tail.drift_line,
                "cross_title": SESSIONTAIL_COPY["cross-title"],
                "cross_line": extras.session_tail.cross_line,
                "note": extras.session_tail.note,
                "active_days": extras.session_tail.active_days,
                "weeks": [
                    {
                        "start_day": week.start_day,
                        "minutes": week.tail_minutes,
                        "clock": week.tail_clock,
                        "days": week.days,
                    }
                    for week in extras.session_tail.weeks
                ],
                "buckets": [
                    {
                        "id": bucket.bucket_id,
                        "label": bucket.label,
                        "days": bucket.days,
                        "clock": bucket.tail_clock,
                        "tools_per_day": bucket.tools_per_day,
                        "active_hours_tenths": bucket.active_hours_tenths,
                        "tools_per_active_hour": bucket.tools_per_active_hour,
                    }
                    for bucket in extras.session_tail.buckets
                ],
                "labels": {
                    "tail": SESSIONTAIL_COPY["bucket-tail-label"],
                    "tools": SESSIONTAIL_COPY["bucket-tools-label"],
                    "hours": SESSIONTAIL_COPY["bucket-hours-label"],
                    "density": SESSIONTAIL_COPY["bucket-density-label"],
                },
                "chart": {
                    "axis_lo": extras.session_tail.axis_lo_minutes,
                    "axis_hi": extras.session_tail.axis_hi_minutes,
                    "quiet_start": extras.session_tail.quiet_start_minutes,
                    "grid": [
                        [minutes, label]
                        for minutes, label in extras.session_tail.grid
                    ],
                },
            }
            if extras.session_tail is not None and extras.session_tail.available
            else None
        ),
        # The Calibration Mirror: the estimate-before-the-clock instrument.
        # `probe` carries the question and the brackets and DELIBERATELY no
        # actual length and no session identity — an estimate made after
        # seeing the answer is not an estimate, and the session is resolved
        # server-side when the answer posts. Local-only forever (NFR-PRV-6).
        "calibration": (
            {
                "eyebrow": CALIBRATION_COPY["eyebrow"],
                "skip": CALIBRATION_COPY["skip"],
                "probe": (
                    {
                        "question": extras.calibration.probe.question,
                        "intro": extras.calibration.probe.intro,
                        "options": [
                            {"id": bucket_id, "label": label}
                            for bucket_id, label in extras.calibration.probe.options
                        ],
                    }
                    if extras.calibration.probe is not None
                    else None
                ),
                "last": (
                    {
                        "line": extras.calibration.last.line,
                        "felt": extras.calibration.last.felt_label,
                        "actual": extras.calibration.last.actual_label,
                    }
                    if extras.calibration.last is not None
                    else None
                ),
                "summary": extras.calibration.summary,
            }
            if extras.calibration is not None and extras.calibration.available
            else None
        ),
        # The vocabulary key was cut 2026-08-21 with its card: the glossary
        # left the interface (page-diet discipline — a computed key with no
        # reader is carrying cost). The teaching lives in the per-card hints;
        # the static shell still renders the full glossary from extras.
        #
        # The documentation shelf: served artifact (catalog docs.json) or the
        # bundled default. Sections and links are validated to https before
        # they ever reach this payload; organizing the shelf is a publish on
        # our end, never a client release.
        "docs": {
            "version": extras.docs.docs_version,
            "sections": [
                {
                    "title": section.title,
                    "links": [
                        {"title": link.title, "url": link.url, "why": link.why}
                        for link in section.links
                    ],
                }
                for section in extras.docs.sections
            ],
        },
        # The staffing guidance, per harness: which model the published
        # benchmark artifact casts in each role (strongest / everyday / fast)
        # for the tools this person actually ran. Centralized in the served
        # models.json — restructuring the recommendations is a publish. The
        # local model name travels only as the "current" marker the
        # recommendation already carries.
        "model_guidance": [
            {
                "tool": rec.tool,
                "tool_label": _GUIDANCE_TOOL_LABELS.get(rec.tool, rec.tool),
                "current_model": rec.current_model,
                "current_model_matched": rec.current_model_matched,
                "published_on": rec.published_on,
                "review_needed": rec.review_needed,
                "attribution": rec.source,
                "roles": _guidance_roles(rec),
                # The pinned default the tool starts a session on, judged
                # by the served benchmark artifact: which cast it matches,
                # or nothing when it is unpinned / off-benchmark.
                "default_model": rec.default_model,
                "default_effort": rec.default_effort,
                "default_role": rec.default_role,
                "default_matched": rec.default_matched,
            }
            for rec in extras.model_recommendations
        ],
        # The raw pins, one per harness, so the page can state the default
        # even before the benchmark artifact has arrived to judge it.
        "model_defaults": [
            {
                "tool": default.tool,
                "tool_label": _GUIDANCE_TOOL_LABELS.get(default.tool, default.tool),
                "model": default.model,
                "effort": default.effort,
            }
            for default in extras.tool_defaults
        ],
        # The strain reading (STRAIN_READING_PLAN S1/S2). The felt-drain probe
        # is asked before the verification components show for the day — the
        # no-peek rule — so while today's probe is pending the components are
        # withheld from the wire entirely, not merely hidden.
        "drain": (
            {
                "available": extras.drain.available,
                "pending": extras.drain.pending,
                "question": extras.drain.question,
                "intro": extras.drain.intro,
                "options": [
                    {"id": bracket_id, "label": label}
                    for bracket_id, label in extras.drain.options
                ],
                "skip_label": extras.drain.skip_label,
                "answered_today": extras.drain.answered_today,
                "answered_count": extras.drain.answered_count,
            }
            if extras.drain is not None
            else None
        ),
        "verification": (
            {
                "available": extras.verification.available,
                # Personal reflection is optional; ordinary activity details
                # do not require answering a research-style probe first.
                "withheld_for_probe": False,
                "window_days": extras.verification.window_days,
                "sessions": extras.verification.sessions,
                "line": extras.verification.line,
                "note": extras.verification.note,
                "source": extras.verification.source,
                "title": VERIFICATION_COPY["title"],
                "eyebrow": VERIFICATION_COPY["eyebrow"],
                "hint": VERIFICATION_COPY["hint"],
                "components": (
                    [
                        {
                            "id": component.id,
                            "label": component.label,
                            "unit": component.unit,
                            "doc": component.doc,
                            "value": component.value,
                            "delta": component.delta,
                        }
                        for component in extras.verification.components
                    ]
                ),
            }
            if extras.verification is not None
            else None
        ),
        # Which audience the page speaks to; the client renders copy and
        # section leads accordingly. Never inferred silently — it is the
        # person's own preference, switchable in place.
        "profile": extras.audience,
        # The work-type mix (PRODUCTIVITY_PROFILE P2): what kind of work
        # each client's sessions were, classified by documented rules from
        # markers the features scan already reads. Shares of the scanned
        # window's sessions; every session lands in exactly one bucket.
        "work_mix": [
            {
                "tool": harness.tool,
                "tool_label": _GUIDANCE_TOOL_LABELS.get(
                    harness.tool, harness.tool
                ),
                "window_days": harness.window_days,
                "sessions": harness.sessions,
                "mix": [
                    {
                        "id": use.work_type,
                        "label": _WORK_TYPE_COPY[use.work_type][0],
                        "doc": _WORK_TYPE_COPY[use.work_type][1],
                        "sessions": use.sessions,
                        "share_pct": round(
                            100 * use.sessions / max(1, harness.sessions)
                        ),
                    }
                    for use in harness.work_mix
                ],
            }
            for harness in extras.harness_features
            if harness.work_mix
        ],
        # The model catalog (served or bundled): what is AVAILABLE on each
        # harness — the model ladder and the reasoning-effort dial — with
        # this machine's own pins marked against it. Teaching content,
        # rendered under a guidance flag; the person's pin is the only
        # local fact that travels.
        "model_catalog": [
            {
                "tool": entry.tool,
                "tool_label": _GUIDANCE_TOOL_LABELS.get(entry.tool, entry.tool),
                "version": extras.model_catalog.catalog_version,
                "models": [
                    {
                        "model": row.model,
                        "role": row.role,
                        "when": row.when,
                        "price_note": row.price_note,
                        # What the tool's config takes for this model - the
                        # value the one-click pin posts back.
                        "pin": row.pin,
                        "pinned": _pin_matches(
                            _default_for(extras.tool_defaults, entry.tool)[0],
                            row.model,
                        ),
                    }
                    for row in entry.models
                ],
                "efforts": [
                    {
                        "level": row.level,
                        "when": row.when,
                        "tone": row.tone,
                        "recommended": row.recommended,
                        "pinned": (
                            _default_for(extras.tool_defaults, entry.tool)[1]
                            == row.level
                        ),
                    }
                    for row in entry.efforts
                ],
                "pinned_model": _default_for(extras.tool_defaults, entry.tool)[0],
                "pinned_effort": _default_for(extras.tool_defaults, entry.tool)[1],
                # The best-practice teaching the served catalog carries:
                # titled facts with their stated source, the switch steps,
                # and the one-line closing rule.
                "practices": [
                    {"title": practice.title, "body": practice.body}
                    for practice in entry.practices
                ],
                "practices_source": entry.practices_source,
                "switch": list(entry.switch),
                "closing": extras.model_catalog.closing,
            }
            for entry in extras.model_catalog.tools
        ],
        # The tools page: what the harnesses ARE. Versions from their own
        # newest logs against the latest published release (the daily
        # GitHub pull); connectors and plugins from their configs; skills
        # from disk. Names only — commands, bodies and paths never travel.
        "tools": {
            "versions": [
                _version_row(version, extras.harness_releases)
                for version in extras.harness_versions
            ],
            "connectors": [
                {
                    "tool": harness.tool,
                    "tool_label": _GUIDANCE_TOOL_LABELS.get(
                        harness.tool, harness.tool
                    ),
                    "mcp": [
                        {
                            "name": item.name,
                            "enabled": item.enabled,
                            "scope": item.scope,
                            # How much: calls in the features scan window,
                            # matched by server name. 0 is a real reading
                            # (declared but unused); None means the scan
                            # had nothing for this harness at all.
                            "calls": _mcp_calls_for(
                                extras.harness_features, harness.tool,
                                item.name,
                            ),
                        }
                        for item in harness.connectors
                        if item.kind == "mcp"
                    ],
                    "plugins": [
                        {
                            "name": item.name,
                            "scope": item.scope,
                            "version": item.detail,
                        }
                        for item in harness.connectors
                        if item.kind == "plugin"
                    ],
                }
                for harness in extras.harness_connectors
            ],
            # The project folders each client worked in over the scan
            # window — the working directory's LEAF name only (the full
            # path stays in the logs), with session counts and the last
            # active day. Most recent first.
            "projects": [
                {
                    "tool": harness.tool,
                    "tool_label": _GUIDANCE_TOOL_LABELS.get(
                        harness.tool, harness.tool
                    ),
                    "window_days": harness.window_days,
                    "projects": [
                        {
                            "name": project.name,
                            "sessions": project.sessions,
                            "last_day": project.last_day,
                        }
                        for project in harness.projects
                    ],
                }
                for harness in extras.harness_features
            ],
            # What the functionality was used FOR, per harness: the closed,
            # documented taxonomy from analysis\harness_features — each row
            # is a feature the logs actually show, with its one-line doc
            # and the invocation count over the scan window.
            "features": [
                {
                    "tool": harness.tool,
                    "tool_label": _GUIDANCE_TOOL_LABELS.get(
                        harness.tool, harness.tool
                    ),
                    "window_days": harness.window_days,
                    "sessions": harness.sessions,
                    "features": [
                        {
                            "id": use.feature,
                            "label": FEATURE_DEFS[use.feature][0],
                            "doc": FEATURE_DEFS[use.feature][1],
                            "count": use.count,
                        }
                        for use in harness.features
                    ],
                }
                for harness in extras.harness_features
            ],
            "skills": [
                {
                    "tool": harness.tool,
                    "tool_label": _GUIDANCE_TOOL_LABELS.get(
                        harness.tool, harness.tool
                    ),
                    # Rendered in the features-row language: the name plus
                    # the description the skill declares about itself.
                    "skills": [
                        {"name": skill.name, "about": skill.about}
                        for skill in harness.skills
                    ],
                }
                for harness in extras.harness_skills
            ],
        },
        # Close the day (A2): today's closed state for the shutdown panel. The
        # copy lives in the app's closed dict; here it's just the flag. Local-only.
        "dayclose": {"closed": extras.day_closed},
        # Units of work (W4): the window distribution — median/p90 model
        # spend per unit, concentration, landed share, the largest units.
        # WINDOW grain only (no per-day counts: the day-close probe's
        # felt answers must stay unanchored). Sentences minted from the
        # closed WORKUNITS_COPY catalog; null below the evidence floor
        # (withheld, not zero). Local-only forever (NFR-PRV-6).
        "work_units": (
            {
                # The client gates the full rendering on this key; omitting it
                # made every available reading render as "not yet" (the page
                # showed an empty card while the numbers sat right here).
                "available": True,
                "title": WORKUNITS_COPY["title"],
                "eyebrow": WORKUNITS_COPY["eyebrow"],
                "hint": WORKUNITS_COPY["hint"],
                "note": WORKUNITS_COPY["note"],
                "window_days": extras.work_units.window_days,
                "units": extras.work_units.units,
                "median_cost_micro_usd": (
                    extras.work_units.median_cost_micro_usd
                ),
                "p90_cost_micro_usd": extras.work_units.p90_cost_micro_usd,
                "landed_share_pct": extras.work_units.landed_share_pct,
                "concentration_line": extras.work_units.concentration_line,
                "landed_line": extras.work_units.landed_line,
                "labels": {
                    "units": WORKUNITS_COPY["label-units"],
                    "median": WORKUNITS_COPY["label-median"],
                    "p90": WORKUNITS_COPY["label-p90"],
                    "landed": WORKUNITS_COPY["label-landed"],
                },
                "top_title": WORKUNITS_COPY["top-title"],
                "top": [
                    {
                        "started_day": row.started_day,
                        "hours_tenths": row.hours_tenths,
                        "assistant_turns": row.assistant_turns,
                        "cost_micro_usd": row.cost_micro_usd,
                        "outcome": row.outcome,
                    }
                    for row in extras.work_units.top
                ],
            }
            if extras.work_units is not None and extras.work_units.available
            # Withheld is explained, never blank (W0.4): the why sentence
            # stands in the slot the numbers will later occupy. Null only
            # for callers predating the reading entirely.
            else (
                {
                    "available": False,
                    "eyebrow": WORKUNITS_COPY["eyebrow"],
                    "title": WORKUNITS_COPY["title"],
                    "why": extras.work_units.why,
                }
                if extras.work_units is not None and extras.work_units.why
                else None
            )
        ),
        # Coaching acknowledgment (A3): last week's cue-pillar acknowledged if it
        # improved, else null (silence, never a negative line). No count of
        # acknowledgments ever — this is a relationship, not a scoreboard.
        "coach_ack": (
            {
                "pillar": extras.coach_ack.pillar,
                "from_score": extras.coach_ack.from_score,
                "to_score": extras.coach_ack.to_score,
                "text": extras.coach_ack.text,
            }
            if extras.coach_ack is not None
            else None
        ),
        # The training coach: per-pillar status + one cue, watch-first. Closed
        # copy, local-only (never an emit). training_load is the header reading.
        "training_load": extras.training_load,
        "coaching": [
            {
                "pillar": p.pillar,
                "label": p.label,
                "tone": p.tone,
                "summary": p.summary,  # the one-line shown by default
                "cue": p.cue,          # the full cue revealed on hover
                "measures": p.measures,  # "what this pillar measures", on hover
            }
            for p in extras.coaching
        ],
        # The conditioning readout: composite fitness-level readings computed
        # indirectly from the behavioral record (analysis/conditioning.py) —
        # never a re-display of the dimension scores. Meter geometry ships
        # with each reading so surfaces never re-derive a band. Local-only.
        "conditioning": [
            {
                "id": ind.indicator_id,
                "label": ind.label,
                "value": ind.value,
                "unit": ind.unit,
                "delta": ind.delta,
                "tone": ind.tone,
                "reading": ind.reading,
                "measures": ind.measures,
                "why": ind.why,
                "axis_max": ind.axis_max,
                "band_lo": ind.band_lo,
                "band_hi": ind.band_hi,
                "prior": ind.prior,
            }
            for ind in extras.conditioning
        ],
    }
