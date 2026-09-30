"""Passive backend over real HTTP (FR-API-1/2/3/5, FR-EMT-3 server side),
plus the full endpoint-to-dashboard flow (acceptance snapshot #5)."""

from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conftest import FIXTURE_ENV, build_fixture_history, build_fixture_payload
from practicegraph.agent import initialize, run_tick
from practicegraph_server import SERVER_VERSION
from practicegraph_server.app import PracticeGraphServer, make_server
from practicegraph_server.config import ServerConfig, load_config
from practicegraph_server.storage import MemoryStorage, SqliteStorage

_STORE, _HEALTH = build_fixture_history()

ORG = "acme-eng"
TOKEN = "test-ingest-source-one-0000000000000000"
TOKEN_TWO = "test-ingest-source-two-0000000000000000"
DASHBOARD_PASSWORD = "dash-456-secret"
NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


def _server_config(
    k_threshold: int, dashboard_password: str = DASHBOARD_PASSWORD, retention_days: int = 0
) -> ServerConfig:
    return ServerConfig(
        bind="127.0.0.1",
        port=0,
        org_id=ORG,
        org_token=TOKEN,
        ingest_tokens=(("source-one", TOKEN), ("source-two", TOKEN_TWO)),
        db_path=":memory:",
        k_threshold=k_threshold,
        access_log=False,
        serve_dashboard=True,
        dashboard_password=dashboard_password,
        retention_days=retention_days,
    )


@pytest.fixture
def server_k2() -> Iterator[tuple[str, PracticeGraphServer]]:
    yield from _run_server(k_threshold=2)


@pytest.fixture
def server_k1() -> Iterator[tuple[str, PracticeGraphServer]]:
    yield from _run_server(k_threshold=1)


@pytest.fixture
def server_split() -> Iterator[tuple[str, PracticeGraphServer]]:
    yield from _run_server(k_threshold=2, dashboard_password=DASHBOARD_PASSWORD)


def _run_server(
    k_threshold: int, dashboard_password: str = DASHBOARD_PASSWORD
) -> Iterator[tuple[str, PracticeGraphServer]]:
    server = make_server(
        _server_config(k_threshold, dashboard_password=dashboard_password), MemoryStorage()
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url, server
    finally:
        server.shutdown()
        server.server_close()


def _request(
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    bearer: str | None = None,
    basic: str | None = None,
    basic_user: str = "",
) -> tuple[int, dict[str, str], bytes]:
    headers: dict[str, str] = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if basic is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{basic_user}:{basic}".encode()
        ).decode("ascii")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _payload(emit_id: str, day: date = date(2026, 7, 2)) -> dict[str, Any]:
    return build_fixture_payload(_STORE, _HEALTH, emit_id, day=day.isoformat(), org_id=ORG)


EMIT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EMIT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_catalog_versions_is_public(server_k2: tuple[str, PracticeGraphServer]) -> None:
    base_url, _ = server_k2
    status, _, body = _request("GET", f"{base_url}/v1/catalog/versions")
    assert status == 200
    document = json.loads(body)
    assert document["supported_schema_versions"] == [1, 2]
    assert "rate_card_version" in document


