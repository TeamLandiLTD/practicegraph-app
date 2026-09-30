"""The skill registry: a server-hosted catalog the endpoint matches LOCALLY to
the person's own recent work. These tests pin the contract that keeps it inside
the invariants — inbound copy is validated exactly like every catalog (closed
schema, caps, lexicon + leak), no runnable payload is accepted, matching never
depends on anything sent to the server, and the whole thing is deterministic."""

from __future__ import annotations

import json

from practicegraph.analysis.skills import (
    BUNDLED_SKILLS,
    MAX_PROMPT_LEN,
    SKILL_TOOLS,
    SKILL_WORK_TYPES,
    Skill,
    parse_skills_artifact,
    relevant_skills,
    skills_artifact,
)
from practicegraph.privacy import leak_findings, lexicon_violations


def test_bundled_registry_round_trips_and_is_scan_clean() -> None:
    """The bundled artifact must survive its own strict validator, and every
    copy field stays inside the observational-copy + no-leak rules."""
    artifact = skills_artifact()
    parsed = parse_skills_artifact(artifact)
    assert parsed is not None
    assert len(parsed) == len(BUNDLED_SKILLS)
    for skill in BUNDLED_SKILLS:
        for text in (skill.title, skill.summary, skill.prompt):
            assert lexicon_violations(text) == []
            assert leak_findings(text) == []
        assert set(skill.work_types) <= set(SKILL_WORK_TYPES)
        assert set(skill.tools) <= set(SKILL_TOOLS)


def test_validator_rejects_bad_artifacts() -> None:
    good = skills_artifact()

    # Unknown top-level key.
    assert parse_skills_artifact({**good, "extra": 1}) is None
    # An entry with an unknown field (e.g. a smuggled executable payload).
    bad_field = json.loads(json.dumps(good))
    bad_field["entries"][0]["script"] = "rm -rf /"
    assert parse_skills_artifact(bad_field) is None
    # A work-type tag outside the closed vocabulary.
    bad_wt = json.loads(json.dumps(good))
    bad_wt["entries"][0]["work_types"] = ["build", "sre"]
    assert parse_skills_artifact(bad_wt) is None
    # An empty tag list is not allowed.
    bad_empty = json.loads(json.dumps(good))
    bad_empty["entries"][0]["tools"] = []
    assert parse_skills_artifact(bad_empty) is None
    # Forbidden lexicon in the copyable prompt.
    bad_lex = json.loads(json.dumps(good))
    bad_lex["entries"][0]["prompt"] = "This trick spikes your dopamine, keep going."
    assert parse_skills_artifact(bad_lex) is None
    # A secret-shaped string in copy is a leak.
    bad_leak = json.loads(json.dumps(good))
    bad_leak["entries"][0]["summary"] = "use token sk-abcdef0123456789abcdef0123"
    assert parse_skills_artifact(bad_leak) is None
    # Oversized prompt.
    bad_len = json.loads(json.dumps(good))
    bad_len["entries"][0]["prompt"] = "x" * (MAX_PROMPT_LEN + 1)
    assert parse_skills_artifact(bad_len) is None
    # Duplicate ids.
    dup = json.loads(json.dumps(good))
    dup["entries"].append(dict(dup["entries"][0]))
    assert parse_skills_artifact(dup) is None
    # A finding tag outside the closed finding vocabulary.
    bad_finding = json.loads(json.dumps(good))
    bad_finding["entries"][0]["findings"] = ["made_up_finding"]
    assert parse_skills_artifact(bad_finding) is None
    # A dimension tag outside the closed dimension vocabulary.
    bad_dim = json.loads(json.dumps(good))
    bad_dim["entries"][0]["dimensions"] = ["vibes"]
    assert parse_skills_artifact(bad_dim) is None
    # But findings/dimensions are OPTIONAL: absent or [] round-trips fine.
    ok_empty = json.loads(json.dumps(good))
    ok_empty["entries"][0]["findings"] = []
    assert parse_skills_artifact(ok_empty) is not None


def test_no_executable_field_is_ever_accepted() -> None:
    """A skill is display copy + a prompt string, never runnable code. Any
    entry key beyond the closed set (code/script/exec/files/run) is rejected."""
    good = skills_artifact()
    for smuggled in ("code", "script", "exec", "files", "run", "cmd"):
        bad = json.loads(json.dumps(good))
        bad["entries"][0][smuggled] = "payload"
        assert parse_skills_artifact(bad) is None, f"{smuggled} must be rejected"


