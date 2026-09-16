"""Synthetic Claude Code + Codex CLI session logs for a demo profile.

Writes ~35 days of a fictional developer's activity in exactly the shapes the
two source adapters accept (``practicegraph.sources.claude_code`` and
``practicegraph.sources.codex``), so the app can be pointed at a profile that
contains no real person's data for public screenshots.

    uv run python -m tools.demo_logs --out <dir> --days 35 --seed 7 --end 2026-09-16

Point the engine at the output with::

    PRACTICEGRAPH_CLAUDE_HOME=<dir>/claude
    PRACTICEGRAPH_CODEX_HOME=<dir>/codex

Everything is derived from one ``random.Random(seed)``; the same arguments
produce byte-identical files (and identical mtimes, which the 30-day feature
scan reads). No real names, hosts, keys or e-mail addresses appear anywhere:
the user is ``demo``, hosts live under ``example.com``, and every prompt is
a generic commit-message-like sentence.

Layout written under ``--out``:

    claude/projects/<slug>/<session-uuid>.jsonl
    claude/projects/<slug>/<session-uuid>/subagents/agent-<id>.jsonl
    claude/settings.json, claude/plugins/installed_plugins.json, claude/skills/*
    .claude.json                      (MCP servers the Integrations page lists)
    codex/sessions/YYYY/MM/DD/rollout-<stamp>-<uuid>.jsonl
    codex/config.toml, codex/skills/*
"""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

CLAUDE_VERSION = "2.1.270"
CODEX_VERSION = "0.154.0"
DEFAULT_TZ = "Europe/Berlin"

# Model ids that the bundled rate card prices (analysis/ratecard.py). The
# dated haiku id exercises the release-date matching rule; the others match
# exactly. Weights are per-session picks.
CLAUDE_SESSION_MODELS: tuple[tuple[str, float], ...] = (
    ("claude-sonnet-5", 0.62),
    ("claude-fable-5", 0.30),
    ("claude-haiku-4-5", 0.08),
)
CLAUDE_SUBAGENT_MODELS: tuple[tuple[str, float], ...] = (
    ("claude-haiku-4-5-20251001", 0.55),
    ("claude-sonnet-5", 0.45),
)
CODEX_SESSION_MODELS: tuple[tuple[str, float], ...] = (
    ("gpt-5.6-terra", 0.70),
    ("gpt-5.6-luna", 0.30),
)
CODEX_EFFORTS: tuple[tuple[str, float], ...] = (("medium", 0.45), ("high", 0.45), ("xhigh", 0.10))

# Rough share of sessions that run in Claude Code (the rest in Codex).
CLAUDE_SHARE = 0.52

# What kind of session it is: "build" edits files, "investigate" only reads,
# searches and runs commands, "chat" is conversation without tools.
SESSION_MODES: tuple[tuple[str, float], ...] = (
    ("build", 0.68), ("investigate", 0.22), ("chat", 0.10),
)

# Model calls per human prompt (an agent loop of tool cycles). Real Claude Code
# and Codex tasks routinely run 20-40 tool cycles before answering; this is the
# main dial for volume and therefore spend.
CALLS_PER_PROMPT: tuple[tuple[int, float], ...] = (
    (1, 0.06), (2, 0.08), (4, 0.12), (6, 0.15), (9, 0.18),
    (14, 0.17), (20, 0.12), (30, 0.08), (45, 0.04),
)

# Claude prompt-cache TTL: a human pause longer than this re-writes the cache.
CACHE_TTL_S = 300
# Context size that triggers a compaction in the synthetic sessions.
COMPACT_AT_TOKENS = 165_000

# Shared prompt prefix every Claude Code call reads from the cache (system
# prompt + tool schemas) — realistic sizes on current builds.
CLAUDE_SHARED_PREFIX = (9_000, 12_500)
CLAUDE_INITIAL_CONTEXT = (16_000, 30_000)
CODEX_INITIAL_CONTEXT = (12_000, 22_000)


@dataclass(frozen=True)
class Project:
    name: str
    cwd: str
    branches: tuple[str, ...]
    files: tuple[str, ...]
    prompts: tuple[str, ...]
    tests: str
    mcp: tuple[str, ...]  # connector servers this project tends to call


PROJECTS: tuple[Project, ...] = (
    Project(
        name="ledger-api",
        cwd=r"C:\Users\demo\projects\ledger-api",
        branches=("main", "feat/webhook-retry", "fix/ledger-sync-flake", "chore/py313"),
        files=(
            r"src\ledger\webhooks.py", r"src\ledger\sync.py", r"src\ledger\models.py",
            r"src\ledger\api\payments.py", r"src\ledger\api\accounts.py",
            r"tests\test_sync.py", r"tests\test_webhooks.py", r"pyproject.toml",
            r"alembic\versions\0042_add_retry_state.py", r"README.md",
        ),
        prompts=(
            "Add retry to the payment webhook",
            "Fix flaky test in ledger sync",
            "Add an idempotency key to the transfer endpoint",
            "Paginate the account statement endpoint",
            "Write a migration for the retry_state column",
            "Why does test_sync_reconciles fail on the CI runner only?",
            "Refactor the webhook signature check into its own module",
            "Add structured logging to the sync worker",
            "Make the balance query use the covering index",
            "Add rate limiting to the public payments API",
            "Backfill the currency column for old ledger entries",
            "Review the open PR for the settlement batch job",
            "Add a health endpoint that checks the database pool",
            "Speed up the test suite, it takes four minutes now",
            "Handle the duplicate-webhook case without a 500",
        ),
        tests="uv run pytest -q",
        mcp=("postgres", "github"),
    ),
    Project(
        name="storefront-web",
        cwd=r"C:\Users\demo\projects\storefront-web",
        branches=("main", "feat/checkout-redesign", "fix/cart-total-rounding",
                  "chore/upgrade-deps"),
        files=(
            r"src\components\Cart.tsx", r"src\components\Checkout.tsx",
            r"src\hooks\useCart.ts", r"src\lib\pricing.ts", r"src\pages\product.tsx",
            r"src\styles\checkout.css", r"tests\cart.test.ts", r"package.json",
            r"vite.config.ts", r"docs\CHECKOUT_FLOW.md",
        ),
        prompts=(
            "Fix the cart total rounding on multi-currency orders",
            "Redesign the checkout summary panel",
            "Add a loading skeleton to the product grid",
            "Why does the cart badge lag one click behind?",
            "Upgrade vite and fix the breaking config changes",
            "Add keyboard navigation to the size picker",
            "Lazy-load the review carousel below the fold",
            "Write tests for the discount code hook",
            "Make the checkout form work without JavaScript enabled",
            "Trim the bundle, the vendor chunk is over a megabyte",
            "Add an empty state to the wishlist page",
            "Fix the focus trap in the address modal",
            "Screenshot the checkout page and compare with the mockup",
            "Add a sticky add-to-cart bar on mobile",
        ),
        tests="npm test",
        mcp=("playwright", "github"),
    ),
    Project(
        name="infra-scripts",
        cwd=r"C:\Users\demo\projects\infra-scripts",
        branches=("main", "feat/backup-rotation", "fix/alert-thresholds"),
        files=(
            r"scripts\rotate_backups.py", r"scripts\check_certs.py", r"scripts\deploy.ps1",
            r"terraform\main.tf", r"terraform\variables.tf", r"ansible\site.yml",
            r"tests\test_rotate.py", r"Makefile", r"docs\RUNBOOK.md",
        ),
        prompts=(
            "Rotate backups older than thirty days",
            "Add a certificate expiry check to the nightly job",
            "Tighten the disk alert thresholds",
            "Write a runbook for the failover procedure",
            "Convert the deploy script to use the new CLI flags",
            "Why did the nightly backup job exit with code 137?",
            "Add dry-run mode to the rotation script",
            "Pin the terraform provider versions",
            "Add a smoke test after every deploy",
            "Make the ansible playbook idempotent on the cache hosts",
            "Summarise last night's cron failures",
        ),
        tests="uv run pytest -q tests",
        mcp=("github",),
    ),
)

