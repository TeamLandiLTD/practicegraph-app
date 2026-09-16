"""Closed wire schema (INV-3, FR-EMT-1): pins, validator behavior, and the
no-echo discipline for validation errors."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from conftest import build_fixture_history, build_fixture_payload
from practicegraph.analysis.capability_ledger import PracticeEntry, record_outcome, write_practice
from practicegraph.analysis.capability_units import CapabilityUnitSummary
from practicegraph.config import save_capability_paths
from practicegraph.emit import build_payload_for_day, synthetic_example_payload
from practicegraph.store import Store
from practicegraph.wire import (
    EMIT_SCHEMA_VERSION,
    ENGAGEMENT_KEYS,
    SUPPORTED_SCHEMA_VERSIONS,
    ModelFamily,
    model_family,
    schema_version_supported,
    validate_emit,
)

_STORE, _HEALTH = build_fixture_history()


def _valid_payload() -> dict[str, object]:
    return build_fixture_payload(
        _STORE, _HEALTH, "7c3de1f0-4b5a-4f7e-9a34-0123456789ab"
    )


def test_model_families_are_pinned() -> None:
    assert [m.value for m in ModelFamily] == [
        "claude_fable",
        "claude_mythos",
        "claude_opus",
        "claude_sonnet",
        "claude_haiku",
        "gpt_5_codex",
        "gpt_5_mini",
        "gpt_5",
        "gpt_4",
        "o_series",
        "other",
    ]
    assert model_family("claude-sonnet-4-20250514") is ModelFamily.CLAUDE_SONNET
    assert model_family("claude-fable-5") is ModelFamily.CLAUDE_FABLE
    assert model_family("claude-mythos-5") is ModelFamily.CLAUDE_MYTHOS
    assert model_family("gpt-5-codex") is ModelFamily.GPT_5_CODEX
    assert model_family("gpt-5.3-codex") is ModelFamily.GPT_5_CODEX
    assert model_family("gpt-5.4-mini") is ModelFamily.GPT_5_MINI
    assert model_family("gpt-5.5") is ModelFamily.GPT_5
    assert model_family("weird-local-model") is ModelFamily.OTHER


def test_fixture_payload_is_valid_and_exact() -> None:
    payload = _valid_payload()
    assert validate_emit(payload) == []
    # Sidechain compute counts in every cost figure (P3 gates behavior only).
    assert payload["estimated_cost_micro_usd"] == 42005
    assert payload["contributors"] == 1
    assert payload["content_present"] is False
    assert payload["identity_present"] is False
    assert payload["tools_observed"] == ["claude_code", "codex"]
    assert payload["spend_by_family"] == {
        "claude_opus": 5175,
        "claude_sonnet": 26580,
        "gpt_5_codex": 10250,
    }
    assert set(payload["engagement"]) == set(ENGAGEMENT_KEYS)  # type: ignore[arg-type]


def test_synthetic_example_passes_the_validator() -> None:
    assert validate_emit(synthetic_example_payload()) == []


def test_unknown_field_rejected_without_echo() -> None:
    payload = _valid_payload()
    payload["hostname_of_user"] = "very-secret-host"
    errors = validate_emit(payload)
    assert "unknown_field" in errors
    assert all("hostname" not in code and "secret" not in code for code in errors)


def test_missing_field_rejected() -> None:
    payload = _valid_payload()
    del payload["tokens"]
    assert "missing_field" in validate_emit(payload)


def test_presence_flag_invariants_enforced() -> None:
    payload = _valid_payload()
    payload["content_present"] = True
    assert "invariant_violation" in validate_emit(payload)
    payload = _valid_payload()
    payload["contributors"] = 2
    assert "invariant_violation" in validate_emit(payload)


def test_free_text_shaped_values_rejected() -> None:
    payload = _valid_payload()
    payload["org_id"] = "acme corp with spaces and a long sentence"
    assert "format_error" in validate_emit(payload)


def test_leaky_string_values_rejected() -> None:
    payload = _valid_payload()
    payload["rate_card_version"] = "not-a-leak"
    assert validate_emit(payload) == []
    # A value that smells like an email/path/key is rejected outright even if
    # a future field were sloppy about formats (defense in depth).
    payload["agent_version"] = "bob@example.org"
    errors = validate_emit(payload)
    assert "format_error" in errors or "value_leak" in errors


def test_schema_version_gate_runs_before_validation() -> None:
    assert EMIT_SCHEMA_VERSION == 2
    assert SUPPORTED_SCHEMA_VERSIONS == (1, 2)
    assert schema_version_supported(_valid_payload())
    assert schema_version_supported({"schema_version": 1})
    assert not schema_version_supported({"schema_version": 999})
    assert not schema_version_supported({"schema_version": "1"})
    assert not schema_version_supported([])


def _as_v1(payload: dict[str, object]) -> dict[str, object]:
    downgraded = dict(payload)
    downgraded["schema_version"] = 1
    del downgraded["work_type_sessions"]
    del downgraded["maturity"]
    return downgraded


def test_v1_payloads_from_older_agents_stay_valid() -> None:
    """The v2 bump is additive (NFR-PRV-2): v1 validates as v1."""
    assert validate_emit(_as_v1(_valid_payload())) == []
    # ...but v2 fields on a v1 payload are unknown fields.
    mixed = dict(_valid_payload())
    mixed["schema_version"] = 1
    assert "unknown_field" in validate_emit(mixed)
    # ...and a v2 payload missing the new fields is incomplete.
    stripped = _as_v1(_valid_payload())
    stripped["schema_version"] = 2
    assert "missing_field" in validate_emit(stripped)


def test_v2_fields_are_closed() -> None:
    payload = _valid_payload()
    # The wire mix keeps its pre-P3 semantics (all sessions, including the
    # sidechain converse-shaped one) — only report surfaces gate on
    # interactive marks.
    assert payload["work_type_sessions"] == {
        "build": 1, "investigate": 1, "converse": 2, "unknown": 0,
    }
    maturity = payload["maturity"]
    assert isinstance(maturity, dict)
    assert set(maturity) == {"cache_reuse", "tool_usage", "multi_tool", "consistency"}

    bad_level = dict(_valid_payload())
    bad_level["maturity"] = {**maturity, "cache_reuse": "galaxy_brain_tier"}
    assert "enum_error" in validate_emit(bad_level)

    bad_work = dict(_valid_payload())
    bad_work["work_type_sessions"] = {"build": 1, "surprise": 2}
    errors = validate_emit(bad_work)
    assert "unknown_field" in errors or "missing_field" in errors


def test_payload_round_trips_as_compact_json() -> None:
    payload = _valid_payload()
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    assert validate_emit(json.loads(encoded)) == []


def test_wire_timestamp_free() -> None:
    """The payload carries a day and versions — no fine-grained timestamps
    that could fingerprint an individual's working hours (INV-4 spirit)."""
    payload = _valid_payload()
    encoded = json.dumps(payload)
    assert datetime.now(UTC).strftime("%H:%M") not in encoded
    assert "T" not in str(payload["day"])