def _ids(matches):
    return [m.skill.skill_id for m in matches]


def test_local_match_ranks_by_the_persons_own_work() -> None:
    """Matching depends ONLY on locally-derived signals (tools seen + the
    work-type mix); a work-type with zero sessions never pulls its skills, and
    the dominant work-type's skills rank first. Deterministic ordering."""
    # Build-heavy Claude user, no findings / weak dims active.
    mix = {"build": 12, "investigate": 3, "converse": 0, "unknown": 1}
    ranked = relevant_skills(BUNDLED_SKILLS, {"claude_code"}, mix)
    ids = _ids(ranked)
    # Build-tagged skills lead (weight 12), investigate next (3), any/any last.
    assert ids[0] in {"scope-before-build", "route-routine-to-midtier"}
    assert "read-the-error-first" in ids  # investigate present (3 sessions)
    # A converse skill must NOT appear — zero converse sessions.
    assert "converse-to-spec" not in ids
    # Deterministic: same inputs, same order.
    assert _ids(relevant_skills(BUNDLED_SKILLS, {"claude_code"}, mix)) == ids


def test_tool_filter_excludes_unused_tools() -> None:
    """A skill scoped to a tool the person never used is not a candidate;
    tool-agnostic skills still are."""
    codex_only = Skill(
        "codex-thing", "Codex only", "only for codex", "do the codex thing",
        ("any",), ("codex",),
    )
    pool = (*BUNDLED_SKILLS, codex_only)
    assert "codex-thing" not in _ids(relevant_skills(pool, {"claude_code"}, {"build": 5}))
    assert "codex-thing" in _ids(relevant_skills(pool, {"codex"}, {"build": 5}))


def test_no_work_no_skills_beyond_agnostic() -> None:
    """With no work-type activity, no findings and no weak dims, only 'any'-work
    skills can surface (they need no signal); nothing specific is invented."""
    ranked = relevant_skills(BUNDLED_SKILLS, {"claude_code"}, {})
    for match in ranked:
        assert "any" in match.skill.work_types
        assert match.score == 0


def test_active_finding_outranks_work_type_fit() -> None:
    """A skill that ANSWERS a finding that fired now outranks one that merely
    fits the dominant work-type — the strongest, most-current reason wins, and
    the reason is surfaced."""
    # Build-heavy, but command_friction fired: the error-first skill (tagged to
    # that finding) must lead the plain build-fit scope skill.
    ranked = relevant_skills(
        BUNDLED_SKILLS, {"claude_code"},
        {"build": 50}, active_findings={"command_friction"},
    )
    assert ranked[0].skill.skill_id == "read-the-error-first"
    assert any("command friction" in r for r in ranked[0].reasons)
    # No finding active -> that skill is not boosted above build work.
    plain = relevant_skills(BUNDLED_SKILLS, {"claude_code"}, {"build": 50})
    assert plain[0].skill.skill_id != "read-the-error-first"


def test_weak_dimension_surfaces_and_outranks_plain_work() -> None:
    """A skill that strengthens a measured-weak dimension outranks a plain
    work-type fit, but ranks below one answering an active finding."""
    ranked = relevant_skills(
        BUNDLED_SKILLS, {"claude_code"},
        {"build": 40}, weak_dimensions={"model_economy"},
    )
    # route-routine (model_economy) beats scope-before-build (plain build).
    order = _ids(ranked)
    assert order.index("route-routine-to-midtier") < order.index("scope-before-build")
    match = next(m for m in ranked if m.skill.skill_id == "route-routine-to-midtier")
    assert any("model economy" in r for r in match.reasons)


def test_finding_beats_weak_dimension_beats_work() -> None:
    """The full priority order holds: finding > weak dimension > work-type."""
    ranked = relevant_skills(
        BUNDLED_SKILLS, {"claude_code"}, {"build": 5},
        active_findings={"command_friction"},
        weak_dimensions={"model_economy"},
    )
    order = _ids(ranked)
    # error-first (finding) < route-routine (weak dim) < scope (plain work).
    assert order.index("read-the-error-first") < order.index("route-routine-to-midtier")
    assert order.index("route-routine-to-midtier") < order.index("scope-before-build")


