"""Doctor triage command (FR-DIA-1/2/3)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXTURE_ENV
from practicegraph.agent import initialize
from practicegraph.doctor import run_doctor
from practicegraph.privacy import leak_findings

CLOSED_STATUSES = {"healthy", "unhealthy", "not_applicable", "skipped"}


def _healthy_env(tmp_path: Path) -> dict[str, str]:
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path / "data")
    initialize(env)  # a healthy install is an initialized one
    return env


def test_healthy_install_exits_zero(tmp_path: Path) -> None:
    document, exit_code = run_doctor(_healthy_env(tmp_path))
    assert exit_code == 0
    assert document["status"] == "healthy"
    by_id = {c["check"]: c for c in document["checks"]}
    from practicegraph.store import STORE_SCHEMA_VERSION

    assert by_id["state_db"]["status"] == "healthy"
    assert by_id["state_db"]["detail"] == {"schema_version": str(STORE_SCHEMA_VERSION)}
    assert by_id["emit_queue"]["status"] == "healthy"
    assert by_id["emit_queue"]["detail"]["pending"] == "0"


def test_check_ids_and_statuses_are_closed(tmp_path: Path) -> None:
    document, _ = run_doctor(_healthy_env(tmp_path))
    checks = document["checks"]
    assert isinstance(checks, list)
    assert [c["check"] for c in checks] == [
        "connection_config",
        "data_dir",
        "state_db",
        "source:claude_code_cli",
        "source:codex_cli",
        "consent",
        "emit_queue",
        "platform_service",
        "versions",
        "network_probe",
    ]
    assert all(c["status"] in CLOSED_STATUSES for c in checks)


def test_missing_data_dir_is_unhealthy_and_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "never-created"
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(missing)
    document, exit_code = run_doctor(env)
    assert exit_code == 1
    by_id = {c["check"]: c for c in document["checks"]}
    assert by_id["data_dir"]["status"] == "unhealthy"
    assert by_id["data_dir"]["code"] == "not_initialized"
    assert not missing.exists(), "doctor must not create state (FR-DIA-1)"


def test_invalid_config_file_is_pinpointed(tmp_path: Path) -> None:
    env = _healthy_env(tmp_path)
    (tmp_path / "data" / "config.json").write_text("this is not json{", encoding="utf-8")
    document, exit_code = run_doctor(env)
    assert exit_code == 1
    by_id = {c["check"]: c for c in document["checks"]}
    assert by_id["connection_config"]["code"] == "invalid_config_file"


def test_org_token_is_never_echoed(tmp_path: Path) -> None:
    env = _healthy_env(tmp_path)
    env["PRACTICEGRAPH_ORG_TOKEN"] = "hunter2-super-secret-token"
    document, _ = run_doctor(env)
    serialized = json.dumps(document)
    assert "hunter2" not in serialized
    by_id = {c["check"]: c for c in document["checks"]}
    detail = by_id["connection_config"]["detail"]
    assert detail["org_token_present"] == "true"
    assert detail["org_token_source"] == "env"


def test_output_passes_no_leak_scan(tmp_path: Path) -> None:
    """FR-DIA-3: no tokens, no full local paths, no content."""
    document, _ = run_doctor(_healthy_env(tmp_path))
    assert leak_findings(json.dumps(document)) == []


def test_invalid_consent_file_is_unhealthy(tmp_path: Path) -> None:
    env = _healthy_env(tmp_path)
    (tmp_path / "data" / "consent.json").write_text("garbage", encoding="utf-8")
    document, exit_code = run_doctor(env)
    assert exit_code == 1
    by_id = {c["check"]: c for c in document["checks"]}
    assert by_id["consent"]["code"] == "invalid_consent_file"


def test_network_probe_is_opt_in(tmp_path: Path) -> None:
    env = _healthy_env(tmp_path)

    document, _ = run_doctor(env, probe_network=False)
    by_id = {c["check"]: c for c in document["checks"]}
    assert by_id["network_probe"]["code"] == "opt_in_required"

    document, _ = run_doctor(env, probe_network=True)
    by_id = {c["check"]: c for c in document["checks"]}
    assert by_id["network_probe"]["status"] == "not_applicable"
    assert by_id["network_probe"]["code"] == "no_endpoint_configured"


def test_network_probe_reports_unreachable_endpoint(tmp_path: Path) -> None:
    env = _healthy_env(tmp_path)
    env["PRACTICEGRAPH_API_BASE_URL"] = "http://127.0.0.1:9"  # nothing listens here
    document, exit_code = run_doctor(env, probe_network=True)
    by_id = {c["check"]: c for c in document["checks"]}
    assert by_id["network_probe"]["status"] == "unhealthy"
    assert by_id["network_probe"]["code"] in {
        "server_unavailable",
        "timeout",
        "transport_error",
    }
    assert exit_code == 1