def test_focus_data_never_reaches_the_wire() -> None:
    """NFR-PRV-6: no field name anywhere in a prepared payload may carry
    focus/wellbeing or performance vocabulary — enforced by name scan,
    forever."""
    from practicegraph.analysis.advisor_receipts import (
        WIRE_FORBIDDEN_TERMS as ADVISOR_TERMS,
    )
    from practicegraph.analysis.focus import WIRE_FORBIDDEN_TERMS
    from practicegraph.analysis.perception import (
        WIRE_FORBIDDEN_TERMS as PERCEPTION_TERMS,
    )
    from practicegraph.analysis.performance import (
        WIRE_FORBIDDEN_TERMS as PERFORMANCE_TERMS,
    )
    from practicegraph.analysis.reliance import (
        WIRE_FORBIDDEN_TERMS as RELIANCE_TERMS,
    )
    from practicegraph.analysis.schedule import WIRE_FORBIDDEN_TERMS as SCHEDULE_TERMS
    from practicegraph.analysis.sessiontail import (
        WIRE_FORBIDDEN_TERMS as SESSIONTAIL_TERMS,
    )
    from practicegraph.analysis.workunits import (
        WIRE_FORBIDDEN_TERMS as WORKUNIT_TERMS,
    )
    usage_terms = ("account_hash", "pool_hash", "parent_id", "replay_", "session_evidence",
                   "transcript", "characters", "resets_at", "quota_snapshots")

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        for term in (
            WIRE_FORBIDDEN_TERMS
            + PERFORMANCE_TERMS
            + PERCEPTION_TERMS
            + SCHEDULE_TERMS
            + ADVISOR_TERMS
            + RELIANCE_TERMS
            + WORKUNIT_TERMS
            + SESSIONTAIL_TERMS
            + usage_terms
        ):
            assert term not in key.lower()