def test_richer_matching_stays_deterministic() -> None:
    """Same signals in, same ranked ids out — the enrichment adds signals, not
    nondeterminism (INV-6)."""
    args = (
        BUNDLED_SKILLS, {"claude_code", "codex"}, {"build": 7, "investigate": 4},
    )
    kwargs = {
        "active_findings": {"premium_heavy", "interruption_cluster"},
        "weak_dimensions": {"context_hygiene"},
    }
    first = relevant_skills(*args, **kwargs)
    second = relevant_skills(*args, **kwargs)
    assert [(m.skill.skill_id, m.score, m.reasons) for m in first] == [
        (m.skill.skill_id, m.score, m.reasons) for m in second
    ]


def test_diversity_caps_stop_one_topic_flooding_the_shelf() -> None:
    """One hot finding must not fill the shelf with near-duplicates: at most
    MAX_PER_FINDING skills per answered finding and MAX_PER_ROLE per role —
    the strongest per topic keeps its slot, the next topic gets the rest."""
    flood = (
        *(
            Skill(
                f"context-variant-{index}", f"Context variant {index}",
                "keep the working set lean", "prune the context",
                ("any",), ("any",), role="context",
                findings=("context_bloat",),
            )
            for index in range(5)
        ),
        Skill(
            "focus-one-thread", "One thread", "one thread at a time",
            "work one thread", ("any",), ("any",), role="focus",
            findings=("interruption_cluster",),
        ),
    )
    ranked = relevant_skills(
        flood, {"claude_code"}, {"build": 3},
        active_findings={"context_bloat", "interruption_cluster"},
    )
    ids = _ids(ranked)
    assert sum(1 for skill_id in ids if skill_id.startswith("context-variant")) == 2
    assert "focus-one-thread" in ids


def test_ties_rotate_by_week_not_alphabet() -> None:
    """Same-band ties must not resolve alphabetically forever (a registry
    author could win the shelf by naming); with a day, ties rotate by ISO
    week, deterministically for that week."""
    from datetime import date, timedelta

    pool = tuple(
        Skill(
            f"skill-{letter}", f"Skill {letter}", "summary", "prompt",
            ("any",), ("any",), role=f"role-{letter}",
        )
        for letter in "abcdefgh"
    )
    week_one = relevant_skills(pool, {"codex"}, {"build": 1}, day=date(2026, 7, 6))
    week_two = relevant_skills(
        pool, {"codex"}, {"build": 1}, day=date(2026, 7, 6) + timedelta(days=7)
    )
    same_week = relevant_skills(pool, {"codex"}, {"build": 1}, day=date(2026, 7, 8))
    assert _ids(week_one) == _ids(same_week)  # stable within a week
    assert _ids(week_one) != _ids(week_two)  # rotates across weeks
    # Legacy path (no day): alphabetical, unchanged.
    assert _ids(relevant_skills(pool, {"codex"}, {"build": 1}))[:2] == [
        "skill-a", "skill-b",
    ]


def test_recently_copied_skills_sit_the_shelf_out(tmp_path) -> None:
    """A copied skill is delivered goods: recorded through the ledger, it
    retires from matching for SKILL_COPY_RETIRE_DAYS, then returns."""
    from datetime import date, timedelta

    from practicegraph.analysis.skills import (
        SKILL_COPY_RETIRE_DAYS,
        recently_copied_skills,
        record_skill_copy,
    )
    from practicegraph.store import Store

    store = Store(tmp_path / "state.db")
    store.migrate()
    today = date(2026, 7, 19)
    assert record_skill_copy(store, "scope-before-build", today)
    assert not record_skill_copy(store, "NOT A SLUG", today)

    copied = recently_copied_skills(store, today)
    assert copied == {"scope-before-build"}
    ranked = relevant_skills(
        BUNDLED_SKILLS, {"claude_code"}, {"build": 5}, recently_copied=copied
    )
    assert "scope-before-build" not in _ids(ranked)

    # Past the retire window the ledger stops excluding it.
    later = today + timedelta(days=SKILL_COPY_RETIRE_DAYS + 1)
    assert recently_copied_skills(store, later) == set()
    # And a new copy on a later day prunes the stale entry from the meta value.
    assert record_skill_copy(store, "explain-it-back", later)
    assert recently_copied_skills(store, later) == {"explain-it-back"}


