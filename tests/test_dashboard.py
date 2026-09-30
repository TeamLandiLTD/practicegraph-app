"""K-anon view-model rules (INV-4, FR-API-5) and dashboard rendering
(FR-DSH-1/2/3/4): byte-golden, no-script, honest suppression."""

from __future__ import annotations

import re

from conftest import (
    FIXTURE_GENERATED_AT,
    FIXTURE_LEAK_MARKERS,
    GOLDENS,
    build_dashboard_summary,
    build_fixture_history,
    build_fixture_payload,
)
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph_server.render import (
    ADOPTION_VERDICTS,
    ONBOARDING_INTRO,
    ONBOARDING_NOTE,
    ONBOARDING_STEPS,
    UNPRICED_CAVEAT,
    VALUE_VERDICTS,
    WASTE_VERDICTS,
    _adoption_band,
    _value_band,
    _waste_band,
    render_dashboard,
    render_error,
)
from practicegraph_server.view import build_summary


def test_view_statuses_no_data_vs_suppressed_vs_ok() -> None:
    summary = build_dashboard_summary()
    by_day = {cell["day"]: cell for cell in summary["days"]}
    assert by_day["2026-06-30"]["status"] == "no_data"
    assert by_day["2026-07-01"]["status"] == "suppressed"
    assert by_day["2026-07-02"]["status"] == "ok"
    # Suppressed cells carry the marker and nothing else — never zeros (FR-DSH-2).
    assert set(by_day["2026-07-01"].keys()) == {"day", "status"}


def test_view_ok_day_sums_and_totals_exclude_hidden_days() -> None:
    summary = build_dashboard_summary()
    ok_day = next(cell for cell in summary["days"] if cell["status"] == "ok")
    assert ok_day["contributors"] == 2
    assert ok_day["estimated_cost_micro_usd"] == 42005 * 2
    totals = summary["totals"]
    assert totals["status"] == "ok"
    assert totals["days_included"] == 1  # the suppressed day is NOT interpolated
    assert totals["estimated_cost_micro_usd"] == 42005 * 2


def test_view_all_below_threshold_suppresses_totals() -> None:
    single = build_dashboard_summary()
    lone_payloads = {"2026-07-01": [{"estimated_cost_micro_usd": 1}]}
    summary = build_summary(lone_payloads, 5, "2026-07-01", "2026-07-02")
    assert summary["days"][0]["status"] == "suppressed"
    assert summary["days"][1]["status"] == "no_data"
    assert summary["totals"] == {"status": "suppressed", "days_included": 0}
    assert single["k_threshold"] == 2  # sanity: fixtures use k=2


def _render() -> str:
    return render_dashboard(build_dashboard_summary(), "acme-eng", FIXTURE_GENERATED_AT)


def test_dashboard_is_byte_stable_and_matches_golden() -> None:
    first, second = _render(), _render()
    assert first == second
    assert first.encode("utf-8") == (GOLDENS / "dashboard.html").read_bytes()


def test_dashboard_states_render_distinctly() -> None:
    page = _render()
    assert "unavailable (below k-anonymity threshold)" in page
    assert "no data" in page
    assert "$0.08" in page  # 2 x 42005 micro-USD, half-up
    error_page = render_error("internal_error")
    assert "internal_error" in error_page
    assert "<script" not in error_page.lower()


def test_view_aggregates_work_type_and_maturity() -> None:
    """M6 depth: v2 fields aggregate across contributors."""
    summary = build_dashboard_summary()
    ok_day = next(cell for cell in summary["days"] if cell["status"] == "ok")
    # Two identical contributors -> doubled session counts (the fixture day
    # carries two converse-shaped sessions since the P3 sidechain fixture).
    assert ok_day["work_type_sessions"] == {
        "build": 2, "investigate": 2, "converse": 4, "unknown": 0,
    }
    assert ok_day["maturity_distribution"]["cache_reuse"]["leading"] == 2
    totals = summary["totals"]
    assert totals["work_type_sessions"]["build"] == 2
    # Two tools is the enum's ceiling, so both fixture contributors sit at
    # leading (the old >=3 rung was unreachable by construction).
    assert totals["maturity_distribution"]["multi_tool"]["leading"] == 2


def test_dashboard_renders_work_type_and_maturity() -> None:
    page = _render()
    assert "Where fleet sessions went" in page
    assert "Maturity distribution" in page
    assert "leading" in page


