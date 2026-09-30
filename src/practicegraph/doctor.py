"""One-command support triage (FR-DIA).

Prints exactly one closed JSON document. Statuses come from the closed set
{healthy, unhealthy, not_applicable, skipped}; codes and detail values are
closed labels. The output passes the same no-leak constraints as every other
surface (FR-DIA-3): no tokens, no full local paths, no content. Doctor MUST
NOT create or mutate any state (FR-DIA-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from practicegraph import __version__
from practicegraph.analysis.ratecard import activate_rate_card_from, active_rate_card
from practicegraph.catalog import catalog_status
from practicegraph.config import resolve
from practicegraph.consent import read_consent
from practicegraph.sources.registry import ADAPTERS
from practicegraph.store import Store
from practicegraph.transport import probe
from practicegraph.wire import EMIT_SCHEMA_VERSION

DOCTOR_SCHEMA_VERSION = "practicegraph.doctor/1"

Status = Literal["healthy", "unhealthy", "not_applicable", "skipped"]


@dataclass(frozen=True, slots=True)
class Check:
    check: str
    status: Status
    code: str
    detail: dict[str, str] | None = None


def run_doctor(
    env: dict[str, str],
    probe_network: bool = False,
    now: datetime | None = None,
) -> tuple[dict[str, object], int]:
    """Run all checks; returns (document, exit_code). Exit codes per FR-DIA-2:
    0 all healthy, 1 any unhealthy (command errors exit 2 at the CLI layer)."""
    config = resolve(env)
    checks: list[Check] = []

    # Connection config resolution — sources and presence flags only, never
    # values (FR-CFG-2, FR-DIA-3).
    config_status: Status = "healthy"
    config_code = "ok"
    if config.config_file_state == "invalid":
        config_status, config_code = "unhealthy", "invalid_config_file"
    checks.append(
        Check(
            check="connection_config",
            status=config_status,
            code=config_code,
            detail={
                "data_dir_source": config.data_dir_source,
                "api_base_url_source": config.api_base_url_source,
                "org_id_source": config.org_id_source,
                "org_token_source": config.org_token_source,
                "org_token_present": "true" if config.org_token_present else "false",
                "config_file": config.config_file_state,
            },
        )
    )

    # Data dir existence — checked without creating anything (FR-DIA-1).
    data_dir_ok = config.data_dir.is_dir()
    if data_dir_ok:
        checks.append(Check(check="data_dir", status="healthy", code="ok"))
    else:
        checks.append(Check(check="data_dir", status="unhealthy", code="not_initialized"))

    # State DB: opened read-only, never created (FR-DIA-1).
    store = Store.in_data_dir(config.data_dir)
    if not data_dir_ok or not store.exists():
        checks.append(Check(check="state_db", status="unhealthy", code="not_initialized"))
    else:
        try:
            version = store.schema_version()
            checks.append(
                Check(
                    check="state_db",
                    status="healthy",
                    code="ok",
                    detail={"schema_version": str(version)},
                )
            )
        except Exception:
            checks.append(Check(check="state_db", status="unhealthy", code="not_openable"))

    # Per-source detection. Full parser health counters move here once the
    # local store persists them (FR-SRC-6); detection is what M0 can answer.
    for adapter in ADAPTERS:
        detected = bool(adapter.discover(env))
        checks.append(
            Check(
                check=f"source:{adapter.source_id}",
                status="healthy" if detected else "not_applicable",
                code="detected" if detected else "not_detected",
                detail={
                    "capability": adapter.capability.value,
                    "parser_version": str(adapter.parser_version),
                },
            )
        )

    consent = read_consent(config.data_dir)
    if consent.file_state == "invalid":
        checks.append(Check(check="consent", status="unhealthy", code="invalid_consent_file"))
    else:
        # Disabled is healthy (FR-DIA-1): consent OFF is the private default.
        checks.append(
            Check(
                check="consent",
                status="healthy",
                code="emission_enabled" if consent.emission_enabled else "emission_disabled",
            )
        )

    # Emit queue counts by status and error code (FR-DIA-1). A dead-lettered
    # emit — including the distinct version-rejection code (FR-EMT-3) — needs
    # attention, so it drives unhealthy.
    if store.exists():
        try:
            counts = store.queue_counts()
            error_counts = store.queue_error_counts()
            detail = {status: str(n) for status, n in counts.items()}
            for code, n in sorted(error_counts.items()):
                detail[f"error:{code}"] = str(n)
            queue_status: Status = "healthy"
            queue_code = "ok"
            if counts.get("dead_letter", 0) > 0:
                queue_status, queue_code = "unhealthy", "dead_letter_present"
            checks.append(
                Check(check="emit_queue", status=queue_status, code=queue_code, detail=detail)
            )
        except Exception:
            checks.append(Check(check="emit_queue", status="unhealthy", code="not_openable"))
    else:
        checks.append(
            Check(check="emit_queue", status="not_applicable", code="not_initialized")
        )
    checks.append(Check(check="platform_service", status="not_applicable", code="not_installed"))
    rate_card_source = activate_rate_card_from(config.data_dir)
    version_detail = {
        "app": __version__,
        "rate_card": active_rate_card().version,
        "rate_card_source": rate_card_source,
        "emit_schema": str(EMIT_SCHEMA_VERSION),
        "doctor_schema": DOCTOR_SCHEMA_VERSION,
    }
    if store.exists():
        version_detail.update(
            {f"catalog_{k}": v for k, v in catalog_status(config.data_dir, store).items()}
        )
    checks.append(
        Check(check="versions", status="healthy", code="ok", detail=version_detail)
    )

    # Network probe is strictly opt-in (FR-DIA-1); closed codes, no store writes.
    if not probe_network:
        checks.append(Check(check="network_probe", status="skipped", code="opt_in_required"))
    elif config.api_base_url is None:
        checks.append(
            Check(check="network_probe", status="not_applicable", code="no_endpoint_configured")
        )
    else:
        probe_code = probe(config.api_base_url)
        checks.append(
            Check(
                check="network_probe",
                status="healthy" if probe_code == "reachable" else "unhealthy",
                code=probe_code,
            )
        )

    any_unhealthy = any(check.status == "unhealthy" for check in checks)
    generated_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    document: dict[str, object] = {
        "schema": DOCTOR_SCHEMA_VERSION,
        "app_version": __version__,
        "generated_at": generated_at,
        "status": "unhealthy" if any_unhealthy else "healthy",
        "checks": [
            {
                "check": check.check,
                "status": check.status,
                "code": check.code,
                **({"detail": check.detail} if check.detail is not None else {}),
            }
            for check in checks
        ],
    }
    return document, 1 if any_unhealthy else 0
