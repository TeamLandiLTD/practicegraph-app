"""Shared test helpers: the fixture-pack pipeline (FR-SRC-8).

The fixture logs are sanitized synthetic files shaped like real tool logs.
They deliberately contain leak markers (fake paths, emails, keys, and
SECRET-MARKER-* strings) that must never appear in any product output.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from practicegraph import catalog_crypto
from practicegraph.analysis.aggregate import DailySnapshot, build_daily_snapshot
from practicegraph.analysis.ratecard import reset_active_rate_card
from practicegraph.analysis.schedule import TZDATA_VERSION, ScheduleProfile
from practicegraph.config import Prefs, read_prefs
from practicegraph.events import TurnEvent
from practicegraph.history import ingest, snapshot_for_day
from practicegraph.report.shell import PrivacyStatus, ShellExtras, gather_shell_extras
from practicegraph.sources import SourceHealthRow
from practicegraph.sources.registry import collect_events
from practicegraph.store import Store

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"
GOLDENS = TESTS_DIR / "goldens"

CLAUDE_FIXTURE_HOME = FIXTURES / "claude_code"
CODEX_FIXTURE_HOME = FIXTURES / "codex"

FIXTURE_ENV: dict[str, str] = {
    "PRACTICEGRAPH_CLAUDE_HOME": str(CLAUDE_FIXTURE_HOME),
    "PRACTICEGRAPH_CODEX_HOME": str(CODEX_FIXTURE_HOME),
    # Hermetic: initialize() adopts a pre-2026-07-26 machine store from
    # %ProgramData%\PracticeGraph into any EMPTY data dir. On a dev machine
    # that directory holds a 44MB real-history store, and every e2e test was
    # silently adopting it — then accidentally re-pruning it on first scan,
    # which masked the leak until a new ingest gate (2026-07-29) changed the
    # prune's inputs and real history surfaced in fixture assertions. Tests
    # point ProgramData at a path that never exists.
    "ProgramData": str(FIXTURES / "no-such-programdata"),
}

FIXTURE_DAY = date(2026, 7, 2)
FIXTURE_GENERATED_AT = datetime(2026, 7, 2, 18, 0, tzinfo=UTC)

# Strings seeded into fixtures that must never leak into any surface.
FIXTURE_LEAK_MARKERS = (
    "SECRET-MARKER",
    "demo.user",
    "example.com",
    "sk-demo",
)


FIXTURE_INGEST_NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _local_network_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scheduler tests independent of live catalogs and network timeouts.

    Real HTTP integration tests use loopback. Outbound transport tests supply
    their own synthetic sockets or responses; unmocked external connections
    fail through the normal unavailable-network path.
    """
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check(address: object) -> None:
        if isinstance(address, tuple):
            host = str(address[0])
            if host == "localhost":
                return
            try:
                if ipaddress.ip_address(host).is_loopback:
                    return
            except ValueError:
                pass
            raise OSError("external connections must be mocked in tests")

    def local_connect(sock: socket.socket, address: object) -> None:
        check(address)
        connect(sock, address)

    def local_connect_ex(sock: socket.socket, address: object) -> int:
        check(address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", local_connect_ex)


@pytest.fixture(autouse=True)
def _no_native_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Notifications must never reach a real installed app. Off-bundle, the
    shell lookup falls back to /Applications/PracticeGraph.app, so on a Mac
    with PracticeGraph installed every agent tick in the suite launched that
    app (and its engine) to post a real notification. Pin the seam to a path
    that does not exist; a test that needs a shell sets its own."""
    monkeypatch.setenv("PRACTICEGRAPH_SHELL_EXE", str(tmp_path / "no-shell"))


@pytest.fixture(autouse=True)
def _bundled_rate_card() -> object:
    """Rate-card activation is process state; every test starts and ends on
    the bundled card so activations can never leak across tests."""
    reset_active_rate_card()
    yield None
    reset_active_rate_card()


def build_fixture_events() -> tuple[list[TurnEvent], list[SourceHealthRow]]:
    return collect_events(dict(FIXTURE_ENV))


def build_fixture_snapshot(day: date = FIXTURE_DAY) -> DailySnapshot:
    events, source_health = build_fixture_events()
    return build_daily_snapshot(events, source_health, day)


def build_fixture_history(
    base_dir: Path | None = None,
) -> tuple[Store, list[SourceHealthRow]]:
    """Ingest the fixture pack into a fresh store (deterministic content)."""
    directory = base_dir or Path(tempfile.mkdtemp(prefix="pg-history-"))
    store = Store(directory / "state.db")
    store.migrate()
    source_health = ingest(dict(FIXTURE_ENV), store, FIXTURE_INGEST_NOW)
    return store, source_health


def build_store_at_schema(path: Path, version: int) -> Store:
    """A store frozen at an OLDER schema version, built from the real historical
    migration scripts — what an install that has not ticked since that release
    actually has on disk. The upgrade-regression fixture (field report
    2026-08-21): every surface that reads a store must migrate it first, because
    the ingest tick that used to be the only migration point can arrive minutes
    after the window opens, or never."""
    import sqlite3

    from practicegraph import store as store_module

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for number, script in store_module._MIGRATIONS:
            if number > version:
                break
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {number}")
        conn.commit()
    finally:
        conn.close()
    return Store(path)


def build_fixture_payload(
    store: Store,
    source_health: list[SourceHealthRow],
    emit_id: str,
    day: str = "2026-07-02",
    org_id: str = "acme-eng",
) -> dict[str, object]:
    from practicegraph.emit import build_payload_for_day

    return build_payload_for_day(
        store=store,
        day=day,
        org_id=org_id,
        engagement={"report_generated": 1},
        emit_id=emit_id,
        platform="windows",
        source_health=source_health,
    )


def default_prefs() -> Prefs:
    return read_prefs(Path(tempfile.mkdtemp(prefix="pg-prefs-")))


def fixture_schedule() -> ScheduleProfile:
    return ScheduleProfile(
        version=1,
        confirmed=True,
        timezone_name="UTC",
        tzdata_version=TZDATA_VERSION,
        working_days=(0, 1, 2, 3, 4),
        work_start="09:00",
        work_end="18:00",
        quiet_start="22:00",
        quiet_end="07:00",
        weekend_mode="exceptional",
    )


def build_fixture_extras(day: date = FIXTURE_DAY) -> ShellExtras:
    """History-backed shell extras for golden rendering (deterministic)."""
    from datetime import timedelta

    from practicegraph.analysis.perception import record_checkin

    store, source_health = build_fixture_history()
    for offset, rating in ((0, 4), (1, 3), (2, 5)):
        record_checkin(store, (day - timedelta(days=offset)).isoformat(), rating)
    snapshot = snapshot_for_day(store, day, source_health)
    return gather_shell_extras(
        store, snapshot, default_prefs(), day, fixture_schedule(), day,
        env=dict(FIXTURE_ENV),
    )


def build_shell_privacy_status() -> PrivacyStatus:
    """Deterministic Privacy Center inputs for golden tests: fresh install,
    consent off, empty queue, synthetic example preview."""
    from practicegraph.emit import synthetic_example_payload
    from practicegraph.store import QUEUE_STATUSES

    return PrivacyStatus(
        consent_enabled=False,
        consent_decided_at=None,
        endpoint_configured=False,
        org_configured=False,
        queue_counts=dict.fromkeys(QUEUE_STATUSES, 0),
        queue_error_counts={},
        next_payload_pretty=None,
        example_payload_pretty=json.dumps(
            synthetic_example_payload(), indent=2, sort_keys=True
        ),
    )


def build_dashboard_summary() -> dict[str, object]:
    """Deterministic dashboard view-model for golden tests: one no-data day,
    one suppressed day (1 contributor < k=2), one released day (2 contributors)."""
    from practicegraph_server.view import build_summary

    store, health = build_fixture_history()
    payloads_by_day = {
        "2026-07-01": [
            build_fixture_payload(
                store, health, "cccccccc-cccc-4ccc-8ccc-cccccccccccc", day="2026-07-01"
            )
        ],
        "2026-07-02": [
            build_fixture_payload(store, health, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            build_fixture_payload(store, health, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ],
    }
    return build_summary(
        payloads_by_day, k_threshold=2, from_day="2026-06-30", to_day="2026-07-02"
    )


# --- Sealed editions for public-pull tests -------------------------------
# Public catalog pulls accept sealed editions only (catalog._get_catalog_json,
# 2026-09-16). Tests that model a served edition wrap it with ``sealed`` so the
# document crosses the same boundary an installed client enforces. The
# synthetic publisher key is merged into READER_KEYS for every test; tests that
# install their own publisher (testcatalog_crypto) still override it.
TEST_PUBLISHER_KEY_ID = "test-publisher"
_TEST_SIGNING_SEED = Ed25519PrivateKey.generate().private_bytes_raw()
_TEST_READER_KEY = os.urandom(32)
_TEST_PUBLIC_KEY = (
    Ed25519PrivateKey.from_private_bytes(_TEST_SIGNING_SEED).public_key().public_bytes_raw()
)


@pytest.fixture(autouse=True)
def _test_publisher_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        catalog_crypto,
        "READER_KEYS",
        {
            **catalog_crypto.READER_KEYS,
            TEST_PUBLISHER_KEY_ID: (_TEST_READER_KEY, _TEST_PUBLIC_KEY),
        },
    )


def sealed(channel: str, document: dict[str, object]) -> dict[str, object]:
    """A served edition as the client receives it: encrypted and publisher-signed."""
    return catalog_crypto.seal(document, channel, TEST_PUBLISHER_KEY_ID, _TEST_SIGNING_SEED)
