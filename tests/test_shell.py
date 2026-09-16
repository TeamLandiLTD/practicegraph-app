"""Multi-view report shell (FR-RPT-1/2/5): golden, script-free views, and the
Privacy Center's exact-preview behavior."""

from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import (
    FIXTURE_GENERATED_AT,
    FIXTURE_LEAK_MARKERS,
    GOLDENS,
    build_fixture_extras,
    build_fixture_snapshot,
    build_shell_privacy_status,
)
from practicegraph import __version__
from practicegraph.analysis.ratecard import RATE_CARD_VERSION
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.shell import PrivacyStatus, ShellExtras, render_shell


def _render(
    privacy: PrivacyStatus | None = None, extras: ShellExtras | None = None
) -> str:
    return render_shell(
        build_fixture_snapshot(),
        privacy or build_shell_privacy_status(),
        FIXTURE_GENERATED_AT,
        __version__,
        RATE_CARD_VERSION,
        extras=extras,
    )


def _render_full() -> str:
    return _render(extras=build_fixture_extras())


def test_shell_is_byte_stable_and_matches_golden() -> None:
    first, second = _render_full(), _render_full()
    assert first == second
    assert first.encode("utf-8") == (GOLDENS / "shell_report.html").read_bytes()


def test_shell_extras_sections_render() -> None:
    page = _render_full()
    assert "Usage at listed rates" in page
    assert "Subscription amounts are comparisons, not charges" in page
    assert "Recorded usage details</summary>" in page
    assert "Accounting details</summary>" in page
    assert 'aria-label="Report range"' in page
    for range_id in ("range-week", "range-month", "range-all"):
        assert f'id="{range_id}"' in page
    assert "By tool" in page and "last active" in page
    assert "Field guide" in page
    assert "Continue sessions to reuse cache" in page
    assert "Your own reflection</summary>" in page
    for rating in range(1, 6):
        assert f'href="practicegraph:checkin-{rating}"' in page
    for retired in (
        "Performance profile", "Maturity signals", "Working rhythm",
        "projects touched", "branches touched", "waiting on responses",
        "longest block", "mid-block switches", "Review &amp; judgment",
        "Approvals going through unread", "current streak", "longest streak",
    ):
        assert retired not in page, retired


def test_shell_has_all_views_script_free() -> None:
    page = _render()
    for view in ("today", "insights", "sources", "skills", "privacy"):
        assert f'id="{view}"' in page
    lowered = page.lower()
    assert "<script" not in lowered
    assert "src=" not in lowered
    assert "http" not in lowered
    hrefs = re.findall(r'href="([^"]*)"', lowered)
    # Anchors switch views; practicegraph: links dispatch to the local shell
    # (protocol-registered, C-5) — still script-free, still on-machine.
    assert hrefs and all(
        href.startswith("#") or href.startswith("practicegraph:") for href in hrefs
    )
    # View switching is CSS-only (:target panels) — anchors, no form controls.
    assert "<input" not in lowered


def test_shell_passes_privacy_scanners() -> None:
    page = _render()
    assert leak_findings(page) == []
    assert lexicon_violations(page) == []
    for marker in FIXTURE_LEAK_MARKERS:
        assert marker not in page


def test_privacy_center_defaults_show_synthetic_example() -> None:
    page = _render()
    assert "OFF (default)" in page
    assert "nothing leaves this machine" in page.lower()
    assert "SYNTHETIC EXAMPLE" in page
    assert "practicegraph consent on" in page


def test_privacy_center_shows_exact_queued_payload() -> None:
    """FR-RPT-5: with an emit queued, the preview is the exact payload."""
    base = build_shell_privacy_status()
    queued = json.dumps(
        {"schema_version": 1, "day": "2026-07-02", "estimated_cost_micro_usd": 39305},
        indent=2,
        sort_keys=True,
    )
    privacy = PrivacyStatus(
        consent_enabled=True,
        consent_decided_at="2026-07-01T08:00:00+00:00",
        endpoint_configured=True,
        org_configured=True,
        queue_counts={"pending": 1, "retry_wait": 0, "sent": 3, "dead_letter": 0},
        queue_error_counts={"server_unavailable": 2},
        next_payload_pretty=queued,
        example_payload_pretty=base.example_payload_pretty,
    )
    page = _render(privacy)
    assert "opted in" in page
    assert "SYNTHETIC EXAMPLE" not in page
    assert "Exact next queued payload" in page
    assert "&quot;2026-07-02&quot;" in page  # escaped JSON preview
    assert "server_unavailable" in page and "server_unavailable: 2" in page


