"""The playbook: paste-ready prompts minted from the finding that fired."""

from __future__ import annotations

from urllib.parse import unquote

from practicegraph.analysis.insights import FINDING_IDS
from practicegraph.analysis.playbook import (
    _METRIC_KIND,
    CODEX_NEW_PREFIX,
    PLAYBOOK_COPY,
    PLAYBOOK_MAX,
    compose_playbook,
)
from practicegraph.privacy import leak_findings, lexicon_violations


def test_every_finding_has_a_move_and_nothing_else_does() -> None:
    """A detector without a move is a described problem with no handle; an
    orphan template is copy nobody reviews. Closed in both directions — and
    closed PER REGISTER: every finding carries both the repo prompt and the
    plain one, so no register can silently fall back to the other's world."""
    expected = {
        # The synthesized default-check move (R3): not a detector, minted
        # from premium_heavy / unpriced_models evidence, two why-doors.
        "default_check-move", "default_check-why-share",
        "default_check-why-unpriced", "default_check-prompt",
        "default_check-prompt-plain",
    }
    for finding_id in FINDING_IDS:
        expected |= {
            f"{finding_id}-move", f"{finding_id}-why",
            f"{finding_id}-prompt", f"{finding_id}-prompt-plain",
        }
    assert set(PLAYBOOK_COPY) == expected
    assert set(_METRIC_KIND) == set(FINDING_IDS)
    assert set(_METRIC_KIND.values()) <= {
        "count", "tokens", "pct", "points", "multiple",
    }


def test_the_plain_register_never_speaks_repository(  # red team #8
) -> None:
    """The original catalog attached repo instructions to every finding — a
    person producing documents got told to edit AGENTS.md. The plain register
    must never mention the machinery its reader does not have; the words are
    banned at the catalog, the same way quoting characters are."""
    for key, template in sorted(PLAYBOOK_COPY.items()):
        if not key.endswith("-prompt-plain"):
            continue
        lowered = template.lower()
        for banned in (
            "repositor", "agents.md", "claude.md", "linter", "lint",
            " diff", "commit", "branch", "test suite", " tests",
        ):
            assert banned not in lowered, (key, banned)
        # Same shape contract as the repo register.
        assert "Goal:" in template
        assert "Measured from my local logs" in template
        assert "Done when" in template


def test_catalog_passes_lexicon_and_leak_scans() -> None:
    for key, template in sorted(PLAYBOOK_COPY.items()):
        text = template.format(
            metric="42%", evidence="premium models carried 42% today."
        )
        assert lexicon_violations(text) == [], key
        assert leak_findings(text) == [], key


def test_prompts_survive_any_shell_quoting_and_any_encoder() -> None:
    """The prompt travels as a URL parameter and as a pasted CLI argument.
    One quote character or newline in a template breaks one of those lanes
    silently, on someone else's machine — so the characters are banned at
    the catalog, not escaped at the edges."""
    for key, template in sorted(PLAYBOOK_COPY.items()):
        for banned in ('"', "'", "$", "`", "\n", "\\"):
            assert banned not in template, (key, banned)
    # And no promised outcome anywhere: the prompt proposes, next week's
    # numbers judge.
    blob = " ".join(PLAYBOOK_COPY.values()).lower()
    for word in ("saves you", "will save", "guarantee", "instantly"):
        assert word not in blob, word


def test_compose_fills_the_metric_caps_and_keeps_detector_order() -> None:
    findings = [
        ("retry_storm", 7),
        ("context_bloat", 669_626),
        ("low_cache_reuse", 34),
        ("marathon_session", 5),
        ("command_friction", 12),  # fifth: past the cap
    ]
    moves = compose_playbook(findings)
    assert len(moves) == PLAYBOOK_MAX
    assert [m.move_id for m in moves] == [
        "retry_storm", "context_bloat", "low_cache_reuse", "marathon_session",
    ]
    assert "7 requests were retried" in moves[0].prompt
    assert "669.6K prompt tokens" in moves[1].prompt  # compact(), not raw
    assert moves[2].why == "Only 34% of prompt tokens came from cache today."
    # Deterministic: same input, same tuple.
    assert compose_playbook(findings) == moves
    # And the cap holds when the synthesized default-check joins the queue.
    with_premium = compose_playbook([("premium_heavy", 87), *findings])
    assert len(with_premium) == PLAYBOOK_MAX
    assert with_premium[0].move_id == "default_check"


def test_codex_url_is_the_prefix_plus_the_fully_encoded_prompt() -> None:
    (move,) = compose_playbook([("marathon_session", 5)])
    assert move.codex_url.startswith(CODEX_NEW_PREFIX)
    encoded = move.codex_url[len(CODEX_NEW_PREFIX):]
    # Fully encoded: nothing shell-meaningful or space-like survives raw.
    for raw in (" ", '"', "&", "#", "?", "="):
        assert raw not in encoded
    assert unquote(encoded) == move.prompt


