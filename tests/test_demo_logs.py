"""tools/demo_logs writes parser-shaped synthetic logs: deterministic, ingestible
by both source adapters without drift, priced by the bundled rate card, and
free of anything that could identify a real person or machine."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

import pytest
import tools.demo_logs as demo

from practicegraph.analysis.ratecard import rate_for_model
from practicegraph.events import TurnKind
from practicegraph.sources.claude_code import ADAPTER as CLAUDE
from practicegraph.sources.codex import ADAPTER as CODEX

SEED = 7
END = date(2026, 9, 16)
DAYS = 35

# Shapes that must never appear in a public demo profile: credential prefixes,
# real-looking e-mail domains, and any home directory other than the demo one.
# The generator is the only source of identity here, so the checks are
# structural rather than a list of real names (a public test file must not
# carry a maintainer's own identifiers either).
DENY_LIST = ("sk-", "ghp_", "AKIA", "BEGIN PRIVATE KEY", "@gmail.", "@outlook.")
DEMO_HOME = "users\\demo\\"


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def profile(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, demo.Summary]:
    out = tmp_path_factory.mktemp("demo-profile")
    return out, demo.generate(out, days=DAYS, seed=SEED, end=END)


def _env(out: Path) -> dict[str, str]:
    return {
        "PRACTICEGRAPH_CLAUDE_HOME": str(out / "claude"),
        "PRACTICEGRAPH_CODEX_HOME": str(out / "codex"),
    }


def test_same_seed_writes_identical_files(tmp_path: Path) -> None:
    first = demo.generate(tmp_path / "a", days=10, seed=SEED, end=END)
    second = demo.generate(tmp_path / "b", days=10, seed=SEED, end=END)
    assert _digests(tmp_path / "a") == _digests(tmp_path / "b")
    assert (first.claude_sessions, first.codex_sessions) == (
        second.claude_sessions, second.codex_sessions)
    # A different seed is a different developer, not a reshuffle of file names.
    demo.generate(tmp_path / "c", days=10, seed=SEED + 1, end=END)
    assert _digests(tmp_path / "c") != _digests(tmp_path / "a")


def test_claude_code_parser_ingests_the_profile(profile: tuple[Path, demo.Summary]) -> None:
    out, summary = profile
    result = CLAUDE.parse_many(CLAUDE.discover(_env(out)))
    health = result.health
    assert health.malformed == 0
    assert health.unknown_field == 0
    assert health.unsupported == 0
    sessions = {event.session_id for event in result.events}
    assert len(sessions) >= 50
    assert len(sessions) == summary.claude_sessions  # subagents ride the parent's session
    assistants = [e for e in result.events if e.kind is TurnKind.ASSISTANT_TURN]
    assert sum(1 for e in result.events if e.human_initiated) >= summary.claude_sessions
    assert any(not e.interactive for e in assistants)  # sidechain transcripts present
    assert any(e.compactions for e in result.events)
    assert sum(e.git_commit_attempts for e in assistants) > 0
    assert sum(e.test_run_attempts for e in assistants) > 0
    assert sum(e.files_edited for e in assistants) > 0
    assert sum(e.export_writes for e in assistants) > 0
    assert sum(e.commands_failed for e in result.events) > 0
    for model in {e.model for e in assistants}:
        assert rate_for_model(model) is not None, model
    assert all(e.tokens.cached > 0 or e.tokens.cache_creation > 0
               for e in assistants if not e.retries)


def test_codex_parser_ingests_the_profile(profile: tuple[Path, demo.Summary]) -> None:
    out, summary = profile
    result = CODEX.parse_many(CODEX.discover(_env(out)))
    health = result.health
    assert health.malformed == 0
    assert health.unknown_field == 0
    assert health.unsupported == 0
    sessions = {event.session_id for event in result.events}
    assert len(sessions) >= 50
    assert len(sessions) == summary.codex_sessions
    assistants = [e for e in result.events if e.kind is TurnKind.ASSISTANT_TURN]
    assert assistants
    assert sum(1 for e in result.events if e.human_initiated) >= summary.codex_sessions
    assert sum(e.tool_calls for e in assistants) > 0
    assert sum(e.files_edited for e in assistants) > 0
    assert sum(e.git_commit_attempts for e in assistants) > 0
    assert sum(e.commands_failed for e in assistants) > 0
    for model in {e.model for e in assistants}:
        assert rate_for_model(model) is not None, model
    # The usage-limits card: both rate-limit windows, with reset times.
    kinds = {snapshot.window_kind for snapshot in result.quota_snapshots}
    assert kinds == {"primary", "secondary"}
    assert all(snapshot.resets_at for snapshot in result.quota_snapshots)
    assert max(s.used_pct_tenths for s in result.quota_snapshots) <= 1000


def test_activity_shape_is_a_working_developer(profile: tuple[Path, demo.Summary]) -> None:
    out, summary = profile
    assert 120 <= summary.claude_sessions + summary.codex_sessions <= 200
    days = {p.parent.name for p in (out / "codex" / "sessions").rglob("*.jsonl")}
    assert len(days) >= 20  # codex alone is active most days of the window
    # Every log lands inside the window and is stamped with a current version.
    for path in (out / "claude" / "projects").rglob("*.jsonl"):
        first = path.read_text(encoding="utf-8").splitlines()[1]
        assert f'"version":"{demo.CLAUDE_VERSION}"' in first
    assert demo.CODEX_VERSION in next((out / "codex" / "sessions").rglob("*.jsonl")).read_text(
        encoding="utf-8").splitlines()[0]


def test_no_denied_strings_in_any_generated_file(profile: tuple[Path, demo.Summary]) -> None:
    out, _ = profile
    hits: list[str] = []
    for path in sorted(out.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        for needle in DENY_LIST:
            if needle.lower() in lowered:
                hits.append(f"{path.relative_to(out).as_posix()}: {needle}")
    assert hits == []
    # The only home directory anywhere in the profile is the demo user's
    # (JSON doubles the backslash, so accept one or two).
    foreign_home = re.compile(r"users\\+(?![\\]*demo\\)", re.IGNORECASE)
    for path in sorted(out.rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert foreign_home.search(text) is None, path.relative_to(out).as_posix()
            assert DEMO_HOME in text.lower().replace("\\\\", "\\") or "users" not in text.lower()