def test_static_surface_never_mutates_state() -> None:
    """FR-RPT-5: no forms or buttons that write."""
    page = _render().lower()
    assert "<form" not in page
    assert "<button" not in page
    assert "<input" not in page


def test_approaching_quota_finding_needs_a_high_latest_reading(
    tmp_path: Path,
) -> None:
    """P4: the informational quota finding gates on the day's LATEST provider
    rate-limit reading. The fixture day's latest gauge (41.0%) stays below
    the named constant, so the demo report is finding-free; a later reading
    above it flips the INFO finding on, metric in whole percent."""
    from datetime import UTC, date, datetime

    from conftest import build_fixture_history, default_prefs
    from practicegraph.analysis.insights import APPROACHING_QUOTA_PCT, Severity
    from practicegraph.history import snapshot_for_day
    from practicegraph.report.shell import gather_shell_extras

    store, health = build_fixture_history(tmp_path)
    day = date(2026, 7, 2)
    snapshot = snapshot_for_day(store, day, health)
    prefs = default_prefs()
    extras = gather_shell_extras(store, snapshot, prefs, day)
    assert store.latest_rate_limit(day.isoformat()) == (410, 300)
    assert "approaching_quota" not in [f.finding_id for f in extras.findings]

    store.replace_file_data(
        source_id="codex_cli",
        path_hash="quota-seed",
        cursor=(1, 1, 7),
        contributions=[],
        marks=[],
        health=None,
        turn_keys=(),
        now=datetime(2026, 7, 2, 23, 30, tzinfo=UTC),
        rate_limit_marks=[
            ("2026-07-02", "2026-07-02T23:00:00+00:00",
             APPROACHING_QUOTA_PCT * 10 + 75, 300),
        ],
    )
    extras = gather_shell_extras(store, snapshot, prefs, day)
    found = [f for f in extras.findings if f.finding_id == "approaching_quota"]
    assert len(found) == 1
    assert found[0].severity is Severity.INFO
    assert found[0].metric == 87  # 875 tenths -> whole percent
    # The report presents the recorded gauge with provenance, not a live warning.
    page = _render(extras=extras)
    assert "Recorded allowance readings" in page
    assert "300-minute window: 88% used" in page
    assert "2026-07-02T23:00:00+00:00" in page


def test_shell_extras_recommend_models_for_detected_tools(tmp_path: Path) -> None:
    from datetime import date

    from conftest import build_fixture_history, default_prefs
    from practicegraph.history import snapshot_for_day
    from practicegraph.report.shell import gather_shell_extras

    store, health = build_fixture_history(tmp_path)
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    artifact = {
        "schema": "practicegraph.model-intelligence/1",
        "artifact_version": "models-aa-coding-v1.1-2026-07-02",
        "published_on": "2026-07-02",
        "source": {
            "name": "Artificial Analysis",
            "results_url": "https://artificialanalysis.ai/agents/coding-agents",
            "methodology_url": (
                "https://artificialanalysis.ai/methodology/coding-agents-benchmarking"
            ),
            "attribution": "Coding-agent benchmark data: Artificial Analysis",
        },
        "benchmark": {
            "name": "Artificial Analysis Coding Agent Index",
            "version": "1.1",
            "token_unit": "average_total_tokens_per_task",
        },
        "variants": [
            {
                "id": "codex-balanced",
                "tool": "codex",
                "model": "GPT Balanced",
                "effort": "high",
                "index_tenths": 800,
                "total_tokens_per_task": 3_000_000,
            },
            {
                "id": "claude-balanced",
                "tool": "claude_code",
                "model": "Claude Balanced",
                "effort": "adaptive",
                "index_tenths": 810,
                "total_tokens_per_task": 4_000_000,
            },
        ],
    }
    (catalog_dir / "models.json").write_text(json.dumps(artifact), encoding="utf-8")
    day = date(2026, 7, 2)

    extras = gather_shell_extras(
        store, snapshot_for_day(store, day, health), default_prefs(), day
    )

    assert [item.tool for item in extras.model_recommendations] == [
        "claude_code",
        "codex",
    ]
    assert all(item.current_model for item in extras.model_recommendations)