def test_catalog_version_is_a_content_hash_that_moves_with_the_content(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    """The endpoint short-circuits its catalog pull on version equality
    (catalog.py:132-134), so a frozen version constant meant a newly published
    skills.json propagated to exactly nobody — every endpoint reported
    "unchanged" forever. The version is now a hash of what is actually
    served: stable while the content is stable, different when it differs,
    and shaped to pass the endpoint's existing _VERSION_RE untouched."""
    import re

    from practicegraph.catalog import _VERSION_RE
    from practicegraph_server.app import _catalog_content_version

    base_url, _ = server_k2
    status, _, body = _request("GET", f"{base_url}/v1/catalog/versions")
    assert status == 200
    version = json.loads(body)["catalog_version"]
    # Not the old constant, and valid under the endpoint's validator.
    assert version != "bundled-2026-07-01"
    assert re.match(_VERSION_RE, version)
    # Stable across requests while served content is unchanged...
    status, _, body = _request("GET", f"{base_url}/v1/catalog/versions")
    assert json.loads(body)["catalog_version"] == version
    # ...deterministic for identical artifacts, different for different ones.
    a = _catalog_content_version({"skills_version": "v1"}, {"briefings": []})
    b = _catalog_content_version({"skills_version": "v1"}, {"briefings": []})
    c = _catalog_content_version({"skills_version": "v2"}, {"briefings": []})
    assert a == b
    assert a != c


def test_healthz_is_public_and_org_free(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    """Load-balancer probe: no auth required, nothing org-specific returned."""
    base_url, _ = server_k2
    status, _, body = _request("GET", f"{base_url}/healthz")
    assert status == 200
    assert json.loads(body) == {"ok": True, "server_version": SERVER_VERSION}
    assert ORG not in body.decode("utf-8")


def test_ingest_requires_auth(server_k2: tuple[str, PracticeGraphServer]) -> None:
    base_url, _ = server_k2
    status, headers, _body = _request("POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A))
    assert status == 401
    assert headers["X-Error-Code"] == "unauthorized"
    status, _, _ = _request(
        "POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer="wrong-token"
    )
    assert status == 401


def test_version_gate_rejects_distinctly_without_echo(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    """FR-EMT-3: distinct closed code, stable header, no echo of the value."""
    base_url, _ = server_k2
    payload = _payload(EMIT_A)
    payload["schema_version"] = 987654
    status, headers, body = _request("POST", f"{base_url}/v1/ingest", body=payload, bearer=TOKEN)
    assert status == 400
    assert headers["X-Error-Code"] == "schema_version_not_supported"
    document = json.loads(body)
    assert document["code"] == "schema_version_not_supported"
    assert "987654" not in body.decode("utf-8")


def test_ingest_rejects_unknown_fields(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    base_url, _ = server_k2
    payload = _payload(EMIT_A)
    payload["surprise_field"] = 1
    status, headers, body = _request("POST", f"{base_url}/v1/ingest", body=payload, bearer=TOKEN)
    assert status == 422
    assert headers["X-Error-Code"] == "invalid_payload"
    assert "surprise_field" not in body.decode("utf-8")  # no echo (FR-API-3)


def test_ingest_rejects_org_mismatch(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    base_url, _ = server_k2
    payload = _payload(EMIT_A)
    payload["org_id"] = "some-other-org"
    status, headers, _ = _request("POST", f"{base_url}/v1/ingest", body=payload, bearer=TOKEN)
    assert status == 403
    assert headers["X-Error-Code"] == "forbidden"


def test_k_anonymity_suppression_then_release(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    """NFR-PRV-3 / FR-API-5: below k the cell is an explicit suppressed marker;
    at k the aggregate appears; duplicate emit_ids are idempotent."""
    base_url, _ = server_k2
    summary_url = f"{base_url}/v1/aggregates/summary?from=2026-07-02&to=2026-07-02"

    status, _, _ = _request("POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer=TOKEN)
    assert status == 202
    status, _, body = _request("GET", summary_url, bearer=DASHBOARD_PASSWORD)
    assert status == 200
    summary = json.loads(body)
    assert summary["days"] == [{"day": "2026-07-02", "status": "suppressed"}]
    assert summary["totals"]["status"] == "suppressed"

    # Second contributor reaches k=2: the aggregate is released.
    status, _, _ = _request(
        "POST", f"{base_url}/v1/ingest", body=_payload(EMIT_B), bearer=TOKEN_TWO
    )
    assert status == 202
    # Idempotent re-post of the same emit_id must not inflate contributors.
    _request("POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer=TOKEN)

    _, _, body = _request("GET", summary_url, bearer=DASHBOARD_PASSWORD)
    summary = json.loads(body)
    day = summary["days"][0]
    assert day["status"] == "ok"
    assert day["contributors"] == 2
    assert day["estimated_cost_micro_usd"] == 42005 * 2
    assert summary["totals"]["status"] == "ok"
    assert summary["totals"]["max_contributors"] == 2


def test_v1_payloads_from_older_agents_are_accepted(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    """FR-EMT-3 operational rule: the server keeps supporting v1 after v2."""
    base_url, _ = server_k2
    payload = _payload(EMIT_A)
    payload["schema_version"] = 1
    del payload["work_type_sessions"]
    del payload["maturity"]
    status, _, _ = _request("POST", f"{base_url}/v1/ingest", body=payload, bearer=TOKEN)
    assert status == 202


def test_aggregates_require_auth(server_k2: tuple[str, PracticeGraphServer]) -> None:
    base_url, _ = server_k2
    status, _, _ = _request(
        "GET", f"{base_url}/v1/aggregates/summary?from=2026-07-02&to=2026-07-02"
    )
    assert status == 401


def test_coverage_endpoint_suppresses_small_subgroups(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    """Even inside a displayable day, a sub-threshold tool subgroup must not
    be enumerable (INV-4)."""
    base_url, server = server_k2
    first = _payload(EMIT_A)
    second = _payload(EMIT_B)
    second["tools_observed"] = ["claude_code"]  # only one contributor uses codex
    for payload in (first, second):
        server.storage.put_emit(
            ORG,
            str(payload["emit_id"]),
            str(payload["day"]),
            payload,
            "2026-07-03T00:00:00+00:00",
            source_id=str(payload["emit_id"]),
        )
    status, _, body = _request(
        "GET",
        f"{base_url}/v1/aggregates/coverage?from=2026-07-02&to=2026-07-02",
        bearer=DASHBOARD_PASSWORD,
    )
    assert status == 200
    day = json.loads(body)["days"][0]
    assert day["status"] == "ok"
    assert day["contributors"] == 2
    assert day["tools"]["claude_code"] == 2
    assert day["tools"]["codex"] == "suppressed"


def test_dashboard_requires_basic_auth_and_renders(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    base_url, _ = server_k2
    status, headers, _ = _request("GET", f"{base_url}/dashboard?from=2026-07-01&to=2026-07-02")
    assert status == 401
    assert headers.get("WWW-Authenticate", "").startswith("Basic")

    _request("POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer=TOKEN)
    status, _, body = _request(
        "GET",
        f"{base_url}/dashboard?from=2026-07-01&to=2026-07-02",
        basic=DASHBOARD_PASSWORD,
    )
    assert status == 200
    page = body.decode("utf-8")
    assert "unavailable (below k-anonymity threshold)" in page  # honest suppression
    assert "no data" in page  # absence is distinct from suppression
    assert "<script" not in page.lower()  # NFR-SEC-2 applies to the dashboard
    assert TOKEN not in page


def test_dashboard_window_clamp_unit() -> None:
    """Preset windows clamp into [1, 90]; garbage stays a closed error."""
    from practicegraph_server.app import clamp_window_days

    assert clamp_window_days("7") == 7
    assert clamp_window_days("90") == 90
    assert clamp_window_days("0") == 1
    assert clamp_window_days("-3") == 1
    assert clamp_window_days("500") == 90
    assert clamp_window_days("abc") is None


def test_live_cadence_clamp_unit() -> None:
    """?live=N → a refresh cadence in [5, 300]; absent/zero/garbage disables it."""
    from practicegraph_server.app import clamp_live_seconds

    assert clamp_live_seconds("") == 0  # absent → static
    assert clamp_live_seconds("0") == 0
    assert clamp_live_seconds("-5") == 0
    assert clamp_live_seconds("abc") == 0
    assert clamp_live_seconds("15") == 15
    assert clamp_live_seconds("1") == 5  # floored (no tight reload loop)
    assert clamp_live_seconds("9999") == 300  # ceiling


def test_dashboard_days_param_clamps_but_json_api_stays_strict(
    server_k2: tuple[str, PracticeGraphServer],
) -> None:
    base_url, _ = server_k2
    # Dashboard: out-of-range preset clamps to the 90-day cap and renders.
    status, _, body = _request("GET", f"{base_url}/dashboard?days=500", basic=DASHBOARD_PASSWORD)
    assert status == 200
    assert "Fleet spend" in body.decode("utf-8")
    # Unparseable stays a closed 400 (no clamp guessing).
    status, _, _ = _request("GET", f"{base_url}/dashboard?days=abc", basic=DASHBOARD_PASSWORD)
    assert status == 400
    # The JSON aggregate route keeps strict validation (FR-API-3 unchanged).
    status, _, _ = _request(
        "GET", f"{base_url}/v1/aggregates/summary?days=500", bearer=DASHBOARD_PASSWORD
    )
    assert status == 400


def test_dashboard_split_credential_accepted_any_username(
    server_split: tuple[str, PracticeGraphServer],
) -> None:
    """With PG_SERVER_DASHBOARD_PASSWORD set, the dashboard opens with that
    password regardless of the Basic username."""
    base_url, _ = server_split
    _request("POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer=TOKEN)
    for user in ("", "it-admin"):
        status, _, _ = _request(
            "GET",
            f"{base_url}/dashboard?from=2026-07-01&to=2026-07-02",
            basic=DASHBOARD_PASSWORD,
            basic_user=user,
        )
        assert status == 200


def test_split_credentials_do_not_cross(
    server_split: tuple[str, PracticeGraphServer],
) -> None:
    """Once the split is configured the ingest bearer token stops opening the
    dashboard, and the dashboard password never becomes an ingest credential."""
    base_url, _ = server_split
    status, headers, _ = _request(
        "GET", f"{base_url}/dashboard?from=2026-07-01&to=2026-07-02", basic=TOKEN
    )
    assert status == 401
    assert headers.get("WWW-Authenticate", "").startswith("Basic")
    status, _, _ = _request(
        "POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer=DASHBOARD_PASSWORD
    )
    assert status == 401
    # The org token keeps working where it belongs: ingest.
    status, _, _ = _request("POST", f"{base_url}/v1/ingest", body=_payload(EMIT_A), bearer=TOKEN)
    assert status == 202


def _seeded_memory_storage(rows: dict[str, str]) -> MemoryStorage:
    storage = MemoryStorage()
    for emit_id, day in rows.items():
        storage.put_emit(
            ORG, emit_id, day, {"day": day}, "2026-07-03T00:00:00+00:00", source_id=emit_id
        )
    return storage


_ALL_DAYS = ("0001-01-01", "9999-12-31")


def test_retention_prunes_old_days_at_startup() -> None:
    """PG_SERVER_RETENTION_DAYS=N: rows older than N days are deleted when the
    server starts; rows exactly N days old and newer are kept."""
    today = datetime.now(UTC).date()
    old = (today - timedelta(days=10)).isoformat()
    edge = (today - timedelta(days=7)).isoformat()
    recent = (today - timedelta(days=1)).isoformat()
    storage = _seeded_memory_storage({"old": old, "edge": edge, "recent": recent})
    server = make_server(_server_config(2, retention_days=7), storage)
    try:
        kept = storage.payloads_by_day(ORG, *_ALL_DAYS)
    finally:
        server.server_close()
    assert old not in kept
    assert set(kept) == {edge, recent}


def test_retention_disabled_keeps_everything() -> None:
    """Unset/0 retention keeps the table append-only forever (pilot default)."""
    storage = _seeded_memory_storage({"ancient": "2000-01-01"})
    server = make_server(_server_config(2, retention_days=0), storage)
    try:
        assert "2000-01-01" in storage.payloads_by_day(ORG, *_ALL_DAYS)
    finally:
        server.server_close()


def test_retention_runs_at_most_once_per_utc_day() -> None:
    """The in-memory marker suppresses re-pruning within a UTC day and fires
    again when the day rolls over (the check ingest piggybacks on)."""
    storage = MemoryStorage()
    server = make_server(_server_config(2, retention_days=7), storage)
    try:
        today = datetime.now(UTC).date()
        old = (today - timedelta(days=30)).isoformat()
        storage.put_emit(ORG, "late-old", old, {"day": old}, "t", source_id="late-old")
        server.prune_expired(today)  # same day as the startup prune: no-op
        assert old in storage.payloads_by_day(ORG, *_ALL_DAYS)
        server.prune_expired(today + timedelta(days=1))  # next UTC day: prunes
        assert old not in storage.payloads_by_day(ORG, *_ALL_DAYS)
    finally:
        server.server_close()


def test_sqlite_storage_prunes_by_day_string(tmp_path: Path) -> None:
    """The SQLite DELETE is a deterministic day-string comparison."""
    storage = SqliteStorage(str(tmp_path / "server.db"))
    storage.put_emit(ORG, "a", "2026-01-01", {"day": "2026-01-01"}, "t", source_id="a")
    storage.put_emit(ORG, "b", "2026-03-01", {"day": "2026-03-01"}, "t", source_id="b")
    assert storage.prune_days_before("2026-03-01") == 1  # strictly-before cutoff
    assert list(storage.payloads_by_day(ORG, *_ALL_DAYS)) == ["2026-03-01"]


def test_load_config_reads_split_credential_and_retention() -> None:
    base = {"PG_SERVER_ORG_ID": ORG, "PG_SERVER_ORG_TOKEN": TOKEN}
    config = load_config(
        base
        | {"PG_SERVER_DASHBOARD_PASSWORD": DASHBOARD_PASSWORD, "PG_SERVER_RETENTION_DAYS": "400"}
    )
    assert config.dashboard_password == DASHBOARD_PASSWORD
    assert config.retention_days == 400
    legacy = load_config(base)
    assert legacy.dashboard_password == ""  # legacy fallback: org token
    assert legacy.retention_days == 0  # keep forever
    assert load_config(base | {"PG_SERVER_RETENTION_DAYS": "-3"}).retention_days == 0


def test_e2e_agent_to_dashboard(
    server_k1: tuple[str, PracticeGraphServer],
    tmp_path: Path,
) -> None:
    """The acceptance story: consent ON -> agent tick queues and flushes the
    completed day -> k-anon summary and dashboard show the aggregate."""
    base_url, server = server_k1
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path / "data")
    env["PRACTICEGRAPH_API_BASE_URL"] = base_url
    env["PRACTICEGRAPH_ORG_ID"] = ORG
    env["PRACTICEGRAPH_ORG_TOKEN"] = TOKEN
    initialize(env)
    (tmp_path / "data" / "consent.json").write_text(
        json.dumps({"emission_enabled": True, "decided_at": "2026-07-01T08:00:00+00:00"}),
        encoding="utf-8",
    )

    outcomes = run_tick(env, now=NOW)
    assert outcomes["emit_build"] == "ok"
    assert outcomes["emit_flush"] == "ok"

    stored = server.storage.payloads_by_day(ORG, "2026-07-02", "2026-07-02")
    assert len(stored.get("2026-07-02", [])) == 1

    _, _, body = _request(
        "GET",
        f"{base_url}/v1/aggregates/summary?from=2026-07-02&to=2026-07-02",
        bearer=DASHBOARD_PASSWORD,
    )
    summary = json.loads(body)
    day = summary["days"][0]
    assert day["status"] == "ok"
    assert day["estimated_cost_micro_usd"] == 42005
    assert day["tools"] == {"claude_code": 1, "codex": 1}

    status, _, page = _request(
        "GET", f"{base_url}/dashboard?from=2026-07-02&to=2026-07-02", basic=DASHBOARD_PASSWORD
    )
    assert status == 200
    assert "$0.04" in page.decode("utf-8")
