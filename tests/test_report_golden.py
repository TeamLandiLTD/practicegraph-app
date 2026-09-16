"""Byte-golden report regression (FR-RPT-3) and offline hard rules (FR-RPT-2).

Goldens are regenerated only via ``python tests/regen_goldens.py`` — an
intentional, reviewable flow. A golden diff is a contract change.
"""

from __future__ import annotations

import re
from datetime import date

from conftest import (
    FIXTURE_GENERATED_AT,
    FIXTURE_LEAK_MARKERS,
    GOLDENS,
    build_fixture_extras,
    build_fixture_snapshot,
)
from practicegraph import __version__
from practicegraph.analysis.aggregate import DailySnapshot, UsageRow
from practicegraph.analysis.ratecard import RATE_CARD_VERSION
from practicegraph.events import TokenCounts
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.html import render_html
from practicegraph.report.text import render_text


def _text() -> str:
    return render_text(
        build_fixture_snapshot(), FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION,
        extras=build_fixture_extras(),
    )


def _html() -> str:
    return render_html(
        build_fixture_snapshot(), FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION,
        extras=build_fixture_extras(),
    )


def test_daily_reports_lead_with_a_qualified_observation() -> None:
    observation = build_fixture_extras().practice_observation
    text = _text()
    html = _html()
    for rendered in (text, html):
        assert observation.observation_id in rendered
        assert observation.confidence in rendered
        assert observation.period in rendered
        assert observation.caveat in rendered
        assert "felt minus measured" not in rendered.lower()
        assert "objective productivity" not in rendered.lower()


def test_text_report_is_byte_stable_and_matches_golden() -> None:
    first, second = _text(), _text()
    assert first == second
    assert first.encode("utf-8") == (GOLDENS / "daily_report.txt").read_bytes()


def test_html_report_is_byte_stable_and_matches_golden() -> None:
    first, second = _html(), _html()
    assert first == second
    assert first.encode("utf-8") == (GOLDENS / "daily_report.html").read_bytes()


def test_reports_pass_no_leak_scanners() -> None:
    """NFR-PRV-1 over every report surface, using the seeded fixture markers."""
    for rendered in (_text(), _html()):
        assert leak_findings(rendered) == []
        assert lexicon_violations(rendered) == []
        for marker in FIXTURE_LEAK_MARKERS:
            assert marker not in rendered


def test_html_offline_hard_rules() -> None:
    """FR-RPT-2 (invariant): no script, no external refs; any href must be an
    in-page anchor."""
    html_out = _html().lower()
    assert "<script" not in html_out
    assert "src=" not in html_out
    assert "http" not in html_out
    hrefs = re.findall(r'href="([^"]*)"', html_out)
    assert all(href.startswith("#") for href in hrefs)


def test_html_escapes_hostile_dynamic_values() -> None:
    """Model names come from local logs and are untrusted (NFR-SEC-2)."""
    hostile_row = UsageRow(
        tool="claude_code",
        model='<script>alert(1)</script>"><img',
        assistant_turns=1,
        user_turns=0,
        tokens=TokenCounts(input=1, output=1),
        tool_calls=0,
        retries=0,
        interruptions=0,
        cost_micro_usd=0,
        unpriced_turns=1,
    )
    snapshot = DailySnapshot(
        day=date(2026, 7, 2),
        rows=(hostile_row,),
        total_cost_micro_usd=0,
        total_unpriced_turns=1,
        total_assistant_turns=1,
        total_user_turns=0,
        total_tool_calls=0,
        total_retries=0,
        total_interruptions=0,
        session_count=1,
        source_health=(),
    )
    rendered = render_html(snapshot, FIXTURE_GENERATED_AT, __version__, RATE_CARD_VERSION)
    assert "<script" not in rendered
    assert "&lt;script&gt;" in rendered
    assert '"><img' not in rendered