def test_reflection_text_has_no_path_to_the_wire() -> None:
    """The reflection band — and its model-restyled variant — is a local view
    surface only (NFR-PRV-6). The emit builder must not import or read the
    reflection composer, the restyle module, or the style cache key: a payload
    is assembled from counters, never from any sentence shown to the person."""
    import inspect

    from practicegraph import emit
    from practicegraph.report.restyle import STYLE_META_KEY

    source = inspect.getsource(emit)
    assert "reflection" not in source.lower()
    assert "restyle" not in source.lower()
    assert STYLE_META_KEY not in source
    # The emit builder reads no free-form meta blob at all — style/wording
    # caches live in meta, and a payload never sources a sentence from there.
    assert "meta_get" not in source


def test_runway_has_no_path_to_the_wire() -> None:
    """Quota runway (A1) is a local daily-glance surface built from rate-limit
    readings, which are LOCAL-ONLY forever (NFR-PRV-6). The emit builder must
    not import the runway composer or touch the readings, and no payload field
    may carry runway/rate-limit vocabulary."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "runway" not in source
    assert "rate_limit" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "runway" not in key.lower()
        assert "rate_limit" not in key.lower()


def test_day_close_has_no_path_to_the_wire() -> None:
    """Close the day (A2) is a local shutdown flag in the meta table. The emit
    builder must not import the dayclose module or read the flag, and no payload
    field may carry day-closed vocabulary (NFR-PRV-6)."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "dayclose" not in source
    assert "day_closed" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "dayclose" not in key.lower()
        assert "day_closed" not in key.lower()


def test_economy_reading_has_no_path_to_the_wire() -> None:
    """The economy reading (W1.1) — capability per dollar/window, the lever,
    the billing lens, and the first-open retrospective — is a local view
    surface only (NFR-PRV-6). The emit builder must not import the economy
    composer or read its meta key, and no payload field may carry its
    vocabulary. (The raw cost/token counters it composes over were already
    wire-fields before this feature; nothing new is added.)"""
    import inspect

    from practicegraph import emit
    from practicegraph.analysis.economy import ECONOMY_INTRO_META_KEY

    source = inspect.getsource(emit)
    lower = source.lower()
    assert "economy" not in lower
    assert "billing" not in lower
    assert "lever" not in lower
    assert ECONOMY_INTRO_META_KEY not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "economy" not in key.lower()
        assert "billing" not in key.lower()
        assert "lever" not in key.lower()
        assert "week_window" not in key.lower()


def test_the_skill_outcome_ledger_has_no_path_to_the_wire() -> None:
    """The outcome ledger holds what a named person took and what their own
    measures did afterwards — an intervention-and-response record, which is
    exactly the shape an employer would want and exactly why it stays local
    (NFR-PRV-6). The emit builder must not import it or read its key, and no
    payload field may carry its vocabulary."""
    import inspect

    from practicegraph import emit
    from practicegraph.analysis.skill_ledger import (
        SKILL_LEDGER_KEY,
        WIRE_FORBIDDEN_TERMS,
    )

    source = inspect.getsource(emit)
    lower = source.lower()
    assert "skill_audit" not in lower
    assert "record_skill_target" not in lower
    assert SKILL_LEDGER_KEY not in source
    for term in WIRE_FORBIDDEN_TERMS:
        assert term not in lower, term

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "skill" not in key.lower(), key
        for term in WIRE_FORBIDDEN_TERMS:
            assert term not in key.lower(), key


