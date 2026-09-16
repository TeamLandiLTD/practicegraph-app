"""Field-guide practices: curated ways of working with AI coding tools.

Practices are a *catalog artifact* (FR-EMT-4): the bundled set below always
exists, and an org can serve an updated set from the backend — the endpoint
pulls it through the existing catalog channel and NEVER scrapes the open web
itself (NFR-SEC-5: outbound only to the configured API base URL; curation is
a server-side concern).

Every practice is display copy, so the artifact validator enforces the same
rules as our own catalogs: closed schema, length caps, the FR-FOC-8 forbidden
lexicon, and the no-leak patterns — a server cannot push copy the product
itself would not be allowed to write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from practicegraph.analysis.insights import FINDING_IDS
from practicegraph.privacy import leak_findings, lexicon_violations

PRACTICES_VERSION = "practices-bundled-2026-07-03"

PRACTICE_TOOLS: tuple[str, ...] = ("any", "claude_code", "codex")

MAX_PRACTICES = 50
MAX_TITLE_LEN = 80
MAX_BODY_LEN = 400

_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class Practice:
    practice_id: str
    tool: str  # "any" or a Tool value
    title: str
    body: str
    finding_id: str | None = None  # highlighted when this finding triggers


# Bundled field guide. Sources: attention research on deep-work cadence and
# switching cost (translated to behavioral language per FR-FOC-8) plus vendor
# token-economics guidance. Copy is fixed and lexicon-scanned (NFR-QLT-3).
BUNDLED_PRACTICES: tuple[Practice, ...] = (
    Practice(
        "protect-the-first-block",
        "any",
        "Give the first 90 minutes to one task",
        "Open the day with a single uninterrupted block on the most demanding "
        "piece of work - before the first prompt goes anywhere else. A protected "
        "first block sets the pace of attention for the whole day.",
    ),
    Practice(
        "ninety-ten-cadence",
        "any",
        "Work in 90/10 cycles",
        "Long attention runs in roughly 90-minute waves. Ride one wave, then take "
        "a real 10-minute pause away from the screen. Two clean cycles beat five "
        "hours of unbroken grinding - the tray timer does the counting for you.",
        finding_id=None,
    ),
    Practice(
        "one-task-parallelism",
        "any",
        "Parallel agents, one task",
        "Running several agent sessions is fine when they all serve the same "
        "task - split one job into parts, not your attention into projects. "
        "Cross-project switching is what fragments the day.",
    ),
    Practice(
        "batch-small-asks",
        "any",
        "Batch small asks into one turn",
        "Ten rapid-fire prompts cost more attention and more tokens than one "
        "well-packed turn. Collect the small questions, send them together, and "
        "read the answer properly once.",
        finding_id="interruption_cluster",
    ),
    Practice(
        "think-first-prompt-second",
        "any",
        "Sketch the approach before prompting",
        "A minute spent stating the goal and constraints in your own words makes "
        "the prompt tighter, the answer better, and the retry loop shorter. Keep "
        "the thinking; delegate the typing.",
    ),
    Practice(
        "keep-sessions-warm",
        "claude_code",
        "Continue sessions to reuse cache",
        "Restarting a session throws the cache away. Continuing one serves "
        "repeated context at a tenth of the input price - the single biggest "
        "token saver on long tasks.",
        finding_id="low_cache_reuse",
    ),
    Practice(
        "trim-the-working-set",
        "claude_code",
        "Prune stale files from context",
        "Files that stopped mattering keep billing on every turn. Drop them from "
        "the working set and let the cache carry what still matters.",
        finding_id="context_bloat",
    ),
    Practice(
        "match-model-to-task",
        "any",
        "Route routine work to mid-tier models",
        "Premium models earn their price on the hard problems. Bug fixes, small "
        "refactors, and boilerplate do just as well one tier down - set that as "
        "the default and escalate deliberately.",
        finding_id="premium_heavy",
    ),
    Practice(
        "pin-reasoning-effort",
        "codex",
        "Match reasoning effort to the task",
        "Reasoning tokens bill like output. Medium effort covers most work; save "
        "high effort for problems that genuinely resist a first pass.",
    ),
    Practice(
        "verify-risk-hotspots",
        "any",
        "Slow down where mistakes cost most",
        "Generated changes touching auth, payments, or data handling deserve a "
        "deliberate read even when everything else is flowing - that is where "
        "generated code fails most often. Review at units of work, and give the "
        "risky paths the closest look.",
    ),
    Practice(
        "read-the-error-first",
        "any",
        "Read the error before you paste it",
        "Small failures are often one careful read away from obvious. Try the "
        "error message yourself first, then bring the assistant the genuinely "
        "hard ones - the debugging skill stays warm that way.",
    ),
    Practice(
        "explain-it-back",
        "any",
        "Explain a shipped change back to yourself",
        "Once a week, pick one change you accepted and walk through why it works "
        "before reusing the pattern. What you can explain, you can still fix "
        "later without help.",
    ),
)


def practices_artifact() -> dict[str, object]:
    """The bundled practices as a versioned catalog artifact (server side)."""
    return {
        "practices_version": PRACTICES_VERSION,
        "entries": [
            {
                "id": practice.practice_id,
                "tool": practice.tool,
                "title": practice.title,
                "body": practice.body,
                **({"finding": practice.finding_id} if practice.finding_id else {}),
            }
            for practice in BUNDLED_PRACTICES
        ],
    }


def parse_practices_artifact(raw: object) -> tuple[Practice, ...] | None:
    """Strict validation: closed schema, caps, lexicon and leak scans. None on
    any deviation — a server cannot push copy the product could not write."""
    if not isinstance(raw, dict) or set(raw.keys()) != {"practices_version", "entries"}:
        return None
    version = raw["practices_version"]
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return None
    entries = raw["entries"]
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_PRACTICES:
        return None
    practices: list[Practice] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        if not set(entry.keys()) <= {"id", "tool", "title", "body", "finding"}:
            return None
        practice_id = entry.get("id")
        tool = entry.get("tool")
        title = entry.get("title")
        body = entry.get("body")
        finding = entry.get("finding")
        if (
            not isinstance(practice_id, str)
            or not _ID_RE.match(practice_id)
            or practice_id in seen_ids
            or tool not in PRACTICE_TOOLS
            or not isinstance(title, str)
            or not 0 < len(title) <= MAX_TITLE_LEN
            or not isinstance(body, str)
            or not 0 < len(body) <= MAX_BODY_LEN
        ):
            return None
        if finding is not None and finding not in FINDING_IDS:
            return None
        for text in (title, body):
            if lexicon_violations(text) or leak_findings(text):
                return None
        seen_ids.add(practice_id)
        practices.append(
            Practice(
                practice_id=practice_id,
                tool=tool,
                title=title,
                body=body,
                finding_id=finding,
            )
        )
    return tuple(practices)


def relevant_practices(
    practices: tuple[Practice, ...],
    tools_observed: set[str],
    triggered_findings: set[str],
    limit: int = 6,
) -> list[tuple[Practice, bool]]:
    """Practices for the tools in use; ones matching a triggered finding are
    flagged and sorted first."""
    applicable = [
        practice
        for practice in practices
        if practice.tool == "any" or practice.tool in tools_observed
    ]
    decorated = [
        (practice, practice.finding_id is not None
         and practice.finding_id in triggered_findings)
        for practice in applicable
    ]
    decorated.sort(key=lambda item: (not item[1]))
    return decorated[:limit]
