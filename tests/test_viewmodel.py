"""The shared view-model: closed keys, human sentences, JSON-able,
deterministic, and copy-scanned like every catalog."""

from __future__ import annotations

import dataclasses
import json

from conftest import FIXTURE_DAY, FIXTURE_GENERATED_AT, build_fixture_extras, build_fixture_snapshot
from practicegraph import __version__
from practicegraph.analysis.news import NewsItem
from practicegraph.analysis.ratecard import RATE_CARD_VERSION
from practicegraph.privacy import leak_findings, lexicon_violations
from practicegraph.report.format import percent
from practicegraph.report.viewmodel import (
    VIEW_SCHEMA,
    _version_row,
    day_ribbon_shape,
    suggestion_evidence,
    trend_phrase,
    view_model,
)


def test_the_productivity_profile_withholds_the_commit_denominator() -> None:
    """PRODUCTIVITY_PROFILE P0: the one git-derived reading is withheld
    with a stated reason — this audience does not make commits, and a
    figure that prices them would read as a judgment of work that is not
    commit-shaped."""
    from conftest import build_fixture_extras

    extras = dataclasses.replace(build_fixture_extras(), audience="productivity")
    model = view_model(
        build_fixture_snapshot(), extras, FIXTURE_GENERATED_AT,
        __version__, RATE_CARD_VERSION,
    )
    assert model["profile"] == "productivity"
    economy = model["economy"]
    if economy is not None:  # the fixture window may sit below the floor
        assert economy["commit_cost"] is None
        assert "productivity profile" in economy["commit_cost_note"]
    # The work-type mix rides the same payload for both lanes. (The
    # fixture pack's file mtimes can sit outside the scan window on a
    # long-lived checkout, so classification COVERAGE is pinned in
    # test_harness_features over tmp files; here only the shape is.)
    mix = model["work_mix"]
    assert isinstance(mix, list)
    for harness in mix:
        assert sum(entry["sessions"] for entry in harness["mix"]) == (
            harness["sessions"]
        )
        for entry in harness["mix"]:
            assert entry["doc"]
    # The default lane is unchanged.
    assert _model()["profile"] == "coding"


def test_version_rows_compare_against_the_installs_own_channel() -> None:
    """Field report 2026-08-22: a Codex alpha install was measured against
    the stable tag — the wrong ladder. A prerelease install compares to the
    newest release of any kind; a stable install to the newest stable."""
    from practicegraph.analysis.harness_inventory import ToolVersion

    releases = {
        "codex": (
            "0.149.0", "https://github.com/openai/codex/releases/s",
            "0.150.0-alpha.6", "https://github.com/openai/codex/releases/p",
        ),
    }
    alpha = _version_row(ToolVersion("codex", "0.148.0-alpha.15"), releases)
    assert alpha["channel"] == "prerelease"
    assert alpha["latest"] == "0.150.0-alpha.6"
    assert alpha["newer"] is True
    stable = _version_row(ToolVersion("codex", "0.149.0"), releases)
    assert stable["channel"] == "stable"
    assert stable["latest"] == "0.149.0"
    assert stable["newer"] is False


def _model() -> dict[str, object]:
    return view_model(
        build_fixture_snapshot(),
        build_fixture_extras(),
        FIXTURE_GENERATED_AT,
        __version__,
        RATE_CARD_VERSION,
    )


def test_view_model_is_json_able_deterministic_and_closed() -> None:
    model = _model()
    encoded = json.dumps(model, sort_keys=True)
    assert encoded == json.dumps(_model(), sort_keys=True)
    assert model["schema"] == VIEW_SCHEMA
    assert model["day"] == FIXTURE_DAY.isoformat()
    # The page diet (2026-08-13): every key here is read by the app. What the
    # app stopped rendering left the wire the same day — a computed key with
    # no reader is carrying cost, and the static shell renders from extras,
    # never from this model. Cut then: practice_observation, brief,
    # performance, dimension_labels, weekly_scores, profile, tips, queue,
    # findings (playbook replaces it), work_mix, maturity, ribbon, briefings,
    # model_recommendations, weekly.
    expected_keys = {
        "billing", "quiet_hours", "privacy", "capability", "community",
        "feed_status",
        "schema", "day", "local_day", "accounting_day_utc", "schedule",
        "generated_at", "app_version", "rate_card_version",
        "totals", "ranges", "focus", "rhythm", "blocks",
        "playbook", "noticed", "reflections", "skills_source", "skills",
        "news", "advisor",
        "coaching", "conditioning", "training_load", "runway", "dayclose",
        "coach_ack", "economy", "sessions", "calibration",
        "rate_card_age_days", "rate_card_stale", "reliance",
        "skill_outcome", "update", "install_notices", "work_units",
        "session_tail", "observation", "docs", "model_guidance",
        "model_defaults", "model_catalog", "tools", "profile", "work_mix",
        "drain", "verification",
        "news_days", "build_ideas", "build_repos", "build_ideas_days",
        "pin_projects",
    }
    assert set(model.keys()) == expected_keys
    assert model["local_day"] == FIXTURE_DAY.isoformat()
    assert model["accounting_day_utc"] == FIXTURE_DAY.isoformat()
    assert model["schedule"]["confirmed"] is True  # type: ignore[index]
    assert model["schedule"]["tzdata_version"] == "2026.2"  # type: ignore[index]