def test_a_renamed_dimension_does_not_invalidate_a_published_registry() -> None:
    """Found on the first macOS run, and it was silent on Windows too.

    `recovery` was renamed to `working_pattern` on 2026-07-26. The public skill
    registry was published on 2026-07-09 and still tags two of its fifty skills
    with the old id. Artifact validation is deliberately all-or-nothing — a
    server must not be able to push a partially malformed catalog — so those
    two stale tags rejected ALL FIFTY skills, reported only as
    `skills_pull=invalid_artifact` in a tick summary nobody reads.

    Renaming a closed vocabulary that SERVED artifacts key on is a breaking
    change. Retired ids therefore stay accepted and are rewritten on the way
    in, so nothing downstream sees a vocabulary that no longer exists."""
    from practicegraph.analysis.skills import (
        RETIRED_DIMENSIONS,
        parse_skills_artifact,
    )

    assert RETIRED_DIMENSIONS["recovery"] == "working_pattern"

    def entry(**over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "id": "focus-break-at-session-limit",
            "title": "Break at the session limit",
            "summary": "Stop at a boundary rather than pushing through it.",
            "prompt": "When a session reaches its limit, stop and summarise.",
            "work_types": ["any"],
            "tools": ["any"],
            "dimensions": ["recovery"],
        }
        base.update(over)
        return base

    artifact = {"skills_version": "v1", "entries": [entry()]}
    parsed = parse_skills_artifact(artifact)
    assert parsed is not None, "a retired dimension id must not reject the file"
    # ...and it is rewritten, not merely tolerated.
    assert parsed[0].dimensions == ("working_pattern",)

    # A genuinely unknown id is still refused: this is an alias, not an opening.
    unknown = {"skills_version": "v1", "entries": [entry(dimensions=["vibes"])]}
    assert parse_skills_artifact(unknown) is None

    # And one bad entry still sinks the whole document - the all-or-nothing
    # stance is the security property, and it is unchanged.
    mixed = {"skills_version": "v1", "entries": [entry(), entry(
        id="other-skill", dimensions=["not_a_dimension"])]}
    assert parse_skills_artifact(mixed) is None


def test_a_retired_finding_does_not_invalidate_a_published_registry() -> None:
    """The same trap, one vocabulary over — and this time it was seen coming.

    `rework_heavy` was retired on 2026-08-13 because its only signal never
    fires. Thirteen of the fifty published skills tag it. Without the shim,
    dropping the id from FINDING_IDS would have rejected the entire catalog
    exactly as the renamed dimension did.

    Retirement differs from renaming in one way that matters: a rename has a
    successor to rewrite to, a retirement does not. Inventing one would re-tag
    thirteen skills as answers to a problem nobody wrote them for. So the id
    is accepted and DROPPED — the skill stays valid and keeps its other tags."""
    from practicegraph.analysis.skills import (
        RETIRED_FINDINGS,
        parse_skills_artifact,
    )

    assert "rework_heavy" in RETIRED_FINDINGS

    def entry(**over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "id": "testing-repro-before-fix",
            "title": "Reproduce before you fix",
            "summary": "Get the failure in front of you before changing code.",
            "prompt": "Before fixing, reproduce the failure in the smallest form.",
            "work_types": ["any"],
            "tools": ["any"],
            "findings": ["rework_heavy", "command_friction"],
        }
        base.update(over)
        return base

    parsed = parse_skills_artifact({"skills_version": "v1", "entries": [entry()]})
    assert parsed is not None, "a retired finding id must not reject the file"
    # Dropped, not rewritten: the live tag survives, the dead one goes.
    assert parsed[0].findings == ("command_friction",)

    # A skill tagged ONLY with the retired id stays valid and simply stops
    # matching on it — it still surfaces on work type and dimension.
    only = {"skills_version": "v1", "entries": [entry(findings=["rework_heavy"])]}
    parsed_only = parse_skills_artifact(only)
    assert parsed_only is not None
    assert parsed_only[0].findings == ()

    # An unknown finding id is still refused. This is a retirement, not an
    # opening in the closed vocabulary.
    unknown = {"skills_version": "v1", "entries": [entry(findings=["vibes"])]}
    assert parse_skills_artifact(unknown) is None