FOLLOW_UPS_SHORT: tuple[str, ...] = (
    "yes", "go ahead", "ok", "do it", "looks good", "continue", "commit it", "ship it", "1",
)
FOLLOW_UPS_LONG: tuple[str, ...] = (
    "Run the tests again and show me only the failures",
    "That breaks the existing callers, keep the old signature and add an overload",
    "Add a test for the empty-list case before you change the loop",
    "Explain why the previous approach was wrong, then fix it",
    "Split this into two commits: the refactor and the behaviour change",
    "Check the docs for the current recommended way to do this",
    "Use the existing helper instead of writing a new one",
    "Update the README section that describes this",
    "Make the error message say which record failed",
    "Undo the change to the config file, that was not part of the task",
    "Now do the same for the other endpoint",
    "What else could cause this? List the options before changing anything",
)

CLAUDE_TEXTS: tuple[str, ...] = (
    "I'll start by reading the current implementation.",
    "The failure comes from the retry loop not resetting the backoff between attempts.",
    "Tests pass now. Here is what changed and why.",
    "Two files touched; the behaviour change is isolated to the sync worker.",
    "Let me check how the callers use this before changing the signature.",
    "Done. The commit is on the current branch.",
    "The rounding error appears when the currency has three decimal places.",
    "Running the suite to confirm nothing else depends on the old order.",
)

CODEX_TEXTS: tuple[str, ...] = (
    "Inspecting the module and its tests first.",
    "Applied the patch; running the tests.",
    "All green. Summary of the change follows.",
    "The root cause is a stale cache key; the fix clears it on rotation.",
    "Committed on the current branch.",
)

SUBAGENT_PROMPTS: tuple[str, ...] = (
    "Find every caller of the webhook signature check and report the file and line",
    "Search the codebase for places that format currency amounts",
    "List the test files that touch the sync worker and summarise what each covers",
    "Explore how the cart total is computed and where rounding happens",
    "Find where the backup retention period is configured",
)

WEB_QUERIES: tuple[str, ...] = (
    "python httpx retry backoff best practice",
    "vite 7 migration guide config changes",
    "postgres covering index include columns",
    "terraform provider version constraint syntax",
    "react focus trap dialog accessibility",
)

CLAUDE_SKILLS: tuple[str, ...] = ("code-review", "release-notes", "db-migration", "commit")
CODEX_SKILLS: tuple[str, ...] = ("code-review", "release-notes")


def _pick(rng: Random, table: tuple[tuple[str, float], ...]) -> str:
    return rng.choices([name for name, _ in table], [w for _, w in table])[0]


def _calls(rng: Random) -> int:
    return rng.choices([n for n, _ in CALLS_PER_PROMPT], [w for _, w in CALLS_PER_PROMPT])[0]


def _pause(rng: Random) -> timedelta:
    """Human think time between prompts: mostly quick, sometimes a real break
    (which lapses the Claude prompt cache and re-writes it — a real cost)."""
    roll = rng.random()
    if roll < 0.62:
        seconds = rng.randint(10, 75)
    elif roll < 0.9:
        seconds = rng.randint(75, 300)
    else:
        seconds = rng.randint(360, 1200)
    return timedelta(seconds=seconds, milliseconds=rng.randint(0, 999))