def test_playbook_moves_have_a_frozen_wire_shape() -> None:
    """Same discipline as the advisor takes: the playbook is new, so it gets
    its key freeze on day one instead of after the gap is discovered. The
    fixture history is deliberately healthy (no detector fires there), so a
    finding is injected to exercise the projection."""
    from practicegraph.analysis.insights import Finding, Severity

    extras = dataclasses.replace(
        build_fixture_extras(),
        findings=[Finding("context_bloat", Severity.OPPORTUNITY, 669_626)],
    )
    model = view_model(
        build_fixture_snapshot(),
        extras,
        FIXTURE_GENERATED_AT,
        __version__,
        RATE_CARD_VERSION,
    )
    moves = model["playbook"]
    assert isinstance(moves, list) and len(moves) == 1
    for move in moves:
        assert set(move) == {
            "id", "title", "finding", "why", "prompt", "codex_url",
        }
        assert move["codex_url"].startswith("codex://new?prompt=")
        assert "Measured from my local logs" in move["prompt"]
        assert "669.6K" in move["why"]
    # And an empty findings list means an empty playbook, never a filler move.
    assert _model()["playbook"] == []


def test_advisor_takes_have_a_frozen_wire_shape() -> None:
    """The advisor was the one surface with no key freeze: a field could be
    added to AdvisorTake and exported without a single assertion noticing, so
    "the suite is green" said nothing about the wire here. It does now — and
    impact_micro_usd stays OFF the wire deliberately, because a ranking number
    rendered next to a verdict reads as a promised saving."""
    from practicegraph.analysis.advisor import AdvisorTake

    advisor = _model()["advisor"]
    assert advisor is not None, "fixture must exercise the advisor"
    assert set(advisor) == {  # type: ignore[arg-type]
        "as_of", "market_state", "market_as_of", "audit", "takes",
    }
    takes = advisor["takes"]  # type: ignore[index]
    assert takes, "fixture must carry at least one take"
    for take in takes:
        assert set(take) == {
            "id", "tier", "tool", "verdict", "boundary", "receipts", "market",
            "steelman", "action", "experiment", "attribution", "expires",
            "confidence", "confidence_note", "evidence",
        }
        assert take["confidence"] in ("high", "medium", "low")
        assert take["confidence_note"]
    # Every dataclass field is either exported or withheld on purpose.
    exported = {"take_id", "confidence", "confidence_note"} | {
        key for key in takes[0] if key != "id"
    }
    withheld = set(AdvisorTake.__dataclass_fields__) - exported
    assert withheld == {"impact_micro_usd"}, withheld


def test_rich_news_is_closed_text_only_and_json_serializable() -> None:
    item = NewsItem(
        news_id="reader-test",
        kind="post",
        title="A practical agent pattern",
        hook="The example keeps a repeated workflow close at hand.",
        summary="It shows one concrete way to package a recurring agent task.",
        why="The pattern is small enough to inspect and adapt.",
        url="https://example.org/agent-pattern",
        source="Example source",
        thumb="assets/news/ignored.jpg",
    )
    extras = dataclasses.replace(build_fixture_extras(), news=[item])
    model = view_model(
        build_fixture_snapshot(), extras, FIXTURE_GENERATED_AT,
        __version__, RATE_CARD_VERSION,
    )

    assert model["news"] == [
        {
            "id": "reader-test",
            "kind": "post",
            "title": "A practical agent pattern",
            "hook": "The example keeps a repeated workflow close at hand.",
            "summary": "It shows one concrete way to package a recurring agent task.",
            "why": "The pattern is small enough to inspect and adapt.",
            "url": "https://example.org/agent-pattern",
            "source": "Example source",
        }
    ]
    encoded = json.dumps(model)
    assert "thumb" not in encoded
    news_copy = " ".join(
        str(model["news"][0][field])  # type: ignore[index]
        for field in ("title", "hook", "summary", "why", "source")
    )
    assert lexicon_violations(news_copy) == []
    assert leak_findings(news_copy) == []