def test_waved_approvals_finding_gates_and_renders(tmp_path: Path) -> None:
    """Waved-through approvals: the fixture day has no long-run approval
    moments (finding absent); a seeded day of waved long runs crosses the
    calibrated gates -> INFO finding, the Focus card counter line, and the
    read-the-handoff tip family. Descriptive forever by decision."""
    from datetime import UTC, date, datetime

    from conftest import build_fixture_history, default_prefs
    from practicegraph.analysis.focus import APPROVAL_STRETCH_MIN_TURNS
    from practicegraph.analysis.insights import Severity
    from practicegraph.history import snapshot_for_day
    from practicegraph.report.shell import gather_shell_extras

    store, health = build_fixture_history(tmp_path)
    day = date(2026, 7, 2)
    snapshot = snapshot_for_day(store, day, health)
    prefs = default_prefs()
    extras = gather_shell_extras(store, snapshot, prefs, day)
    assert extras.focus is not None
    assert extras.focus.approval_moments == 0  # fixture runs are short
    assert "approvals_waved_through" not in [
        f.finding_id for f in extras.findings
    ]
    assert "long-run approvals waved through" not in _render(extras=extras)

    marks = []
    for run in range(3):
        session = f"waved-{run}"
        for index in range(APPROVAL_STRETCH_MIN_TURNS):
            marks.append(
                ("2026-07-02", session, f"2026-07-02T1{run}:{index:02d}:00+00:00",
                 "assistant_turn", 2, 0, 0, 0, 0, 1, "", "", 0)
            )
        ask_min = APPROVAL_STRETCH_MIN_TURNS
        marks.append(
            ("2026-07-02", session, f"2026-07-02T1{run}:{ask_min:02d}:00+00:00",
             "assistant_turn", 0, 0, 0, 0, 0, 1, "", "", 0)
        )
        marks.append(
            ("2026-07-02", session, f"2026-07-02T1{run}:{ask_min:02d}:03+00:00",
             "user_turn", 0, 0, 0, 0, 0, 1, "", "", 1)  # "go", 3s later
        )
    store.replace_file_data(
        source_id="claude_code_cli",
        path_hash="waved-seed",
        cursor=(1, 1, 7),
        contributions=[],
        marks=marks,
        health=None,
        turn_keys=(),
        now=datetime(2026, 7, 2, 23, 30, tzinfo=UTC),
    )
    extras = gather_shell_extras(store, snapshot, prefs, day)
    assert extras.focus is not None
    assert extras.focus.approval_moments == 3
    assert extras.focus.waved_through == 3
    found = [
        f for f in extras.findings if f.finding_id == "approvals_waved_through"
    ]
    assert len(found) == 1
    assert found[0].severity is Severity.INFO
    assert found[0].metric == 3
    page = _render(extras=extras)
    assert "Approvals going through unread" not in page
    assert "long-run approvals waved through" not in page


def test_news_is_capped_at_three_regardless_of_the_served_artifact() -> None:
    """Awareness has a hard ceiling, enforced here rather than trusted to the
    file. News survived the evaluation on the owner's override ("I still feel
    news should contain 3 at least. As a dev I like to know") — and the reason
    the override is sound is that news asks you to *know*, not to act, so the
    recommend-less evidence does not bind it. What does bind it is that a
    served artifact is remote input: if it ever grows to twelve items, the page
    must not become a feed."""
    from practicegraph.report.shell import NEWS_MAX_ITEMS

    assert NEWS_MAX_ITEMS == 3