def test_collaboration_shape_has_no_path_to_the_wire() -> None:
    """How you worked with it is a behavioural reading of the person's own
    exchange, and it is local-only forever (NFR-PRV-6). The emit builder must
    not import or name the composer, and no payload field may carry its
    vocabulary. The stakes are specific: a shipped "reliance" figure would be
    exactly the number an employer would want, which is the reason it stays on
    the machine."""
    import inspect

    from practicegraph import emit
    from practicegraph.analysis.reliance import WIRE_FORBIDDEN_TERMS

    source = inspect.getsource(emit).lower()
    assert "compose_reliance" not in source
    for term in WIRE_FORBIDDEN_TERMS:
        assert term not in source, term

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        for term in WIRE_FORBIDDEN_TERMS:
            assert term not in key.lower(), key


def test_calibration_has_no_path_to_the_wire() -> None:
    """The Calibration Mirror holds a self-report joined to a session identity
    — among the most sensitive local records the product keeps. It is
    local-only forever (NFR-PRV-6): the emit builder must not import it or
    read its ledger key, and no payload field may carry its vocabulary."""
    import inspect

    from practicegraph import emit
    from practicegraph.analysis.calibration import CALIBRATION_LEDGER_KEY

    source = inspect.getsource(emit)
    lower = source.lower()
    assert "calibration" not in lower
    assert "felt" not in lower
    assert "compose_calibration" not in lower
    assert CALIBRATION_LEDGER_KEY not in source
    # Note: the bare word "estimate" is NOT banned here — cost figures are
    # estimates and `is_estimate` is a legitimate payload field. What may
    # never travel is the self-report vocabulary asserted above.

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "calibration" not in key.lower()
        assert "felt" not in key.lower()
        assert "felt_bucket" not in key.lower()


def test_session_grain_has_no_path_to_the_wire() -> None:
    """Session grain (W2.0) is local-only, and `session_key` is an IDENTITY —
    the strongest reason it must never travel (NFR-PRV-6, INV-1). The emit
    builder must not import the session composer or read the table, and no
    payload field may carry session-grain vocabulary."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "session_totals" not in source
    assert "session_key" not in source
    assert "compose_sessions" not in source
    assert "context_tax" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "session_key" not in key.lower()
        assert "session_totals" not in key.lower()
        assert "context_tax" not in key.lower()
        assert "receipt" not in key.lower()


def test_coach_ledger_has_no_path_to_the_wire() -> None:
    """The acknowledgment ledger (A3) is a local relationship record — cue
    history and acknowledgments never travel up (INV-2 passive, NFR-PRV-6). The
    emit builder must not import the ledger machinery or read the ledger key,
    and no payload field may carry ledger/acknowledgment vocabulary."""
    import inspect

    from practicegraph import emit
    from practicegraph.report.coach import LEDGER_META_KEY

    source = inspect.getsource(emit)
    lower = source.lower()
    assert "ledger" not in lower
    assert "coach_ack" not in lower
    assert "acknowledg" not in lower
    assert LEDGER_META_KEY not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "ledger" not in key.lower()
        assert "coach_ack" not in key.lower()
        assert "acknowledg" not in key.lower()


def test_weekly_reading_has_no_path_to_the_wire() -> None:
    """The weekly reading (A4) is a local in-app ritual composed from the
    person's own week-over-week data. The emit builder must not import the
    weekly composer, and no payload field may carry weekly-reading vocabulary
    (NFR-PRV-6)."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "weekly_reading" not in source
    assert "compose_weekly" not in source
    assert "next_focus" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "weekly_reading" not in key.lower()
        assert "next_focus" not in key.lower()


def test_skill_matching_has_no_path_to_the_wire() -> None:
    """The skill registry is served BY the passive server and matched on the
    endpoint against local signals; the match — which skills fit this person —
    must never travel back up (INV-2 passive, NFR-PRV-6). The emit builder must
    not touch the skills module, and 'skill' must not appear as a payload field."""
    import inspect

    from practicegraph import emit

    assert "skill" not in inspect.getsource(emit).lower()
    # And no wire payload field name carries skill/work-type vocabulary.
    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "skill" not in key.lower()


def test_skill_install_metadata_has_no_path_to_the_wire() -> None:
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    for term in ("skill", "install_command", "source_url", "clipboard", "skill-installer"):
        assert term not in source