def _uuid(rng: Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _token(rng: Random, length: int) -> str:
    return "".join(rng.choice(_ALNUM) for _ in range(length))


def _hex(rng: Random, length: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(length))


def _stamp(ts: datetime) -> str:
    utc = ts.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _write_jsonl(path: Path, records: list[dict[str, object]], mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    epoch = mtime.astimezone(UTC).timestamp()
    os.utime(path, (epoch, epoch))


@dataclass
class SessionPlan:
    start: datetime
    duration_s: int
    harness: str  # "claude" | "codex"
    project: Project
    branch: str
    mode: str = "build"


@dataclass
class Summary:
    claude_sessions: int = 0
    claude_subagents: int = 0
    codex_sessions: int = 0
    files: list[Path] = field(default_factory=list)
    first_day: str = ""
    last_day: str = ""


# --------------------------------------------------------------------------
# Day / session planning
# --------------------------------------------------------------------------


def plan_days(
    rng: Random, end: date, days: int, tz: ZoneInfo, end_hour: int | None = None
) -> list[SessionPlan]:
    """``end_hour`` caps the LAST day at that local hour, so a profile generated
    for "today" holds no readings from later than the moment it is viewed (the
    usage-limits card treats a future observation as unknown)."""
    plans: list[SessionPlan] = []
    first = end - timedelta(days=days - 1)
    focus_offset = rng.randrange(len(PROJECTS))
    for offset in range(days):
        day = first + timedelta(days=offset)
        weekday = day.weekday()
        week = (day - first).days // 7
        focus = PROJECTS[(week + focus_offset) % len(PROJECTS)]
        if weekday < 5:
            count = rng.choices([3, 4, 5, 6], [0.08, 0.27, 0.37, 0.28])[0]
        else:
            count = rng.choices([0, 1, 2], [0.25, 0.50, 0.25])[0]
        starts: list[int] = []  # seconds since local midnight
        for _ in range(count):
            if weekday < 5 and rng.random() < 0.12:
                starts.append(rng.randint(20 * 3600, 22 * 3600 + 1800))  # late evening
            elif weekday < 5:
                starts.append(rng.randint(9 * 3600, 18 * 3600 + 1800))
            else:
                starts.append(rng.randint(10 * 3600, 17 * 3600))
        starts.sort()
        cursor = 0
        latest_end = 23 * 3600 + 1800
        if end_hour is not None and offset == days - 1:
            latest_end = min(latest_end, end_hour * 3600)
            starts = [s for s in starts if s + 600 <= latest_end]
        for start_s in starts:
            lo, hi = rng.choices(
                [(5, 15), (15, 35), (35, 60), (60, 90)], [0.18, 0.28, 0.30, 0.24]
            )[0]
            duration = rng.randint(lo * 60, hi * 60)
            start_s = max(start_s, cursor + rng.randint(120, 900))
            if start_s + 300 > latest_end:
                continue
            duration = min(duration, latest_end - start_s)
            project = focus if rng.random() < 0.6 else rng.choice(PROJECTS)
            branch = rng.choices(project.branches, [0.45] + [0.55 / (len(project.branches) - 1)]
                                 * (len(project.branches) - 1))[0]
            harness = "claude" if rng.random() < CLAUDE_SHARE else "codex"
            mode = _pick(rng, SESSION_MODES)
            if mode == "chat":
                duration = min(duration, rng.randint(5 * 60, 15 * 60))
            cursor = start_s + duration
            local = datetime.combine(day, time(0, 0), tz) + timedelta(seconds=start_s)
            plans.append(SessionPlan(local, duration, harness, project, branch, mode))
    return plans


# --------------------------------------------------------------------------
# Claude Code
# --------------------------------------------------------------------------


class _ClaudeWriter:
    """One Claude Code main transcript plus any subagent transcripts it spawns."""

    def __init__(self, rng: Random, plan: SessionPlan, root: Path) -> None:
        self.rng = rng
        self.plan = plan
        self.root = root
        self.session_id = _uuid(rng)
        self.records: list[dict[str, object]] = []
        self.subagents: list[tuple[Path, list[dict[str, object]], datetime]] = []
        self.now = plan.start
        self.end = plan.start + timedelta(seconds=plan.duration_s)
        self.parent_uuid: str | None = None
        self.model = _pick(rng, CLAUDE_SESSION_MODELS)
        self.shared_prefix = rng.randint(*CLAUDE_SHARED_PREFIX)
        self.context = rng.randint(*CLAUDE_INITIAL_CONTEXT)
        self.pending_growth = self.context  # nothing cached yet beyond the shared prefix
        self.last_call: datetime | None = None
        self.first_call = True

    # -- record plumbing ---------------------------------------------------

    def _base(self, record_type: str, sidechain: bool = False) -> dict[str, object]:
        record_uuid = _uuid(self.rng)
        base: dict[str, object] = {
            "parentUuid": self.parent_uuid,
            "isSidechain": sidechain,
            "userType": "external",
            "cwd": self.plan.project.cwd,
            "sessionId": self.session_id,
            "version": CLAUDE_VERSION,
            "gitBranch": self.plan.branch,
            "type": record_type,
            "uuid": record_uuid,
            "timestamp": _stamp(self.now),
        }
        self.parent_uuid = record_uuid
        return base

    def _advance(self, lo: int, hi: int) -> None:
        self.now += timedelta(seconds=self.rng.randint(lo, hi),
                              milliseconds=self.rng.randint(0, 999))

    def _user_text(self, text: str, **extra: object) -> None:
        record = self._base("user")
        record["message"] = {"role": "user", "content": text}
        record.update(extra)
        self.records.append(record)

    # -- token accounting ----------------------------------------------------

    def _usage(self, output: int) -> dict[str, object]:
        """Anthropic usage semantics: input excludes cache reads/writes; the
        shared prefix is always a cache read; new material since the last call
        is a cache write unless the 5-minute TTL lapsed, in which case the whole
        conversation is written again."""
        if self.first_call:
            cache_read = self.shared_prefix
            cache_creation = max(0, self.context - self.shared_prefix)
            self.first_call = False
        elif (self.last_call is not None
              and (self.now - self.last_call).total_seconds() > CACHE_TTL_S):
            cache_read = self.shared_prefix
            cache_creation = max(0, self.context - self.shared_prefix)
        else:
            cache_creation = self.pending_growth
            cache_read = max(0, self.context - cache_creation)
        self.pending_growth = 0
        self.last_call = self.now
        return {
            "input_tokens": self.rng.randint(1, 9),
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "cache_creation": {
                "ephemeral_5m_input_tokens": cache_creation,
                "ephemeral_1h_input_tokens": 0,
            },
            "output_tokens": output,
            "service_tier": "standard",
        }

    def _grow(self, tokens: int) -> None:
        self.context += tokens
        self.pending_growth += tokens

    # -- content ---------------------------------------------------------------

    def _file(self) -> str:
        return self.plan.project.cwd + "\\" + self.rng.choice(self.plan.project.files)

    def _tool_use(self, name: str) -> tuple[dict[str, object], int, int]:
        """(block, output_tokens, result_tokens) for one tool call."""
        rng = self.rng
        project = self.plan.project
        block_id = "toolu_01" + _token(rng, 22)
        tool_input: dict[str, object]
        out, result = 120, 400
        if name == "Read":
            tool_input = {"file_path": self._file()}
            result = rng.randint(400, 6000)
        elif name == "Edit":
            tool_input = {"file_path": self._file(), "old_string": "return value",
                          "new_string": "return normalise(value)"}
            out, result = rng.randint(300, 1800), 60
        elif name == "Write":
            roll = rng.random()
            if roll < 0.12:
                path = rf"C:\Users\demo\Documents\notes\{project.name}-summary.md"
            elif roll < 0.35:
                doc = rng.choice(("NOTES.md", "PLAN.md", "CHANGELOG.md"))
                path = project.cwd + "\\docs\\" + doc
            else:
                path = self._file()
            tool_input = {"file_path": path, "content": "# generated in the demo profile\n"}
            out, result = rng.randint(500, 2600), 40
        elif name == "Bash":
            command = rng.choices(
                [project.tests, "git status --short", "git diff --stat",
                 'git add -A && git commit -m "{}"'.format(rng.choice(project.prompts).rstrip("?")),
                 "git log --oneline -5", "uv run ruff check .", "npm run build",
                 "docker compose ps", "ls -la src", f"git checkout -b {self.plan.branch}"],
                [0.30, 0.12, 0.10, 0.12, 0.06, 0.08, 0.06, 0.04, 0.06, 0.06],
            )[0]
            tool_input = {"command": command, "description": "Run the project command"}
            result = rng.randint(80, 2500) if command != project.tests else rng.randint(300, 4000)
        elif name == "Grep":
            tool_input = {"pattern": rng.choice(("def sync_", "useCart", "retry", "TODO")),
                          "path": project.cwd, "output_mode": "content"}
            result = rng.randint(200, 3000)
        elif name == "Glob":
            tool_input = {"pattern": rng.choice(("src/**/*.py", "src/**/*.tsx", "tests/**"))}
            result = rng.randint(100, 900)
        elif name == "Agent":
            tool_input = {"description": "Explore the code paths",
                          "prompt": rng.choice(SUBAGENT_PROMPTS),
                          "subagent_type": rng.choice(("Explore", "general-purpose"))}
            out, result = rng.randint(200, 500), rng.randint(600, 2500)
        elif name == "WebSearch":
            tool_input = {"query": rng.choice(WEB_QUERIES)}
            result = rng.randint(1500, 5000)
        elif name == "WebFetch":
            tool_input = {"url": "https://docs.example.com/guides/" + rng.choice(
                ("retries", "migration", "indexes", "accessibility")),
                "prompt": "Extract the recommended approach"}
            result = rng.randint(1000, 4000)
        elif name == "Skill":
            tool_input = {"skill": rng.choice(CLAUDE_SKILLS)}
            result = rng.randint(800, 3000)
        elif name == "TodoWrite":
            tool_input = {"todos": [
                {"content": "Read the current implementation", "status": "completed",
                 "activeForm": "Reading the current implementation"},
                {"content": "Apply the fix", "status": "in_progress",
                 "activeForm": "Applying the fix"},
                {"content": "Run the tests", "status": "pending",
                 "activeForm": "Running the tests"},
            ]}
            out, result = rng.randint(150, 400), 60
        elif name == "AskUserQuestion":
            tool_input = {"questions": [{"question": "Keep the old endpoint as an alias?",
                                         "header": "Compatibility",
                                         "options": [{"label": "Yes"}, {"label": "No"}]}]}
            out, result = rng.randint(150, 400), 30
        elif name in ("EnterPlanMode", "ExitPlanMode"):
            tool_input = {} if name == "EnterPlanMode" else {"plan": "1. Read 2. Change 3. Test"}
            out, result = rng.randint(80, 900), 40
        elif name == "Monitor":
            tool_input = {"command": "npm run build -- --watch", "until": "built in"}
            out, result = 90, rng.randint(50, 400)
        elif name == "mcp__Claude_Browser__navigate":
            tool_input = {"url": "http://localhost:5173/checkout"}
            result = 120
        elif name == "mcp__Claude_Browser__computer":
            tool_input = {"action": rng.choice(("screenshot", "left_click", "scroll")),
                          "coordinate": [640, 400]}
            result = rng.randint(200, 1500)
        elif name == "mcp__github__search_issues":
            tool_input = {"query": f"repo:demo/{project.name} is:open label:bug"}
            result = rng.randint(800, 3000)
        elif name == "mcp__github__get_pull_request":
            tool_input = {"owner": "demo", "repo": project.name,
                          "pull_number": rng.randint(40, 260)}
            result = rng.randint(800, 4000)
        elif name == "mcp__postgres__query":
            tool_input = {"sql": "select count(*) from ledger_entries where retry_state is null"}
            result = rng.randint(60, 1200)
        elif name == "mcp__playwright__browser_navigate":
            tool_input = {"url": "http://localhost:5173/"}
            result = rng.randint(300, 2500)
        elif name == "mcp__playwright__browser_snapshot":
            tool_input = {}
            result = rng.randint(1500, 6000)
        else:
            tool_input = {}
        block = {"type": "tool_use", "id": block_id, "name": name, "input": tool_input}
        return block, out, result

    def _tool_names(self, count: int) -> list[str]:
        rng = self.rng
        project = self.plan.project
        if self.plan.mode == "investigate":
            names = ["Read", "Bash", "Grep", "Glob", "TodoWrite"]
            weights = [0.36, 0.22, 0.24, 0.10, 0.03]
        else:
            names = ["Read", "Edit", "Bash", "Grep", "Glob", "Write", "TodoWrite"]
            weights = [0.24, 0.20, 0.20, 0.10, 0.05, 0.05, 0.04]
        extras = {
            "Agent": 0.007, "WebSearch": 0.02, "WebFetch": 0.015, "Skill": 0.02,
            "AskUserQuestion": 0.008, "EnterPlanMode": 0.006, "ExitPlanMode": 0.006,
            "Monitor": 0.003,
        }
        if project.name == "storefront-web":
            extras.update({"mcp__Claude_Browser__navigate": 0.02,
                           "mcp__Claude_Browser__computer": 0.04,
                           "mcp__playwright__browser_navigate": 0.015,
                           "mcp__playwright__browser_snapshot": 0.02})
        if "postgres" in project.mcp:
            extras["mcp__postgres__query"] = 0.03
        if "github" in project.mcp:
            extras["mcp__github__search_issues"] = 0.012
            extras["mcp__github__get_pull_request"] = 0.012
        names += list(extras)
        weights += list(extras.values())
        return rng.choices(names, weights, k=count)

    def _assistant(self, content: list[dict[str, object]], output: int,
                   stop_reason: str, streamed: bool) -> None:
        """One API call. ``streamed`` writes the text block first and the
        full message second under the same message id + requestId with
        cumulative usage — the shape the parser deduplicates last-wins."""
        message_id = "msg_01" + _token(self.rng, 22)
        request_id = "req_011" + _token(self.rng, 21)
        usage = self._usage(output)
        if streamed and len(content) > 1 and content[0].get("type") == "text":
            partial_usage = dict(usage)
            partial_usage["output_tokens"] = max(1, output // 3)
            record = self._base("assistant")
            record["message"] = {
                "id": message_id, "type": "message", "role": "assistant", "model": self.model,
                "content": [content[0]], "stop_reason": None, "stop_sequence": None,
                "usage": partial_usage,
            }
            record["requestId"] = request_id
            self.records.append(record)
            self._advance(0, 3)
            content = content[1:]
        record = self._base("assistant")
        record["message"] = {
            "id": message_id, "type": "message", "role": "assistant", "model": self.model,
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": usage,
        }
        record["requestId"] = request_id
        self.records.append(record)
        self._grow(output)

    def _api_error(self) -> None:
        record = self._base("assistant")
        record["isApiErrorMessage"] = True
        record["message"] = {
            "id": "msg_01" + _token(self.rng, 22), "type": "message", "role": "assistant",
            "model": self.model,
            "content": [{"type": "text", "text": "API Error: 529 overloaded_error, retrying"}],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 3, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": 4, "service_tier": "standard"},
        }
        record["requestId"] = "req_011" + _token(self.rng, 21)
        self.records.append(record)
        self._advance(3, 20)

    def _tool_results(self, blocks: list[dict[str, object]], result_tokens: list[int]) -> None:
        rng = self.rng
        for block, tokens in zip(blocks, result_tokens, strict=True):
            name = str(block["name"])
            if name == "Agent":
                self._spawn_subagent(str(block["input"]["prompt"]))  # type: ignore[index]
                self._advance(40, 240)
            elif name == "Bash":
                command = str(block["input"]["command"])  # type: ignore[index]
                self._advance(6, 60) if command == self.plan.project.tests else self._advance(1, 5)
            elif name.startswith("mcp__") or name in ("WebSearch", "WebFetch"):
                self._advance(1, 9)
            else:
                self._advance(0, 2)
            is_error = False
            if (name == "Bash" and rng.random() < 0.12) or (
                    name in ("Edit", "Read") and rng.random() < 0.03):
                is_error = True
            result_block: dict[str, object] = {
                "tool_use_id": block["id"], "type": "tool_result",
                "content": "Error: command exited with status 1" if is_error else "ok",
            }
            if is_error:
                result_block["is_error"] = True
            record = self._base("user")
            record["message"] = {"role": "user", "content": [result_block]}
            if name == "Edit":
                record["toolUseResult"] = {
                    "filePath": block["input"]["file_path"],  # type: ignore[index]
                    "oldString": "return value", "newString": "return normalise(value)",
                    "originalFile": "", "structuredPatch": [], "replaceAll": False,
                    "userModified": rng.random() < 0.03,
                }
            elif name == "Bash":
                record["toolUseResult"] = {"stdout": "", "stderr": "", "interrupted": False,
                                           "isImage": False}
            elif name == "Read":
                file_path = block["input"]["file_path"]  # type: ignore[index]
                record["toolUseResult"] = {"type": "text", "file": {"filePath": file_path,
                                                                     "numLines": tokens // 12}}
            else:
                record["toolUseResult"] = "ok"
            self.records.append(record)
            self._grow(tokens + 40)

    def _spawn_subagent(self, prompt: str) -> None:
        rng = self.rng
        agent_id = _hex(rng, 7)
        model = _pick(rng, CLAUDE_SUBAGENT_MODELS)
        parent: str | None = None
        now = self.now + timedelta(seconds=rng.randint(2, 6))
        records: list[dict[str, object]] = []
        context = rng.randint(14_000, 30_000)
        shared = rng.randint(*CLAUDE_SHARED_PREFIX)
        pending = context - shared

        def base(record_type: str) -> dict[str, object]:
            nonlocal parent
            record_uuid = _uuid(rng)
            base: dict[str, object] = {
                "parentUuid": parent, "isSidechain": True, "userType": "external",
                "cwd": self.plan.project.cwd, "sessionId": self.session_id,
                "version": CLAUDE_VERSION, "gitBranch": self.plan.branch,
                "type": record_type, "uuid": record_uuid, "timestamp": _stamp(now),
            }
            parent = record_uuid
            return base

        record = base("user")
        record["message"] = {"role": "user", "content": prompt}
        records.append(record)
        calls = rng.randint(4, 14)
        for index in range(calls):
            now += timedelta(seconds=rng.randint(3, 18))
            last = index == calls - 1
            output = rng.randint(120, 700) if last else rng.randint(60, 300)
            if last:
                content: list[dict[str, object]] = [
                    {"type": "text",
                     "text": "Findings: three call sites, listed with file and line."}]
                growth = 0
            else:
                name = rng.choices(["Read", "Grep", "Glob", "Bash"], [0.45, 0.3, 0.15, 0.1])[0]
                block, _, growth = self._tool_use(name)
                content = [block]
            cache_creation = pending
            cache_read = max(0, context - cache_creation)
            pending = 0
            record = base("assistant")
            record["message"] = {
                "id": "msg_01" + _token(rng, 22), "type": "message", "role": "assistant",
                "model": model, "content": content,
                "stop_reason": "end_turn" if last else "tool_use", "stop_sequence": None,
                "usage": {"input_tokens": rng.randint(1, 9),
                          "cache_creation_input_tokens": cache_creation,
                          "cache_read_input_tokens": cache_read,
                          "cache_creation": {"ephemeral_5m_input_tokens": cache_creation,
                                             "ephemeral_1h_input_tokens": 0},
                          "output_tokens": output, "service_tier": "standard"},
            }
            record["requestId"] = "req_011" + _token(rng, 21)
            records.append(record)
            context += output
            pending += output
            if not last:
                now += timedelta(seconds=rng.randint(0, 4))
                record = base("user")
                record["message"] = {"role": "user", "content": [
                    {"tool_use_id": content[0]["id"], "type": "tool_result", "content": "ok"}]}
                record["toolUseResult"] = "ok"
                records.append(record)
                context += growth
                pending += growth
        path = (self.root / "projects" / _slug(self.plan.project.cwd) / self.session_id
                / "subagents" / f"agent-{agent_id}.jsonl")
        self.subagents.append((path, records, now))

    def _compact(self) -> None:
        record = self._base("user")
        record["isCompactSummary"] = True
        record["message"] = {
            "role": "user",
            "content": "This session is being continued from a previous conversation that ran "
                       "out of context. The summary below covers the work so far.",
        }
        self.records.append(record)
        self.context = self.rng.randint(22_000, 36_000)
        self.pending_growth = self.context - self.shared_prefix
        self._advance(15, 45)

    # -- the session -----------------------------------------------------------

    def run(self) -> None:
        rng = self.rng
        project = self.plan.project
        self.records.append({
            "type": "file-history-snapshot",
            "messageId": _uuid(rng),
            "snapshot": {"messageId": _uuid(rng), "trackedFileBackups": {},
                         "timestamp": _stamp(self.now)},
            "isSnapshotUpdate": False,
        })
        prompt = rng.choice(project.prompts)
        prompts = 0
        while prompts == 0 or (self.now < self.end and prompts < 60):
            self._user_text(prompt)
            prompts += 1
            self._grow(len(prompt) // 4 + 20)
            self._advance(2, 9)
            calls = 1 if self.plan.mode == "chat" else _calls(rng)
            for index in range(calls):
                if self.now >= self.end + timedelta(minutes=8):
                    break
                if rng.random() < 0.008:
                    self._api_error()
                last = index == calls - 1
                if last:
                    text = rng.choice(CLAUDE_TEXTS)
                    output = rng.randint(200, 2500)
                    self._assistant([{"type": "text", "text": text}], output, "end_turn", False)
                    break
                count = rng.choices([1, 2, 3], [0.72, 0.2, 0.08])[0]
                blocks: list[dict[str, object]] = []
                results: list[int] = []
                output = 0
                for name in self._tool_names(count):
                    block, out, result = self._tool_use(name)
                    blocks.append(block)
                    results.append(result)
                    output += out
                if rng.random() < 0.85:
                    output += rng.randint(800, 9000)  # adaptive thinking on a hard task
                content: list[dict[str, object]] = []
                if rng.random() < 0.4:
                    content.append({"type": "text", "text": rng.choice(CLAUDE_TEXTS)})
                content.extend(blocks)
                self._advance(2, 11)
                self._assistant(content, output, "tool_use", streamed=rng.random() < 0.5)
                self._tool_results(blocks, results)
                if self.context > COMPACT_AT_TOKENS:
                    self._compact()
            if rng.random() < 0.04:
                self._advance(5, 40)
                self._user_text("[Request interrupted by user]")
            self.now += _pause(rng)
            prompt = (rng.choice(FOLLOW_UPS_SHORT) if rng.random() < 0.3
                      else rng.choice(FOLLOW_UPS_LONG))
        self.records.append({"type": "summary", "summary": rng.choice(project.prompts),
                             "leafUuid": self.parent_uuid})

    def write(self, summary: Summary) -> None:
        path = self.root / "projects" / _slug(self.plan.project.cwd) / f"{self.session_id}.jsonl"
        _write_jsonl(path, self.records, self.now)
        summary.files.append(path)
        summary.claude_sessions += 1
        for sub_path, records, ended in self.subagents:
            _write_jsonl(sub_path, records, ended)
            summary.files.append(sub_path)
            summary.claude_subagents += 1


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


@dataclass
class _CodexQuota:
    """Deterministic provider rate-limit gauge: tokens in the trailing 5-hour
    and 7-day windows, scaled to plausible plan limits."""

    events: list[tuple[datetime, int]] = field(default_factory=list)
    primary_capacity: int = 1_200_000
    secondary_capacity: int = 6_500_000

    def add(self, when: datetime, tokens: int) -> None:
        self.events.append((when, tokens))

    def reading(self, when: datetime) -> dict[str, object]:
        five_h = when - timedelta(hours=5)
        week = when - timedelta(days=7)
        primary = sum(t for ts, t in self.events if ts > five_h)
        secondary = sum(t for ts, t in self.events if ts > week)
        primary_pct = min(97.0, round(100.0 * primary / self.primary_capacity, 1))
        secondary_pct = min(98.5, round(100.0 * secondary / self.secondary_capacity, 1))
        # Windows reset on a fixed grid: 5-hour blocks, and weekly on Monday 00:00 UTC.
        utc = when.astimezone(UTC)
        block_start = utc.replace(minute=0, second=0, microsecond=0)
        block_start -= timedelta(hours=block_start.hour % 5)
        primary_reset = int((block_start + timedelta(hours=5)).timestamp())
        monday = (utc - timedelta(days=utc.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        secondary_reset = int((monday + timedelta(days=7)).timestamp())
        return {
            "limit_id": "codex",
            "primary": {"used_percent": primary_pct, "window_minutes": 300,
                        "resets_at": primary_reset},
            "secondary": {"used_percent": secondary_pct, "window_minutes": 10080,
                          "resets_at": secondary_reset},
        }


class _CodexWriter:
    def __init__(self, rng: Random, plan: SessionPlan, root: Path, quota: _CodexQuota) -> None:
        self.rng = rng
        self.plan = plan
        self.root = root
        self.quota = quota
        self.session_id = _uuid(rng)
        self.records: list[dict[str, object]] = []
        self.now = plan.start
        self.end = plan.start + timedelta(seconds=plan.duration_s)
        self.model = _pick(rng, CODEX_SESSION_MODELS)
        self.effort = _pick(rng, CODEX_EFFORTS)
        self.context = rng.randint(*CODEX_INITIAL_CONTEXT)
        self.uncached_pending = self.context
        self.total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                      "reasoning_output_tokens": 0, "total_tokens": 0}

    def _emit(self, record_type: str, payload: dict[str, object]) -> None:
        self.records.append({"timestamp": _stamp(self.now), "type": record_type,
                             "payload": payload})

    def _advance(self, lo: int, hi: int) -> None:
        self.now += timedelta(seconds=self.rng.randint(lo, hi),
                              milliseconds=self.rng.randint(0, 999))

    def _turn_context(self) -> None:
        self._emit("turn_context", {
            "cwd": self.plan.project.cwd,
            "approval_policy": "on-request",
            "sandbox_policy": {"mode": "workspace-write", "network_access": False},
            "model": self.model,
            "effort": self.effort,
            "summary": "auto",
        })

    def _token_count(self, output: int, reasoning: int) -> None:
        """Cumulative totals plus the last call's usage (OpenAI semantics:
        cached is a subset of input)."""
        cached = max(0, self.context - self.uncached_pending)
        last = {
            "input_tokens": self.context,
            "cached_input_tokens": cached,
            "output_tokens": output,
            "reasoning_output_tokens": reasoning,
            "total_tokens": self.context + output,
        }
        for key, value in last.items():
            self.total[key] += value
        self.quota.add(self.now, self.uncached_pending + output)
        self.uncached_pending = 0
        self.context += output
        self.uncached_pending += output
        self._emit("event_msg", {
            "type": "token_count",
            "info": {"total_token_usage": dict(self.total), "last_token_usage": last,
                     "model_context_window": 272_000},
            "rate_limits": self.quota.reading(self.now),
        })
        if self.rng.random() < 0.15:  # the CLI repeats a reading now and then
            self._advance(0, 2)
            self._emit("event_msg", {
                "type": "token_count",
                "info": {"total_token_usage": dict(self.total), "last_token_usage": last,
                         "model_context_window": 272_000},
                "rate_limits": self.quota.reading(self.now),
            })

    def _grow(self, tokens: int) -> None:
        self.context += tokens
        self.uncached_pending += tokens

    def _step(self) -> None:
        """One model response that calls a tool, with its output and usage."""
        rng = self.rng
        project = self.plan.project
        call_id = "call_" + _token(rng, 24)
        self._emit("response_item", {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "**Checking the current behaviour**"}],
            "content": None,
        })
        if self.plan.mode == "investigate":
            kinds, weights = ["shell", "plan", "web", "mcp", "view"], [0.74, 0.06, 0.06, 0.10, 0.04]
        else:
            kinds = ["shell", "patch", "plan", "web", "mcp", "view"]
            weights = [0.56, 0.24, 0.06, 0.04, 0.07, 0.03]
        kind = rng.choices(kinds, weights)[0]
        output = rng.randint(80, 400)
        reasoning = rng.randint(200, 2600) if self.effort != "medium" else rng.randint(50, 900)
        result_tokens = 300
        if kind == "shell":
            command = rng.choices(
                [project.tests, "git status --short", "git diff", "rg -n 'retry' src",
                 'git add -A && git commit -m "{}"'.format(rng.choice(project.prompts).rstrip("?")),
                 "sed -n '1,120p' {}".format(rng.choice(project.files).replace("\\", "/")),
                 "ls src", "npm run build"],
                [0.30, 0.12, 0.10, 0.12, 0.10, 0.14, 0.06, 0.06],
            )[0]
            self._emit("response_item", {
                "type": "function_call", "name": "shell_command",
                "arguments": json.dumps({"command": command, "workdir": project.cwd,
                                         "timeout_ms": 120000}),
                "call_id": call_id,
            })
            self._advance(6, 60) if command == project.tests else self._advance(1, 5)
            failed = rng.random() < (0.14 if command == project.tests else 0.04)
            body = ("execution error: exit code 1\n2 failed, 41 passed\n" if failed
                    else "Total output lines: 12\n\nok\n")
            self._emit("response_item", {"type": "function_call_output", "call_id": call_id,
                                         "output": body})
            result_tokens = rng.randint(100, 3000)
        elif kind == "patch":
            adds = rng.random() < 0.15
            target = rng.choice(project.files).replace("\\", "/")
            patch = ("*** Begin Patch\n"
                     + (f"*** Add File: {target}\n+# new module\n" if adds
                        else f"*** Update File: {target}\n@@\n-    return value\n"
                        "+    return normalise(value)\n")
                     + "*** End Patch")
            self._emit("response_item", {"type": "custom_tool_call", "status": "completed",
                                         "call_id": call_id, "name": "apply_patch", "input": patch})
            self._advance(0, 3)
            success = rng.random() > 0.05
            self._emit("event_msg", {"type": "patch_apply_end", "call_id": call_id,
                                     "stdout": ("Success. Updated the following files:\n"
                                                if success else ""),
                                     "stderr": "" if success else "patch failed to apply",
                                     "success": success})
            self._emit("response_item", {"type": "custom_tool_call_output", "call_id": call_id,
                                         "output": (
                                             f"Success. Updated the following files:\nM {target}\n"
                                             if success else "execution error: patch rejected\n")})
            output = rng.randint(300, 1900)
            result_tokens = 60
        elif kind == "plan":
            self._emit("response_item", {
                "type": "function_call", "name": "update_plan",
                "arguments": json.dumps({"plan": [
                    {"step": "Read the failing test", "status": "completed"},
                    {"step": "Fix the retry loop", "status": "in_progress"},
                    {"step": "Run the suite", "status": "pending"}]}),
                "call_id": call_id,
            })
            self._emit("response_item", {"type": "function_call_output", "call_id": call_id,
                                         "output": "Plan updated"})
            result_tokens = 40
        elif kind == "web":
            self._emit("response_item", {
                "type": "web_search_call", "status": "completed",
                "action": {"type": "search", "query": rng.choice(WEB_QUERIES)},
            })
            self._advance(2, 9)
            result_tokens = rng.randint(1000, 4000)
        elif kind == "mcp":
            server = rng.choice(project.mcp)
            tool = {"github": "search_issues", "postgres": "query",
                    "playwright": "browser_snapshot"}[server]
            arguments = {"github": {"query": f"repo:demo/{project.name} is:open"},
                         "postgres": {"sql": "select count(*) from ledger_entries"},
                         "playwright": {}}[server]
            self._emit("event_msg", {"type": "mcp_tool_call_begin", "call_id": call_id,
                                     "invocation": {"server": server, "tool": tool,
                                                    "arguments": arguments}})
            self._emit("response_item", {
                "type": "function_call", "name": f"mcp__{server}__{tool}",
                "arguments": json.dumps(arguments), "call_id": call_id,
            })
            self._advance(1, 12)
            self._emit("event_msg", {"type": "mcp_tool_call_end", "call_id": call_id,
                                     "invocation": {"server": server, "tool": tool,
                                                    "arguments": arguments},
                                     "duration": {"secs": rng.randint(0, 6), "nanos": 0},
                                     "result": {"Ok": {"content": [
                                         {"type": "text", "text": "ok"}]}}})
            self._emit("response_item", {"type": "function_call_output", "call_id": call_id,
                                         "output": json.dumps(
                                             {"content": [{"type": "text", "text": "ok"}]},
                                             separators=(",", ":"))})
            result_tokens = rng.randint(200, 2500)
        else:  # view_image
            self._emit("response_item", {
                "type": "function_call", "name": "view_image",
                "arguments": json.dumps({"path": project.cwd + r"\docs\screenshot.png"}),
                "call_id": call_id,
            })
            self._emit("response_item", {"type": "function_call_output", "call_id": call_id,
                                         "output": "attached local image path"})
            result_tokens = rng.randint(800, 1600)
        self._advance(0, 2)
        self._token_count(output + reasoning, reasoning)
        self._grow(result_tokens)

    def run(self) -> None:
        rng = self.rng
        project = self.plan.project
        self._emit("session_meta", {
            "id": self.session_id,
            "timestamp": _stamp(self.now),
            "cwd": project.cwd,
            "originator": "codex_cli_rs",
            "cli_version": CODEX_VERSION,
            "instructions": None,
            "source": "cli",
            "model_provider": "openai",
            "git": {
                "commit_hash": _hex(rng, 40),
                "branch": self.plan.branch,
                "repository_url": f"https://git.example.com/demo/{project.name}.git",
            },
        })
        self._advance(0, 1)
        self._turn_context()
        # Context injections ride user-role messages WITHOUT a user_message event.
        self._emit("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text",
             "text": f"<environment_context>\n  <cwd>{project.cwd}</cwd>\n"
                     "  <shell>powershell</shell>\n</environment_context>"}]})
        prompt = rng.choice(project.prompts)
        prompts = 0
        while prompts == 0 or (self.now < self.end and prompts < 60):
            if prompts:
                self._turn_context()
            self._emit("response_item", {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": prompt}]})
            self._emit("event_msg", {"type": "user_message", "message": prompt, "images": [],
                                     "local_images": [], "text_elements": []})
            self._emit("event_msg", {"type": "task_started", "model_context_window": 272_000})
            prompts += 1
            self._grow(len(prompt) // 4 + 20)
            self._advance(2, 9)
            steps = 0 if self.plan.mode == "chat" else _calls(rng) - 1
            aborted = False
            for _ in range(steps):
                if self.now >= self.end + timedelta(minutes=8):
                    break
                if rng.random() < 0.006:
                    self._emit("event_msg", {"type": "stream_error",
                                             "message": "stream disconnected before completion"})
                    self._advance(2, 12)
                self._advance(2, 10)
                self._step()
                if rng.random() < 0.012:
                    self._emit("turn_aborted", {"reason": "user_interrupt"})
                    aborted = True
                    break
                if self.context > COMPACT_AT_TOKENS:
                    self._emit("compacted", {"message": "Conversation history was compacted."})
                    self.context = rng.randint(20_000, 34_000)
                    self.uncached_pending = self.context
                    self._advance(10, 30)
            if not aborted:
                self._advance(3, 20)
                text = rng.choice(CODEX_TEXTS)
                self._emit("response_item", {"type": "message", "role": "assistant",
                                             "content": [{"type": "output_text", "text": text}]})
                self._emit("event_msg", {"type": "agent_message", "message": text})
                self._token_count(rng.randint(80, 900), rng.randint(0, 300))
                self._emit("event_msg", {"type": "task_complete", "last_agent_message": text})
            self.now += _pause(rng)
            prompt = (rng.choice(FOLLOW_UPS_SHORT) if rng.random() < 0.3
                      else rng.choice(FOLLOW_UPS_LONG))

    def write(self, summary: Summary) -> None:
        utc = self.plan.start.astimezone(UTC)
        path = (self.root / "sessions" / f"{utc:%Y}" / f"{utc:%m}" / f"{utc:%d}"
                / f"rollout-{utc:%Y-%m-%dT%H-%M-%S}-{self.session_id}.jsonl")
        _write_jsonl(path, self.records, self.now)
        summary.files.append(path)
        summary.codex_sessions += 1


# --------------------------------------------------------------------------
# Config files the inventory pages read (names only, nothing personal)
# --------------------------------------------------------------------------


def _write_text(path: Path, text: str, mtime: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    epoch = mtime.astimezone(UTC).timestamp()
    os.utime(path, (epoch, epoch))


def write_config(out: Path, mtime: datetime) -> list[Path]:
    written: list[Path] = []
    claude_root = out / "claude"
    codex_root = out / "codex"
    claude_json = out / ".claude.json"
    _write_text(claude_json, json.dumps({
        "mcpServers": {
            "github": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
            "postgres": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
        },
        "projects": {
            PROJECTS[1].cwd: {"mcpServers": {
                "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]}}},
        },
    }, indent=2) + "\n", mtime)
    written.append(claude_json)
    settings = claude_root / "settings.json"
    _write_text(settings, json.dumps({"model": "sonnet"}, indent=2) + "\n", mtime)
    written.append(settings)
    manifest = claude_root / "plugins" / "installed_plugins.json"
    _write_text(manifest, json.dumps({
        "version": 2,
        "plugins": {
            "code-review@demo-marketplace": [{"scope": "user", "version": "1.4.0"}],
            "frontend-design@demo-marketplace": [{"scope": "user", "version": "0.9.2"}],
        },
    }, indent=2) + "\n", mtime)
    written.append(manifest)
    descriptions = {
        "code-review": "Review the current diff for correctness bugs and simplification "
                       "opportunities.",
        "release-notes": "Draft release notes from the commits since the last tag.",
        "db-migration": "Write and check an Alembic migration for a schema change.",
        "commit": "Stage the related changes and write a conventional commit message.",
    }
    for name in CLAUDE_SKILLS:
        path = claude_root / "skills" / name / "SKILL.md"
        _write_text(path, f"---\nname: {name}\ndescription: {descriptions[name]}\n---\n\n"
                    f"# {name}\n\nDemo skill body.\n", mtime)
        written.append(path)
    for name in CODEX_SKILLS:
        path = codex_root / "skills" / name / "SKILL.md"
        _write_text(path, f"---\nname: {name}\ndescription: {descriptions[name]}\n---\n\n"
                    f"# {name}\n\nDemo skill body.\n", mtime)
        written.append(path)
    config_toml = codex_root / "config.toml"
    _write_text(config_toml, (
        'model = "gpt-5.6-terra"\n'
        'model_reasoning_effort = "medium"\n'
        'approval_policy = "on-request"\n'
        'sandbox_mode = "workspace-write"\n'
        "\n"
        "[mcp_servers.github]\n"
        'command = "npx"\n'
        'args = ["-y", "@modelcontextprotocol/server-github"]\n'
        "\n"
        "[mcp_servers.postgres]\n"
        'command = "npx"\n'
        'args = ["-y", "@modelcontextprotocol/server-postgres"]\n'
        "\n"
        "[mcp_servers.playwright]\n"
        'command = "npx"\n'
        'args = ["-y", "@playwright/mcp@latest"]\n'
    ), mtime)
    written.append(config_toml)
    return written


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------


def generate(out: Path, days: int = 35, seed: int = 7, end: date | None = None,
             tz_name: str = DEFAULT_TZ, end_hour: int | None = None) -> Summary:
    """Write the demo profile under ``out`` and return what was written."""
    if end is None:
        end = date(2026, 9, 16)
    rng = Random(seed)
    tz = ZoneInfo(tz_name)
    plans = plan_days(rng, end, days, tz, end_hour)
    summary = Summary()
    quota = _CodexQuota()
    claude_root = out / "claude"
    codex_root = out / "codex"
    for plan in plans:
        if plan.harness == "claude":
            writer = _ClaudeWriter(rng, plan, claude_root)
            writer.run()
            writer.write(summary)
        else:
            codex = _CodexWriter(rng, plan, codex_root, quota)
            codex.run()
            codex.write(summary)
    summary.files.extend(write_config(out, plans[-1].start if plans else datetime.now(UTC)))
    if plans:
        summary.first_day = plans[0].start.date().isoformat()
        summary.last_day = plans[-1].start.date().isoformat()
    return summary


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16),
                        help="last day of activity (YYYY-MM-DD)")
    parser.add_argument("--tz", default=DEFAULT_TZ, help="IANA zone the developer works in")
    parser.add_argument("--end-hour", type=int, default=None, choices=range(1, 24),
                        metavar="H", help="no activity on the last day at or after this local hour")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = generate(args.out, args.days, args.seed, args.end, args.tz, args.end_hour)
    print(f"claude sessions: {summary.claude_sessions} "
          f"(+{summary.claude_subagents} subagent transcripts)")
    print(f"codex sessions:  {summary.codex_sessions}")
    print(f"files written:   {len(summary.files)} under {args.out}")
    print(f"days:            {summary.first_day} .. {summary.last_day}")
    print("point the engine at it with:")
    print(f"  PRACTICEGRAPH_CLAUDE_HOME={args.out / 'claude'}")
    print(f"  PRACTICEGRAPH_CODEX_HOME={args.out / 'codex'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