def test_small_maturity_buckets_are_withheld() -> None:
    """INV-4: a level bucket smaller than k would make individuals
    enumerable — the distribution is withheld, not zeroed."""
    store, health = build_fixture_history()
    first = build_fixture_payload(store, health, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    second = build_fixture_payload(store, health, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    second["maturity"] = {**second["maturity"], "cache_reuse": "emerging"}  # type: ignore[dict-item]
    summary = build_summary(
        {"2026-07-02": [first, second]}, k_threshold=2,
        from_day="2026-07-02", to_day="2026-07-02",
    )
    page = render_dashboard(summary, "acme-eng", FIXTURE_GENERATED_AT)
    assert "insufficient subgroup sizes" in page
    assert "withheld, not zero" in page


def test_live_mode_is_meta_refresh_and_still_script_free() -> None:
    """`refresh_seconds` > 0 gives a live page via meta-refresh only — no
    script, no external reference (NFR-SEC-2 holds). The clamp happens in the
    route; here we render a live page and pin its shape."""
    live = render_dashboard(
        build_dashboard_summary(), "acme-eng", FIXTURE_GENERATED_AT, refresh_seconds=15
    )
    low = live.lower()
    # It IS live: a meta-refresh drives the reload, on the given cadence.
    assert '<meta http-equiv="refresh" content="15;' in low
    assert "refreshing every" in low
    # It stays script-free and carries no external URL — the only "http" token
    # is the http-equiv attribute, never an http:// or https:// reference.
    assert "<script" not in low
    assert "src=" not in low
    assert "http://" not in low
    assert "https://" not in low
    # A live page offers "pause" (back to static), a static page offers "go live".
    assert "pause" in low
    assert "go live" not in low
    static = _render().lower()
    assert "http-equiv" not in static  # no auto-refresh when not live
    assert "go live" in static


def test_dashboard_offline_hard_rules_and_no_leaks() -> None:
    page = _render().lower()
    assert "<script" not in page
    assert "src=" not in page
    assert "http" not in page
    hrefs = re.findall(r'href="([^"]*)"', page)
    # Fragments, the relative window presets, and the relative go-live link
    # (?days=N&live=N) — all relative, still nothing external.
    assert all(
        href.startswith("#")
        or re.fullmatch(r"\?days=\d{1,2}", href)
        or re.fullmatch(r"\?days=\d{1,2}&amp;live=\d{1,3}", href)
        for href in hrefs
    ), hrefs
    rendered = _render()
    assert leak_findings(rendered) == []
    assert lexicon_violations(rendered) == []
    for marker in FIXTURE_LEAK_MARKERS:
        assert marker not in rendered
    # No person, machine, or session identifiers anywhere (FR-DSH-3).
    assert "session_id" not in rendered
    assert "emit_id" not in rendered


# ---- Phase 3: data-driven verdicts, coverage, presets, bias, onboarding ------


def _two_contributor_summary(
    mutate: dict[str, object] | None = None,
) -> dict[str, object]:
    """One released day with two fixture contributors; ``mutate`` patches the
    second payload before suppression rules run."""
    store, health = build_fixture_history()
    first = build_fixture_payload(store, health, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    second = build_fixture_payload(store, health, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    if mutate:
        second.update(mutate)
    return build_summary(
        {"2026-07-02": [first, second]},
        k_threshold=2,
        from_day="2026-07-02",
        to_day="2026-07-02",
    )


def test_verdict_bands_are_deterministic_and_closed() -> None:
    """Every sentence comes from a closed catalog; the band pickers are pure
    functions of the view-model (endpoint report/brief.py pattern)."""
    assert _value_band(70, 1) == "strong"
    assert _value_band(69, 1) == "moderate"
    assert _value_band(40, 1) == "moderate"
    assert _value_band(39, 1) == "low"
    assert _value_band(99, 0) == "no_data"  # no released token detail
    assert _waste_band(70, 1) == "heavy"
    assert _waste_band(69, 1) == "moderate"
    assert _waste_band(40, 1) == "moderate"
    assert _waste_band(39, 1) == "light"
    assert _waste_band(0, 0) == "unpriced"  # nothing priced
    assert _adoption_band([]) == "none"
    assert _adoption_band([2]) == "single_day"
    assert _adoption_band([2, 3]) == "rising"
    assert _adoption_band([3, 3]) == "steady"
    assert _adoption_band([3, 2]) == "steady"
    # The pickers can only ever return catalog keys.
    assert set(VALUE_VERDICTS) == {"strong", "moderate", "low", "no_data"}
    assert set(WASTE_VERDICTS) == {"heavy", "moderate", "light", "unpriced"}
    assert set(ADOPTION_VERDICTS) == {"rising", "steady", "single_day", "none"}


def test_answer_cards_pick_verdicts_from_the_data() -> None:
    """Fixture window: 74% cached (strong), 12% premium (light), one released
    day (single_day) — the static sentences of the audit are gone."""
    page = _render()
    assert VALUE_VERDICTS["strong"] in page
    assert WASTE_VERDICTS["light"] in page
    assert ADOPTION_VERDICTS["single_day"] in page
    assert WASTE_VERDICTS["heavy"] not in page  # the old always-on sentence


def test_dashboard_copy_catalogs_pass_lexicon_and_leak_scans() -> None:
    """FR-FOC-8/NFR-PRV-1 discipline for the new closed catalogs."""
    texts = [
        *VALUE_VERDICTS.values(),
        *WASTE_VERDICTS.values(),
        *ADOPTION_VERDICTS.values(),
        UNPRICED_CAVEAT,
        ONBOARDING_INTRO,
        ONBOARDING_NOTE,
    ]
    for title, command in ONBOARDING_STEPS:
        texts.extend((title, command))
    for text in texts:
        assert lexicon_violations(text) == []
        assert leak_findings(text) == []


def test_coverage_card_counts_and_withholds_small_subgroups() -> None:
    page = _render()
    assert "Coverage" in page
    assert "2 of 2 supported source(s) detected across released days" in page
    assert "peak released day 2 contributor(s)" in page
    # One contributor drops codex: a 1-observer subgroup inside a released
    # day stays withheld (INV-4), never rendered as a count.
    summary = _two_contributor_summary({"tools_observed": ["claude_code"]})
    page = render_dashboard(summary, "acme-eng", FIXTURE_GENERATED_AT)
    assert "1 of 2 supported source(s)" not in page  # codex detected, withheld
    assert ">withheld<" in page
    assert "codex: 1" not in page


def test_window_preset_links_are_relative_anchors() -> None:
    page = _render()
    for href in ("?days=7", "?days=30", "?days=90"):
        assert f'href="{href}"' in page


def test_unpriced_turns_render_the_estimate_bias_caveat() -> None:
    assert UNPRICED_CAVEAT not in _render()  # fixture window is fully priced
    summary = _two_contributor_summary({"unpriced_turns": 3})
    page = render_dashboard(summary, "acme-eng", FIXTURE_GENERATED_AT)
    assert f"3 {UNPRICED_CAVEAT}" in page


def test_empty_org_renders_onboarding_card() -> None:
    """A fresh org sees how to connect agents — placeholders only."""
    summary = build_summary({}, k_threshold=2, from_day="2026-06-30", to_day="2026-07-02")
    page = render_dashboard(summary, "acme-eng", FIXTURE_GENERATED_AT)
    assert "Connect your first agents" in page
    assert "--api-base-url &lt;server-url&gt; --org-id &lt;org-id&gt;" in page
    assert "&lt;org-token&gt; | practicegraph config set-token" in page
    assert "practicegraph consent on" in page
    assert "<script" not in page.lower()
    assert leak_findings(page) == []
    assert lexicon_violations(page) == []
    # Never rendered once any aggregate exists (even a suppressed one).
    lone = build_summary(
        {"2026-07-01": [{"estimated_cost_micro_usd": 1}]},
        k_threshold=2,
        from_day="2026-07-01",
        to_day="2026-07-02",
    )
    assert "Connect your first agents" not in render_dashboard(
        lone, "acme-eng", FIXTURE_GENERATED_AT
    )


def test_day_rows_split_drift_for_released_days() -> None:
    """Fixture drift 2x(2,1,2): the released day row carries the split."""
    page = _render()
    assert "malformed 4 &middot; unknown field 2 &middot; unsupported 4" in page
    # Zero drift renders a bare 0 — no split noise.
    store, health = build_fixture_history()
    quiet = []
    for emit_id in (
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ):
        payload = build_fixture_payload(store, health, emit_id)
        health_block = dict(payload["agent_health"])  # type: ignore[arg-type]
        health_block.update({"malformed": 0, "unknown_field": 0, "unsupported": 0})
        payload["agent_health"] = health_block
        quiet.append(payload)
    zero = build_summary(
        {"2026-07-02": quiet}, k_threshold=2,
        from_day="2026-07-02", to_day="2026-07-02",
    )
    page = render_dashboard(zero, "acme-eng", FIXTURE_GENERATED_AT)
    assert "malformed" not in page