def test_unknown_ids_are_skipped_not_invented() -> None:
    moves = compose_playbook([("not_a_finding", 3), ("retry_storm", 4)])
    assert [m.move_id for m in moves] == ["retry_storm"]


def test_the_default_check_leads_subsumes_unpriced_and_needs_a_door() -> None:
    """R3 with the red-team correction: the release has two doors, because a
    default of an automatic alias lands every turn in unpriced and the
    premium share then never computes — the person the move most helps would
    otherwise never see it. We state what we observed and hand the setting
    question to the agent that can read it; we never claim to know the
    default ourselves."""
    # Door one: premium share readable.
    moves = compose_playbook([("premium_heavy", 87), ("retry_storm", 4)])
    assert moves[0].move_id == "default_check"
    assert "87%" in moves[0].why
    assert "never see the setting" in moves[0].prompt
    # The routing move still follows — different action, both earn a slot.
    assert [m.move_id for m in moves[1:]] == ["premium_heavy", "retry_storm"]

    # Door two: the auto-alias case — share unreadable, unpriced fires.
    moves = compose_playbook([("unpriced_models", 12)])
    assert [m.move_id for m in moves] == ["default_check"]
    assert "12" in moves[0].why and "cannot price" in moves[0].why

    # Subsumption: when the check fires, the pin-the-default move does not
    # render twice.
    moves = compose_playbook([("premium_heavy", 60), ("unpriced_models", 3)])
    assert [m.move_id for m in moves] == ["default_check", "premium_heavy"]

    # Neither door: no synthetic move is invented.
    moves = compose_playbook([("retry_storm", 4)])
    assert [m.move_id for m in moves] == ["retry_storm"]


def test_the_register_changes_the_instruction_not_the_number() -> None:
    """Same finding, same measured number, different world: the repo register
    proposes a standing note in AGENTS.md or CLAUDE.md, the plain register a
    note the person keeps. The metric and the move title never differ."""
    (repo,) = compose_playbook([("context_bloat", 546_900)], register="repo")
    (plain,) = compose_playbook([("context_bloat", 546_900)], register="plain")
    assert repo.title == plain.title
    assert repo.why == plain.why
    assert "546.9K" in repo.prompt and "546.9K" in plain.prompt
    assert "AGENTS.md" in repo.prompt
    assert "AGENTS.md" not in plain.prompt
    assert "repository" not in plain.prompt
    # Both deep links carry their own register's prompt.
    assert repo.codex_url != plain.codex_url


def test_moves_read_like_moves() -> None:
    """Row labels are imperatives with no trailing period; the why is a
    sentence carrying the number; the prompt states its contract."""
    findings = [(finding_id, 42) for finding_id in FINDING_IDS]
    for move in compose_playbook(findings[:PLAYBOOK_MAX]):
        assert not move.title.endswith(".")
        # Whys that lead with the number stay as the number.
        assert move.why[0].isupper() or move.why[0].isdigit()
        assert move.why.endswith(".")
        assert "Goal:" in move.prompt
        assert "Measured from my local logs" in move.prompt
        assert "Done when" in move.prompt


def test_percentage_point_delta_is_not_rendered_as_a_share() -> None:
    """late_night_drift's metric is a percentage-POINT rise against the
    person's own baseline (quiet_hours_drift's docstring says so). Rendering
    it as "12%" states a share the number is not, and the string travels out
    of the app inside a copyable prompt."""
    from practicegraph.analysis.playbook import _metric_text

    assert _metric_text("late_night_drift", 12) == "12 points"

    moves = compose_playbook([("late_night_drift", 12)])
    move = next(m for m in moves if m.move_id == "late_night_drift")
    assert "12 points" in move.why
    assert "12%" not in move.why
    assert "12%" not in move.prompt
    # the claim itself must name the baseline, not a share of the week
    assert "baseline" in move.why.lower()


def test_cost_multiple_renders_as_a_multiple_not_a_percent() -> None:
    """context_carried's metric is cost_multiple_tenths: 23 means 2.3x.
    It was declared "pct", so a 2.3x multiple rendered as "23%"."""
    from practicegraph.analysis.playbook import _metric_text

    assert _metric_text("context_carried", 23) == "2.3x"
    assert _metric_text("context_carried", 40) == "4.0x"

    moves = compose_playbook([("context_carried", 23)])
    move = next(m for m in moves if m.move_id == "context_carried")
    assert "2.3x" in move.why
    assert "23%" not in move.why
    assert "23%" not in move.prompt


def test_both_registers_carry_the_corrected_metric() -> None:
    """The plain register has its own prompt string; the fix must reach it."""
    for register in ("repo", "plain"):
        moves = compose_playbook(
            [("late_night_drift", 12), ("context_carried", 23)], register
        )
        by_id = {m.move_id: m for m in moves}
        assert "12 points" in by_id["late_night_drift"].prompt
        assert "2.3x" in by_id["context_carried"].prompt
        assert "12%" not in by_id["late_night_drift"].prompt
        assert "23%" not in by_id["context_carried"].prompt