def test_every_sentence_is_human_and_scanner_clean() -> None:
    encoded = json.dumps(_model())
    # No CLI strings and no key-value dumps on a human surface.
    assert "practicegraph suggest dismiss" not in encoded
    assert "evidence:" not in encoded
    assert "moved between sessions mid-flow" not in encoded
    assert "session switches" not in encoded
    assert "rapid-burst windows" not in encoded
    assert lexicon_violations(encoded) == []


def test_activity_is_never_presented_as_objective_productivity() -> None:
    encoded = json.dumps(_model()).lower()
    assert "perception check" not in encoded
    assert "felt minus measured" not in encoded
    assert "gap_points" not in encoded
    assert "objective productivity" not in encoded


def test_default_coaching_json_has_no_numeric_score() -> None:
    coaching = _model()["coaching"]
    assert coaching
    assert all("score" not in pillar for pillar in coaching)  # type: ignore[union-attr]
    assert all(
        pillar["label"] != "Discipline" for pillar in coaching  # type: ignore[union-attr]
    )


def test_shell_renders_the_shared_ribbon_geometry() -> None:
    """ONE ribbon implementation: the static shell's SVG rects are exactly
    the shared helper's numbers (the golden pins the full bytes; this pins
    the sharing so the algorithms can never fork again)."""
    extras = build_fixture_extras()
    shape = day_ribbon_shape(FIXTURE_DAY, extras.day_marks)
    from practicegraph.report.shell import _day_ribbon

    svg = _day_ribbon(FIXTURE_DAY, extras.day_marks)
    for x, w in shape.segments:
        assert f'<rect x="{x}" y="8" width="{w}" height="12"' in svg
    for x, w in shape.deep:
        assert f'<rect x="{x}" y="8" width="{w}" height="12" rx="2" fill="#1b574b"' in svg
    for tick in shape.ticks:
        assert f'<rect x="{tick}" y="4" width="2" height="20"' in svg


def test_noticed_facts_are_deterministic_ints_from_their_sources() -> None:
    """The recognition band's numbers, pinned to the counters they mirror.
    Ints only — the app owns wording and skips zeros."""
    extras = build_fixture_extras()
    noticed = _model()["noticed"]
    assert set(noticed.keys()) == {  # type: ignore[union-attr]
        "waiting_minutes_28d", "agent_share_pct",
        "attention_high_switch_days_28d", "attention_active_days_28d",
        "attention_confident",
        "longest_block_min_90d", "quiet_hours_activity_pct", "refires_today",
        "outside_preferred_hours_pct",
        "waved_through_today", "approval_moments_today",
        "marathon_compactions_today",
    }
    assert all(
        isinstance(value, int) and value >= 0
        for value in noticed.values()  # type: ignore[union-attr]
    )
    assert extras.rhythm is not None
    assert noticed["waiting_minutes_28d"] == extras.rhythm.waiting_minutes  # type: ignore[index]
    assert noticed["quiet_hours_activity_pct"] == extras.rhythm.quiet_hours_activity_pct  # type: ignore[index]
    assert extras.rhythm_marks_total >= extras.rhythm_marks_interactive > 0
    assert noticed["agent_share_pct"] == percent(  # type: ignore[index]
        extras.rhythm_marks_total - extras.rhythm_marks_interactive,
        extras.rhythm_marks_total,
    )
    assert extras.performance is not None
    assert extras.performance.attention is not None
    attention = extras.performance.attention
    assert noticed["attention_confident"] == int(attention.confident)  # type: ignore[index]
    assert noticed["attention_high_switch_days_28d"] == (  # type: ignore[index]
        attention.high_switch_days if attention.confident else 0
    )
    assert noticed["attention_active_days_28d"] == (  # type: ignore[index]
        attention.active_days if attention.confident else 0
    )
    assert extras.profile is not None
    assert noticed["longest_block_min_90d"] == extras.profile.longest_block_min  # type: ignore[index]
    assert extras.focus is not None
    assert noticed["refires_today"] == extras.focus.refire_replies  # type: ignore[index]
    # Waved approvals mirror today's focus walk exactly (numbers only; the
    # app owns the sentence and its M>=2 / N>=1 significance rule).
    assert noticed["waved_through_today"] == extras.focus.waved_through  # type: ignore[index]
    assert noticed["approval_moments_today"] == (  # type: ignore[index]
        extras.focus.approval_moments
    )
    marathon = [
        finding.metric
        for finding in extras.findings
        if finding.finding_id == "marathon_session"
    ]
    assert noticed["marathon_compactions_today"] == (  # type: ignore[index]
        marathon[0] if marathon else 0
    )


def test_new_sections_pass_the_privacy_scanners() -> None:
    """Noticed ships numbers, never sentences — and even its key names must
    stay clean under the lexicon and leak scanners (FR-FOC-8)."""
    encoded = json.dumps({"noticed": _model()["noticed"]})
    assert lexicon_violations(encoded) == []
    assert leak_findings(encoded) == []


