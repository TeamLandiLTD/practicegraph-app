"""The playbook: every finding becomes a prompt you can hand to the agent.

The page used to answer a detected issue with prose — a finding card that
described the problem and left the fixing to you. The move ported here (from
the Codex usage-coach report reviewed 2026-08-06) is to answer it with a
PROMPT: a ready-to-paste brief that carries the one measured number that
fired, asks the agent to diagnose its own working habits in this repository,
and ends with a done-when. The agent can see the code; it cannot see its own
usage economics. The prompt hands it exactly the part it is blind to.

Delivery is two-lane, matching the skills shelf: a ``codex://new?prompt=``
deep link that opens the Codex composer prefilled (nothing sends until the
person presses enter), and copy — which is also the whole Claude Code lane,
because no public URL scheme prefills a Claude Code session today.

Honesty rules, enforced by tests rather than intention:

- Slots hold ONE formatted counter each — the metric that fired the finding.
  No paths, no repo names, no commands: the store never held them (INV-5),
  so the prompts structurally cannot leak what was never stored.
- Prompt text contains no double quote, dollar sign, backtick, or newline,
  so it survives any shell quoting and any URL encoding unmangled.
- Templates are a closed catalog covering every id in FINDING_IDS; a
  detector without a move fails the suite, not the render.
- No promised savings anywhere. The prompt asks the agent to propose and
  verify; the verdict about whether it helped belongs to next week's numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from practicegraph.analysis.insights import FINDING_COPY
from practicegraph.report.format import compact

if TYPE_CHECKING:
    from practicegraph.store import Store

# The composer deep link. Their own report ships 46 of these, so the scheme
# is the ChatGPT desktop app's documented-in-practice entry point. The shell
# allowlists exactly this prefix for external launch; everything else stays
# copy-only.
CODEX_NEW_PREFIX = "codex://new?prompt="

# At most this many moves render; the rest of the findings still show in
# their cards' own lines. One expanded, the tail behind a disclosure — the
# same one-thing-first discipline as the advisor and the skills shelf.
PLAYBOOK_MAX = 4

# Two prompt registers (red-team correction, 2026-08-13). The original
# catalog assumed a code repository in every prompt — "this repository",
# AGENTS.md, tests, linters — which for a person producing documents and
# analyses is 100% invalid instruction attached to a perfectly valid
# finding. The register is chosen from window evidence (shell.py): any
# non-empty branch hash (Claude writes gitBranch; empty outside a repo) or
# any commit/test attempt selects "repo"; otherwise "plain", which speaks
# about documents, chats and notes and never mentions repo machinery.
PLAYBOOK_REGISTERS: tuple[str, ...] = ("repo", "plain")

# How each finding's metric reads as prose. "count" and "tokens" go through
# compact(); "pct" renders as a whole percent; "points" renders a
# percentage-POINT delta against the person's own baseline (never a share);
# "multiple" renders tenths-of-a-multiple as "2.3x". One number per prompt,
# always the number that fired.
_METRIC_KIND: dict[str, str] = {
    "retry_storm": "count",
    "interruption_cluster": "count",
    "low_cache_reuse": "pct",
    "context_bloat": "tokens",
    "premium_heavy": "pct",
    "unpriced_models": "count",
    "late_night_drift": "points",
    "command_friction": "pct",
    "refire_after_failure": "count",
    "marathon_session": "count",
    "approaching_quota": "pct",
    "approvals_waved_through": "count",
    "context_carried": "multiple",
}

# A "multiple" metric arrives in tenths (23 -> 2.3x), matching SessionTax.
METRIC_TENTHS = 10

# Closed copy catalog (NFR-QLT-3, lexicon- and leak-scanned). Each entry:
# <id>-move   the imperative row label
# <id>-why    one line naming what fired, with the {metric} slot
# <id>-prompt the paste-ready brief: Goal / measured context / plan-first
#             numbered task / done-when. Tool-neutral, single line, and free
#             of quote-dollar-backtick so CLI wrapping stays safe.
PLAYBOOK_COPY: dict[str, str] = {
    "retry_storm-move": "Break the retry loop with the agent",
    "retry_storm-why": "{metric} requests were retried after errors today.",
    "retry_storm-prompt": (
        "Goal: stop repeated retries in my coding-agent sessions. Measured "
        "from my local logs: {metric} requests were retried after errors "
        "today. Task, plan before changing anything: 1) when a run fails, "
        "read the full error before any retry and tell me the actual "
        "failure in one line; 2) propose a pre-flight check for this "
        "repository that catches the most common failure before a long run "
        "starts; 3) write a standing note of at most 6 lines for AGENTS.md "
        "or CLAUDE.md that says what to check before retrying. Done when: I "
        "have the read-the-error-first habit written down and the "
        "pre-flight check I can run."
    ),
    "interruption_cluster-move": "Shape asks so runs finish",
    "interruption_cluster-why": (
        "{metric} requests were interrupted mid-turn today."
    ),
    "interruption_cluster-prompt": (
        "Goal: fewer runs I have to cut short. Measured from my local logs: "
        "{metric} of my requests today were interrupted before they "
        "finished. Task: 1) look at how work is scoped in this repository "
        "and propose how to split my typical request into steps that each "
        "finish in one pass; 2) draft the plan-first instruction I should "
        "open long tasks with, so I can stop a plan instead of a half-done "
        "edit; 3) keep it to a template of at most 8 lines I can paste at "
        "the start of a session. Done when: I have that opening template."
    ),
    "low_cache_reuse-move": "Keep the cache warm with the agent",
    "low_cache_reuse-why": (
        "Only {metric} of prompt tokens came from cache today."
    ),
    "low_cache_reuse-prompt": (
        "Goal: raise how much of my prompt volume is served from cache. "
        "Measured from my local logs: only {metric} of my prompt tokens "
        "came from cache today; the rest billed at the fresh rate. Task: "
        "1) explain what resets a prompt cache in an agent session in "
        "plain terms; 2) look at how this repository is usually worked on "
        "and propose a session rhythm that keeps one session warm instead "
        "of restarting - including when a restart is still the right call; "
        "3) write the two habits that matter most as a note of at most 6 "
        "lines. Done when: I have the note and can say which of my restarts "
        "yesterday were avoidable."
    ),
    "context_bloat-move": "Cut the carried context",
    "context_bloat-why": (
        "Turns carried about {metric} prompt tokens each today."
    ),
    "context_bloat-prompt": (
        "Goal: carry less context per turn without losing continuity. "
        "Measured from my local logs: my sessions averaged about {metric} "
        "prompt tokens per assistant turn today. Task, plan first: 1) list "
        "what a session in this repository tends to re-read every turn and "
        "why; 2) propose a read-once working set and a standing note of at "
        "most 10 lines for AGENTS.md or CLAUDE.md that prevents "
        "rediscovery of the same files; 3) give me a short handover "
        "template so I can restart fresh at a task boundary instead of "
        "carrying everything forward. Done when: I have the note as a "
        "proposed diff and the handover template."
    ),
    "premium_heavy-move": "Draft a routing rule with the agent",
    "premium_heavy-why": (
        "Premium models carried {metric} of estimated value at API prices today."
    ),
    "premium_heavy-prompt": (
        "Goal: a written routing rule for which model tier gets which work. "
        "Measured from my local logs: premium models carried {metric} of "
        "my estimated value at API prices today. Task: 1) list the kinds of tasks I run in "
        "this repository from its own structure - edits, tests, refactors, "
        "questions; 2) for each, say which model tier it needs and why, "
        "being honest about where the premium tier genuinely earns its "
        "rate; 3) condense that into a routing rule of at most 8 lines I "
        "can keep in AGENTS.md or CLAUDE.md, plus the one check I should "
        "watch next week to see if quality held. Done when: I have the "
        "rule and the check."
    ),
    "unpriced_models-move": "Pin the models the estimates can price",
    "unpriced_models-why": (
        "{metric} turns today used models the rate card cannot price."
    ),
    "unpriced_models-prompt": (
        "Goal: make my usage fully priceable so my own estimates stay "
        "honest. Measured from my local logs: {metric} of my turns today "
        "ran on models missing from my local rate card. Task: 1) tell me "
        "exactly which model and version you are running as right now; "
        "2) show me where my tool configuration pins the default model "
        "and how to set it explicitly; 3) if an alias like latest is in "
        "use, propose pinning a named version instead and say what "
        "changes. Done when: my default model is pinned by name."
    ),
    "late_night_drift-move": "Close the day with a handover",
    "late_night_drift-why": (
        "Quiet-hours activity is up {metric} on your own recent baseline."
    ),
    "late_night_drift-prompt": (
        "Goal: end working days cleanly so tomorrow restarts cheap and "
        "nothing is lost by stopping. Measured from my local logs: "
        "activity inside the quiet hours I set for myself is up {metric} "
        "on my own recent baseline. Task: 1) draft an end-of-day handover "
        "template for this repository - goal, decisions, done, open, next "
        "single step - in at most 10 lines; 2) make it something I can "
        "ask for in one short message at the end of any session; 3) keep "
        "the wording neutral: this is about a clean stop, not about when "
        "I should work. Done when: I have the template and have run it "
        "once on this session."
    ),
    "command_friction-move": "Read the error before the next attempt",
    "command_friction-why": (
        "{metric} of steps came back as an error today."
    ),
    "command_friction-prompt": (
        "Goal: fewer steps in my sessions coming back as errors. Measured "
        "from my local logs: {metric} of the steps you ran today returned "
        "an error - a file that was not there, a search that found "
        "nothing, or a command that failed. Task: 1) look back over this "
        "session and list what actually failed and why, reading each error "
        "in full rather than retrying; 2) tell me which of those were "
        "caused by something I could have told you up front - the right "
        "folder, the real file name, the tool that is actually installed; "
        "3) write me a short note of at most 6 lines I can paste at the "
        "start of similar work so the same errors stop happening. Done "
        "when: I have the list of causes and the note."
    ),
    "refire_after_failure-move": "Put a step between failure and retry",
    "refire_after_failure-why": (
        "{metric} replies today chased a failed run within minutes."
    ),
    "refire_after_failure-prompt": (
        "Goal: a fixed next step for the moment after a failed run, so the "
        "loop breaks sooner than another quick retry. Measured from my "
        "local logs: {metric} of my replies today landed within minutes of "
        "a failed run. Task: 1) write the failure-triage checklist for "
        "this repository: read the whole error, name the failing layer, "
        "reproduce it in the smallest form, then fix; 2) make it at most "
        "6 lines so it fits in AGENTS.md or CLAUDE.md; 3) apply it to the "
        "most recent failure in this session as a demonstration. Done "
        "when: the checklist is written and demonstrated once."
    ),
    "marathon_session-move": "Bank the progress, restart sharp",
    "marathon_session-why": (
        "One session compacted its context {metric} times today."
    ),
    "marathon_session-prompt": (
        "Goal: carry long work across sessions without carrying the whole "
        "conversation. Measured from my local logs: one session today "
        "compacted its context {metric} times, which means it was "
        "summarizing itself repeatedly to stay under its limit. Task: "
        "1) summarize where this work stands in at most 10 lines - goal, "
        "decisions, done, open, exact next step; 2) list the few files "
        "that actually matter for the next step so a fresh session starts "
        "warm; 3) tell me what in the current context is safe to leave "
        "behind. Done when: I can paste your summary into a fresh session "
        "and continue without re-explaining."
    ),
    "approaching_quota-move": "Sequence the window that is left",
    "approaching_quota-why": (
        "The provider window sits at {metric} of its limit."
    ),
    "approaching_quota-prompt": (
        "Goal: spend the rest of a nearly full provider window on what "
        "matters most. Measured from my local logs: my current provider "
        "rate window sits at about {metric} of its limit. Task: 1) list "
        "the open work in this session or repository and split it into "
        "must-land-now versus can-wait-for-reset; 2) for the must-land "
        "part, propose the shortest path that gets it committed or "
        "written down; 3) draft the handover note for everything that "
        "waits. Done when: the must-land work is sequenced and the rest "
        "has a note."
    ),
    "approvals_waved_through-move": "Make approvals cost one real look",
    "approvals_waved_through-why": (
        "{metric} long runs were approved within seconds today."
    ),
    "approvals_waved_through-prompt": (
        "Goal: approvals that are decisions rather than reflexes. Measured "
        "from my local logs: {metric} long agent runs today were approved "
        "within seconds of the summary landing. Task: 1) from now on in "
        "this session, end every long run with a three-line handoff: what "
        "changed, what could break, the one thing to look at before "
        "approving; 2) propose that instruction as a standing note of at "
        "most 5 lines for AGENTS.md or CLAUDE.md; 3) demonstrate it on "
        "the next change you make. Done when: the note exists and the "
        "next handoff arrives in that shape."
    ),
    "context_carried-move": "Restart at the task boundary",
    "context_carried-why": (
        "Longer sessions cost about {metric} what shorter ones do per turn."
    ),
    "context_carried-prompt": (
        "Goal: stop paying for context that outlived its task. Measured "
        "from my local logs: my longer sessions this window cost about "
        "{metric} per turn what my shorter ones did, and the carried "
        "context is why. Task: 1) explain in plain terms why a turn "
        "late in a session bills more than an early one; 2) propose the "
        "task-boundary rule for my work in this repository: the signals "
        "that a fresh session with a handover beats continuing; 3) write "
        "the 10-line handover template that makes restarting cheap. Done "
        "when: I have the rule and the template."
    ),
    # ---- The default check (R3, 2026-08-13). The one setting that decides
    # most of the month is the default model, and this product structurally
    # cannot see it: we see the model on every turn, never the setting that
    # chose it — and on the machine this was verified on, the pinned config
    # value and the observed turns DISAGREED, so any card claiming "your
    # default is X" would lie. The inversion: we state the observed
    # distribution, say plainly what we cannot see, and hand the person's own
    # agent — which CAN read its configuration — the question. Fires when
    # premium models carry the day's estimated value at API prices or when turns run unpriced
    # (the auto-alias case), and subsumes the unpriced_models move.
    "default_check-move": "Find out what your sessions start on",
    "default_check-why-share": (
        "Premium models carried {metric} of estimated value at API prices today; which "
        "setting chose them is not in the logs."
    ),
    "default_check-why-unpriced": (
        "{metric} turns today ran on models the rate card cannot price - "
        "often the sign of an automatic default."
    ),
    "default_check-prompt": (
        "Goal: know what my sessions start on, because my own logs cannot "
        "see it. Measured from my local logs: {evidence} I see the model on "
        "every turn; I never see the setting that chose it. Task: 1) tell "
        "me my current default model and reasoning effort and exactly where "
        "they are set - configuration file, environment, or per-session "
        "choice; 2) if the default is a top-tier model or an automatic "
        "alias, propose the everyday model at medium effort instead and "
        "show me the exact change before applying anything; 3) tell me "
        "what the change commits me to and how to undo it. Done when: I "
        "know where my default lives and have chosen it on purpose. If "
        "quality dips afterwards, switch back - that result matters more "
        "than the rate."
    ),
    "default_check-prompt-plain": (
        "Goal: know what my sessions start on, because my own logs cannot "
        "see it. Measured from my local logs: {evidence} I see the model on "
        "every turn; I never see the setting that chose it. Task: 1) tell "
        "me which model and reasoning effort my sessions start on and "
        "where that is chosen in this tool; 2) if the default is a "
        "top-tier model or an automatic alias, propose the everyday model "
        "at medium effort instead and show me exactly what would change "
        "before changing anything; 3) tell me how to undo it if I change "
        "my mind. Done when: I know where my default lives and have chosen "
        "it on purpose. If quality dips afterwards, switch back - that "
        "result matters more than the rate."
    ),
    # ---- The plain register: same findings, same numbers, instructions a
    # person working in documents and chats can actually follow. No
    # repositories, no AGENTS.md, no tests, no linters, no diffs — a
    # standing note here is a note the person keeps and pastes.
    "retry_storm-prompt-plain": (
        "Goal: stop repeated retries in my sessions. Measured from my "
        "local logs: {metric} requests were retried after errors today. "
        "Task, plan before changing anything: 1) when a step fails, read "
        "the full error before any retry and tell me the actual failure "
        "in one line; 2) tell me what is worth checking before starting a "
        "long run in my kind of work - the right file is attached, the "
        "folder is the right one, the source is reachable; 3) write that "
        "as a note of at most 6 lines I can paste at the start of future "
        "sessions. Done when: I have the one-line failure habit and the "
        "note."
    ),
    "interruption_cluster-prompt-plain": (
        "Goal: fewer runs I have to cut short. Measured from my local "
        "logs: {metric} of my requests today were interrupted before they "
        "finished. Task: 1) propose how to split my typical request into "
        "steps that each finish in one pass; 2) draft the short opening "
        "message I should start big asks with - what I want, what good "
        "looks like, what to work from; 3) keep it to a template of at "
        "most 8 lines I can paste at the start of a session. Done when: I "
        "have that opening template."
    ),
    "low_cache_reuse-prompt-plain": (
        "Goal: raise how much of my prompt volume is served from cache. "
        "Measured from my local logs: only {metric} of my prompt tokens "
        "came from cache today; the rest billed at the fresh rate. Task: "
        "1) explain in plain terms what makes a session cheap to continue "
        "and expensive to restart; 2) propose a simple rhythm for my day "
        "- when staying in one chat pays, and when a fresh one is "
        "genuinely better; 3) write the two habits that matter most as a "
        "note of at most 6 lines. Done when: I have the note."
    ),
    "context_bloat-prompt-plain": (
        "Goal: carry less into each reply without losing the thread. "
        "Measured from my local logs: my sessions averaged about {metric} "
        "prompt tokens per assistant turn today - roughly a long report "
        "re-read before every reply. Task: 1) tell me what this session "
        "keeps re-reading each turn and what it actually needs; 2) when I "
        "attach documents, tell me how to ask for a working summary first "
        "and continue from that instead of re-attaching the originals; "
        "3) give me a short handover template so I can start a fresh "
        "session at a task boundary without re-explaining. Done when: I "
        "have the handover template and the attachment habit."
    ),
    "premium_heavy-prompt-plain": (
        "Goal: a simple rule for which model I pick for which work. "
        "Measured from my local logs: premium models carried {metric} of "
        "my estimated value at API prices today. Task: 1) list the kinds of work I bring "
        "to you - drafting, summarising, checking, analysis - and say "
        "which model tier each needs and why, being honest about where "
        "the top tier genuinely earns its rate; 2) tell me exactly where "
        "the model picker is in this tool and when to switch; 3) condense "
        "the rule to at most 6 lines I can keep. Done when: I have the "
        "rule and know where the picker is."
    ),
    "unpriced_models-prompt-plain": (
        "Goal: make my usage fully priceable so my own estimates stay "
        "honest. Measured from my local logs: {metric} of my turns today "
        "ran on models missing from my local rate card. Task: 1) tell me "
        "exactly which model and version you are running as right now; "
        "2) tell me where the default model is chosen in this tool and "
        "how I change it; 3) if the default is an automatic alias, "
        "propose picking a named model instead and say what changes. Done "
        "when: my default model is one I chose by name."
    ),
    "late_night_drift-prompt-plain": (
        "Goal: end working days cleanly so tomorrow restarts cheap and "
        "nothing is lost by stopping. Measured from my local logs: "
        "activity inside the quiet hours I set for myself is up {metric} "
        "on my own recent baseline. Task: 1) draft an end-of-day handover "
        "template for my work - goal, decisions, done, open, next single "
        "step - in at most 10 lines; 2) make it something I can ask for "
        "in one short message at the end of any session; 3) keep the "
        "wording neutral: this is about a clean stop, not about when I "
        "should work. Done when: I have the template and have run it "
        "once on this session."
    ),
    "command_friction-prompt-plain": (
        "Goal: fewer steps in my sessions coming back as errors. Measured "
        "from my local logs: {metric} of the steps you ran today returned "
        "an error - a file that was not there, a search that found "
        "nothing, or a step that failed. Task: 1) look back over this "
        "session and list what actually failed and why, reading each "
        "error in full rather than retrying; 2) tell me which failures I "
        "could prevent up front - attaching the right file, naming the "
        "folder, saying where things live; 3) write me a note of at most "
        "6 lines I can paste at the start of similar work. Done when: I "
        "have the list of causes and the note."
    ),
    "refire_after_failure-prompt-plain": (
        "Goal: a fixed next step for the moment after a failed run, so "
        "the loop breaks sooner than another quick retry. Measured from "
        "my local logs: {metric} of my replies today landed within "
        "minutes of a failed run. Task: 1) write the what-to-do-after-a-"
        "failure checklist for my kind of work: read the whole error, "
        "name what failed, try the smallest version first, then continue; "
        "2) keep it to at most 6 lines, as a note I can keep and paste; "
        "3) apply it to the most recent failure in this session as a "
        "demonstration. Done when: the checklist is written and "
        "demonstrated once."
    ),
    "marathon_session-prompt-plain": (
        "Goal: carry long work across sessions without carrying the whole "
        "conversation. Measured from my local logs: one session today "
        "compacted its context {metric} times, which means it was "
        "summarizing itself repeatedly to stay under its limit. Task: "
        "1) summarize where this work stands in at most 10 lines - goal, "
        "decisions, done, open, exact next step; 2) list the few things "
        "that actually matter for the next step so a fresh session starts "
        "warm; 3) tell me what in the current conversation is safe to "
        "leave behind. Done when: I can paste your summary into a fresh "
        "session and continue without re-explaining."
    ),
    "approaching_quota-prompt-plain": (
        "Goal: spend the rest of a nearly full provider window on what "
        "matters most. Measured from my local logs: my current provider "
        "rate window sits at about {metric} of its limit. Task: 1) list "
        "the open work in this session and split it into must-finish-now "
        "versus can-wait-for-reset; 2) for the must-finish part, propose "
        "the shortest path to getting it saved or written down; 3) draft "
        "the handover note for everything that waits. Done when: the "
        "must-finish work is sequenced and the rest has a note."
    ),
    "approvals_waved_through-prompt-plain": (
        "Goal: approvals that are decisions rather than reflexes. "
        "Measured from my local logs: {metric} long agent runs today were "
        "approved within seconds of the summary landing. Task: 1) from "
        "now on in this session, end every long run with a three-line "
        "handoff: what changed, what could be wrong, the one thing to "
        "check before I say go; 2) write that as a note of at most 5 "
        "lines I can paste into future sessions; 3) demonstrate it on "
        "your next result. Done when: the note exists and the next "
        "handoff arrives in that shape."
    ),
    "context_carried-prompt-plain": (
        "Goal: stop paying for context that outlived its task. Measured "
        "from my local logs: my longer sessions this window cost about "
        "{metric} per turn what my shorter ones did, and the carried "
        "context is why. Task: 1) explain in plain terms why a reply "
        "late in a long session costs more than an early one; 2) propose "
        "the signals that a fresh session with a handover beats "
        "continuing; 3) write the 10-line handover template that makes "
        "restarting cheap. Done when: I have the rule and the template."
    ),
}


@dataclass(frozen=True, slots=True)
class PlaybookMove:
    """One finding answered with a paste-ready brief."""

    move_id: str  # the finding id it answers
    title: str  # imperative row label
    finding_title: str  # the finding's own display title, for the chip
    why: str  # one line naming the measured trigger
    prompt: str  # the brief itself, single line, shell- and URL-safe
    codex_url: str  # CODEX_NEW_PREFIX + urlencoded prompt


def _metric_text(finding_id: str, metric: int) -> str:
    kind = _METRIC_KIND[finding_id]
    if kind == "pct":
        return f"{metric}%"
    if kind == "points":
        return f"{metric} points"
    if kind == "multiple":
        return f"{metric // METRIC_TENTHS}.{metric % METRIC_TENTHS}x"
    return compact(metric)


def playbook_register(store: Store, today: date, window_days: int = 28) -> str:
    """Which world the prompts should speak to, from window evidence.

    "repo" on any repository sign in the window: a non-empty branch hash
    (Claude Code records gitBranch; the hash is empty outside a repo) or any
    commit/test attempt (both tools, from command classification). Otherwise
    "plain". Deliberately NOT keyed on file writes — a person producing
    documents writes files constantly, and routing them to the repository
    register on that evidence was the original mistake, reintroduced
    (red-team finding 8, 2026-08-13)."""
    start = today - timedelta(days=window_days - 1)
    for row in store.work_spans_between(start.isoformat(), today.isoformat()):
        # WorkSpanRow order (store.py): session_key, cwd_hash, branch_hash,
        # first_ts, last_ts, 6 token/cost fields, unpriced, then
        # git_commit_attempts, test_run_attempts, artifact counters.
        branch_hash = str(row[2])
        git_commits = int(row[12])
        test_runs = int(row[13])
        if branch_hash or git_commits > 0 or test_runs > 0:
            return "repo"
    return "plain"


def compose_playbook(
    findings: list[tuple[str, int]], register: str = "repo"
) -> tuple[PlaybookMove, ...]:
    """Findings (id, metric) in detector order -> at most PLAYBOOK_MAX moves.

    ``register`` picks which prompt the move carries: "repo" when the window
    showed repository evidence, "plain" when it did not. Same finding, same
    number, an instruction the person can actually follow.

    Unknown ids are skipped rather than raised: the findings list is a closed
    catalog upstream, so an unknown here means a version skew mid-upgrade and
    the honest behavior is to not invent a prompt for it."""
    suffix = "-prompt" if register == "repo" else "-prompt-plain"
    moves: list[PlaybookMove] = []

    # The default check leads when it fires: the setting is the highest-
    # leverage single change (one act, persistent), and it is the one thing
    # here the logs cannot answer alone. It subsumes the unpriced_models
    # move — pinning a named default IS its remedy.
    by_id = dict(findings)
    default_check = _default_check(by_id, suffix)
    if default_check is not None:
        moves.append(default_check)

    for finding_id, metric in findings:
        if f"{finding_id}{suffix}" not in PLAYBOOK_COPY:
            continue
        if default_check is not None and finding_id == "unpriced_models":
            continue
        slot = {"metric": _metric_text(finding_id, metric)}
        prompt = PLAYBOOK_COPY[f"{finding_id}{suffix}"].format(**slot)
        moves.append(
            PlaybookMove(
                move_id=finding_id,
                title=PLAYBOOK_COPY[f"{finding_id}-move"],
                finding_title=FINDING_COPY[finding_id][0],
                why=PLAYBOOK_COPY[f"{finding_id}-why"].format(**slot)[0].upper()
                + PLAYBOOK_COPY[f"{finding_id}-why"].format(**slot)[1:],
                prompt=prompt,
                codex_url=CODEX_NEW_PREFIX + quote(prompt, safe=""),
            )
        )
        if len(moves) == PLAYBOOK_MAX:
            break
    return tuple(moves)


def _default_check(
    findings_by_id: dict[str, int], suffix: str
) -> PlaybookMove | None:
    """The R3 move: observed distribution + the question only the person's
    agent can answer. Release condition covers BOTH doors (red-team 3): the
    premium share when it is readable, and the unpriced count when an
    automatic alias keeps the share from ever being computed."""
    share = findings_by_id.get("premium_heavy")
    unpriced = findings_by_id.get("unpriced_models")
    if share is None and unpriced is None:
        return None
    if share is not None:
        why = PLAYBOOK_COPY["default_check-why-share"].format(
            metric=_metric_text("premium_heavy", share)
        )
    else:
        why = PLAYBOOK_COPY["default_check-why-unpriced"].format(
            metric=_metric_text("unpriced_models", unpriced or 0)
        )
    evidence = why[0].lower() + why[1:]
    prompt = PLAYBOOK_COPY[f"default_check{suffix}"].format(evidence=evidence)
    return PlaybookMove(
        move_id="default_check",
        title=PLAYBOOK_COPY["default_check-move"],
        finding_title="What sessions start on",
        why=why[0].upper() + why[1:],
        prompt=prompt,
        codex_url=CODEX_NEW_PREFIX + quote(prompt, safe=""),
    )
