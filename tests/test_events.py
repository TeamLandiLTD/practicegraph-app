"""Closed-vocabulary pins (NFR-PRV-2) and event-model invariants (FR-SRC-4)."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta, timezone

import pytest

from practicegraph.events import (
    CapabilityClass,
    EngagementCounter,
    TokenCounts,
    Tool,
    TurnEvent,
    TurnKind,
)


def test_capability_classes_are_pinned() -> None:
    assert [m.value for m in CapabilityClass] == [
        "deep_session_log",
        "telemetry_stream",
        "admin_compliance_feed",
        "quota_billing_feed",
        "export_importer",
        "local_app_cache_probe",
    ]


def test_engagement_counters_are_pinned() -> None:
    assert [m.value for m in EngagementCounter] == [
        "report_generated",
        "tip_shown",
        "tip_acted",
        "recommendation_dismissed",
        "recommendation_never_suggest",
        "engagement_unavailable",
    ]


def test_tools_are_pinned() -> None:
    assert [m.value for m in Tool] == ["claude_code", "codex"]
    assert [m.value for m in TurnKind] == ["user_turn", "assistant_turn"]


def test_turn_event_structure_is_pinned() -> None:
    """The event model carries counters, enums, and presence flags only.

    Adding a field here is a reviewed contract change: raw content, paths, and
    identity values must never gain a slot (FR-SRC-4, INV-1).
    """
    assert {f.name for f in dataclasses.fields(TurnEvent)} == {
        "timestamp",
        "session_id",
        "source_id",
        "tool",
        "kind",
        "model",
        "tokens",
        "tool_calls",
        "retries",
        "interruptions",
        "rework_edits",
        "commands_run",
        "commands_failed",
        "commands_slow",
        # W3.1 (reviewed contract change):
        # attempt COUNTERS from the bounded command classifier — integers
        # only, the command string never gains a slot.
        "git_commit_attempts",
        "test_run_attempts",
        # W4.1 (reviewed contract change): artifact COUNTERS from the bounded
        # path classifier — integers only, the path never gains a slot.
        "files_created",
        "doc_files_created",
        "export_writes",
        "files_edited",
        "compactions",
        "interactive",
        "cwd_hash",
        "branch_hash",
        "short_reply",
        "human_initiated",
        # Fast-mode pricing flag (reviewed contract change 2026-09-18): a
        # boolean read from usage.speed, no content.
        "fast",
        "content_present",
        "path_present",
        "identity_present",
    }


def test_execution_counters_default_to_zero() -> None:
    """P1/P2 counters are counters-only with safe defaults (FR-SRC-4)."""
    event = _event(datetime(2026, 7, 2, 9, 0, tzinfo=timezone(timedelta(0))))
    assert (event.rework_edits, event.commands_run) == (0, 0)
    assert (event.commands_failed, event.commands_slow) == (0, 0)
    assert event.compactions == 0
    # P3 defaults: interactive fail-open, environment hashes absent.
    assert event.interactive is True
    assert (event.cwd_hash, event.branch_hash) == ("", "")
    assert event.human_initiated is False


def _event(ts: datetime) -> TurnEvent:
    return TurnEvent(
        timestamp=ts,
        session_id="s",
        source_id="claude_code_cli",
        tool=Tool.CLAUDE_CODE,
        kind=TurnKind.ASSISTANT_TURN,
        model="m",
        tokens=TokenCounts(),
    )


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _event(datetime(2026, 7, 2, 9, 0))


def test_day_key_is_utc_anchored() -> None:
    late_evening_minus5 = datetime(2026, 7, 2, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert _event(late_evening_minus5).day_key() == date(2026, 7, 3)


def test_token_counts_add() -> None:
    a = TokenCounts(input=1, output=2, cached=3, cache_creation=4, reasoning=5,
                    cache_creation_1h=1)
    b = TokenCounts(input=10, output=20, cached=30, cache_creation=40, reasoning=50,
                    cache_creation_1h=2)
    assert a.add(b) == TokenCounts(
        input=11, output=22, cached=33, cache_creation=44, reasoning=55,
        cache_creation_1h=3,
    )