def test_evidence_sentences_read_like_sentences() -> None:
    assert suggestion_evidence("route-routine-to-midtier", 100) == (
        "Premium models carried all of today's priced spend."
    )
    assert suggestion_evidence("route-routine-to-midtier", 62) == (
        "Premium models carried 62% of today's priced spend."
    )
    assert suggestion_evidence("keep-sessions-warm", 12) == (
        "Only 12% of today's prompt tokens came from cache."
    )
    assert "385.3K" in suggestion_evidence("trim-carried-context", 385_300)
    assert trend_phrase(None) == "" and trend_phrase(0) == ""
    assert "9 points worse" in trend_phrase(-9)
    assert "4 points better" in trend_phrase(4)


def test_teamlandi_skills_get_derived_source_and_install_command() -> None:
    extras = dataclasses.replace(
        build_fixture_extras(), skills_source="teamlandi_public"
    )
    model = view_model(
        build_fixture_snapshot(),
        extras,
        FIXTURE_GENERATED_AT,
        __version__,
        RATE_CARD_VERSION,
    )
    assert model["skills_source"] == "teamlandi_public"
    for skill in model["skills"]:
        expected_url = (
            "https://github.com/TeamLandiLTD/skill-registry/tree/main/skills/"
            + skill["id"]
        )
        assert skill["source_url"] == expected_url
        assert skill["install_command"] == f"$skill-installer install {expected_url}"
        # W4.2 Claude Code deep link: the registry SKILL.md is the shared
        # Agent Skills format, so the Claude Code install is one fetch into
        # ~/.claude/skills/<id>/ — same trust gate as the Codex command.
        raw = (
            "https://raw.githubusercontent.com/TeamLandiLTD/skill-registry/"
            "main/skills/" + skill["id"] + "/SKILL.md"
        )
        assert skill["claude_install_command"] == (
            "mkdir -p ~/.claude/skills/" + skill["id"] + " && "
            "curl -fsSL " + raw + " -o ~/.claude/skills/"
            + skill["id"] + "/SKILL.md"
        )


def test_non_teamlandi_skills_remain_prompt_only() -> None:
    for source in ("enterprise", "custom_public", "bundled", "unknown"):
        extras = dataclasses.replace(build_fixture_extras(), skills_source=source)
        model = view_model(
            build_fixture_snapshot(),
            extras,
            FIXTURE_GENERATED_AT,
            __version__,
            RATE_CARD_VERSION,
        )
        assert model["skills_source"] == source
        assert model["skills"]
        for skill in model["skills"]:
            assert "source_url" not in skill
            assert "install_command" not in skill
            assert "claude_install_command" not in skill
            assert skill["prompt"]


def test_observation_is_served_with_its_qualification() -> None:
    """PRINCIPLES 3: a qualified observation leads, carrying its period,
    confidence and caveat. The composer has always existed; until now it
    reached only the static report, so the app had no implementation of it."""
    model = _model()
    observation = model["observation"]
    assert set(observation) == {  # type: ignore[arg-type]
        "id", "title", "body", "period", "confidence", "caveat",
    }
    # the three qualifiers are the point of the card - none may be empty
    assert observation["period"]  # type: ignore[index]
    assert observation["confidence"]  # type: ignore[index]
    assert observation["caveat"]  # type: ignore[index]


def test_observation_survives_the_json_round_trip_unchanged() -> None:
    """INV-6: the same extras produce the same bytes."""
    from practicegraph.report.observation import PracticeObservation

    source = PracticeObservation(
        observation_id="repeated-no-pause",
        title="Long stretches without a pause are repeating",
        body="A two-hour stretch without a 15-minute pause appeared on 9 of the last 28 days.",
        period="last 28 days",
        confidence="repeated pattern",
        caveat="Fixture caveat, distinct from the module default.",
    )
    extras = dataclasses.replace(build_fixture_extras(), practice_observation=source)
    model = view_model(
        build_fixture_snapshot(), extras, FIXTURE_GENERATED_AT,
        "0.0.0-test", RATE_CARD_VERSION,
    )
    served = model["observation"]
    assert served["id"] == "repeated-no-pause"  # type: ignore[index]
    assert served["title"] == source.title  # type: ignore[index]
    assert served["body"] == source.body  # type: ignore[index]
    assert served["period"] == source.period  # type: ignore[index]
    assert served["confidence"] == "repeated pattern"  # type: ignore[index]
    assert served["caveat"] == source.caveat  # type: ignore[index]
    assert json.dumps(model, sort_keys=True) == json.dumps(
        view_model(
            build_fixture_snapshot(), extras, FIXTURE_GENERATED_AT,
            "0.0.0-test", RATE_CARD_VERSION,
        ),
        sort_keys=True,
    )