def test_briefings_have_no_path_to_the_wire() -> None:
    """The news feed is a local display surface: the endpoint shows briefings
    but never reports which were seen, opened, or dismissed (that would make the
    passive server an ad-analytics backend, breaking NFR-PRV-6). The emit builder
    must not touch the briefings module, and no payload field carries the term."""
    import inspect

    from practicegraph import emit

    assert "briefing" not in inspect.getsource(emit).lower()

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "briefing" not in key.lower()
        assert "news" not in key.lower()


def test_coaching_has_no_path_to_the_wire() -> None:
    """The training coach is a local view surface — coaching about a person's
    recovery/focus/judgment must never travel up (NFR-PRV-6). The emit builder
    imports nothing coach-related, and no payload field carries the vocabulary."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "coach" not in source
    assert "pillar" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "coach" not in key.lower()
        assert "pillar" not in key.lower()


def test_conditioning_has_no_path_to_the_wire() -> None:
    """The conditioning readout (capacity / load balance / composure) is a
    local view surface about a person's pacing — it must never travel up
    (NFR-PRV-6). The emit builder imports nothing from it, and no payload
    field carries its vocabulary."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "conditioning" not in source
    assert "composure" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(str(key))
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        assert "conditioning" not in key.lower()
        assert "composure" not in key.lower()
        assert "load_balance" not in key.lower()


def test_observation_has_no_path_to_the_wire() -> None:
    """The qualified observation is a local reading of the person's own
    work pattern. It renders in the app and never leaves the machine
    (NFR-PRV-6): the emit builder must not import the composer, and no
    payload field name may carry its vocabulary."""
    import inspect

    from practicegraph import emit

    source = inspect.getsource(emit).lower()
    assert "compose_observation" not in source
    assert "practice_observation" not in source

    def _keys(value: object) -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                found.append(key)
                found.extend(_keys(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(_keys(item))
        return found

    for key in _keys(_valid_payload()):
        for term in ("observation", "caveat", "confidence"):
            assert term not in key.lower(), key


def test_private_capability_data_has_no_path_to_the_wire(tmp_path: Path) -> None:
    """Real private capability metadata cannot affect the production payload."""
    from practicegraph.analysis.capability import WIRE_FORBIDDEN_TERMS

    data_dir = tmp_path / "private-capability-data"
    store = Store.in_data_dir(data_dir)
    store.migrate()
    builder_args = {
        "store": store,
        "day": "2026-08-14",
        "org_id": "acme-eng",
        "engagement": {"report_generated": 1},
        "emit_id": "7c3de1f0-4b5a-4f7e-9a34-0123456789ab",
        "platform": "windows",
        "source_health": [],
    }
    before = build_payload_for_day(**builder_args)
    summary = CapabilityUnitSummary(
        unit_key="private-capability-unit",
        first_ts=datetime(2026, 8, 14, 9, tzinfo=UTC),
        last_ts=datetime(2026, 8, 14, 10, tzinfo=UTC),
        assistant_turns=6,
        prompt_tokens=1_200,
        cost_micro_usd=250,
        retries=2,
        command_failures=2,
        git_commit_attempts=0,
        test_run_attempts=0,
        files_created=0,
        doc_files_created=0,
        export_writes=0,
        files_edited=0,
        knowledge_evidence=True,
        software_evidence=False,
    )
    record_outcome(store, summary, "partly", datetime(2026, 8, 14).date())
    write_practice(
        store,
        PracticeEntry(
            practice_id="diagnose_before_retry",
            title="Pause before retrying",
            path="shared",
            accepted_day="2026-08-14",
            baseline_unit_key=summary.unit_key,
            baseline_last_ts=summary.last_ts.isoformat(),
            baseline_value=summary.retries,
        ),
    )
    from practicegraph.analysis.capability_ledger import finish_practice

    finish_practice(store, "helpful", datetime(2026, 8, 15).date())
    saved_prefs = save_capability_paths(data_dir, ("knowledge",))
    assert saved_prefs.capability_paths == ("knowledge",)
    assert saved_prefs.capability_paths_confirmed is True

    after = build_payload_for_day(**builder_args)

    assert validate_emit(before) == []
    assert validate_emit(after) == []
    assert after == before
    encoded = json.dumps(after, sort_keys=True).lower()
    for term in WIRE_FORBIDDEN_TERMS:
        assert term not in encoded
