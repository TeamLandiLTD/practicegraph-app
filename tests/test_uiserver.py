"""Local dashboard server: localhost-only bind, token gate, the closed API,
and writes flowing through the same paths as the CLI (C-4)."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import practicegraph.uiserver as uiserver_module
from conftest import FIXTURE_ENV, build_fixture_history, build_store_at_schema
from practicegraph.report.viewmodel import VIEW_SCHEMA
from practicegraph.uiserver import UiServer, create_server


@pytest.fixture()
def running_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        uiserver_module,
        "pull_public_news",
        lambda _store, _config, _now: "skipped_recently",
        raising=False,
    )
    for name in (
        "pull_public_build_ideas",
        "pull_public_community",
        "pull_public_model_catalog",
        "pull_public_models",
        "pull_public_playbooks",
        "pull_public_ratecard",
    ):
        monkeypatch.setattr(uiserver_module, name, lambda *_args: "skipped_recently")
    monkeypatch.setattr(uiserver_module, "pull_fx_rates", lambda *_args: "skipped_unconfigured")
    store, _health = build_fixture_history(tmp_path)
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path)
    created = create_server(env)
    assert created is not None
    server, url = created
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, url, store
    finally:
        server.shutdown()
        server.server_close()


def _get(url: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _post(url: str, body: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _raw_status(port: int, request_line: str, headers: dict[str, str]) -> int:
    """Send a hand-built request so the Host / Content-Length headers can be set
    to exactly what a test needs, and return the response status code."""
    lines = [request_line]
    lines += [f"{name}: {value}" for name, value in headers.items()]
    lines += ["Connection: close", "", ""]
    raw = "\r\n".join(lines).encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
        conn.sendall(raw)
        data = b""
        while b"\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    return int(data.split(b" ")[1])


def test_binds_localhost_and_requires_the_token(running_server) -> None:
    server, url, _store = running_server
    assert isinstance(server, UiServer)
    assert server.server_address[0] == "127.0.0.1"
    base = url.split("/?token=")[0]
    token = url.split("token=")[1]
    status, body = _get(f"{base}/api/ping")
    assert status == 403 and body == {"error": "forbidden"}
    status, body = _get(f"{base}/api/ping?token={token}")
    assert status == 200 and body["ok"] is True


def test_news_reading_and_preferences_require_auth_and_closed_actions(running_server) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/news?token={token}"
    assert _get(f"{base}/api/news")[0] == 403
    assert _post(f"{base}/api/news", {"action": "snooze", "minutes": 60})[0] == 403
    status, result = _get(endpoint)
    assert status == 200 and result["settings"]["mode"] == "important"
    assert _post(endpoint, {"action": "settings", "mode": "off", "urgent_popup": False})[0] == 200
    assert _get(endpoint)[1]["settings"]["mode"] == "off"
    assert _post(endpoint, {"action": "snooze", "minutes": -1})[0] == 400
    assert _post(endpoint, {"action": "open", "url": "https://example.org"})[0] == 400


def test_usage_is_authenticated_and_accepts_no_paths_or_open_queries(running_server) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    assert _get(f"{base}/api/usage")[0] == 403
    endpoint = f"{base}/api/usage?token={token}"
    for query in ("&period=invalid", "&period=7d&period=30d", "&page=-1",
                  "&page=100001", "&page=bad", "&path=C:/private", "&period="):
        assert _get(endpoint + query)[0] == 400
    status, usage = _get(endpoint + "&period=all")
    assert status == 200 and usage["schema"] == "practicegraph.usage/1"
    session = usage["sessions"][0]["id"]
    status, detail = _get(endpoint + f"&period=all&session={session}")
    assert status == 200
    assert "session_key" not in detail["session"]
    assert detail["context"]["basis"] == "visible_transcript"
    assert _get(endpoint + "&session=C:/private")[0] == 404


def test_practice_time_is_authenticated_closed_and_restorable(running_server) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/practice-time?token={token}"
    assert _get(f"{base}/api/practice-time")[0] == 403
    assert _get(f"{base}/api/practice-time/export")[0] == 403
    assert _post(f"{base}/api/practice-time", {"action": "enable", "enabled": True})[0] == 403
    assert _post(endpoint, {"action": "enable", "enabled": True, "raw": "private"})[0] == 400
    assert _post(endpoint, {"action": "enable", "enabled": True})[0] == 200
    status, result = _post(endpoint, {"action": "start", "skill": "ai-development"})
    assert status == 200 and result["active"]["running"] is True
    entry_id = result["active"]["id"]
    assert _post(endpoint, {"action": "start", "skill": "verification"})[0] == 400
    assert _post(f"{base}/api/block?token={token}", {"event": "break-started"})[0] == 200
    assert _get(endpoint)[1]["active"]["running"] is False
    assert _post(endpoint, {"action": "finish", "id": entry_id})[0] == 200
    backup = _get(f"{base}/api/practice-time/export?token={token}")[1]
    assert backup["schema"] == "practicegraph.practice-time/1"
    assert _post(endpoint, {"action": "clear", "confirm": True})[0] == 200
    restore = f"{base}/api/practice-time/restore?token={token}"
    assert _post(restore, {"action": "restore", "backup": backup})[0] == 200
    assert len(_get(endpoint)[1]["pending"]) == 1


def test_setup_improvement_http_journey_and_restore(running_server, tmp_path):
    from test_setup_improvements import publish

    server, url, _store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/setup-improvements?token={token}"
    assert _get(f"{base}/api/setup-improvements")[0] == 403
    assert _get(f"{base}/api/setup-improvements/export")[0] == 403
    assert _post(f"{base}/api/setup-improvements", {"action": "clear", "confirm": True})[0] == 403
    publish(server.config.data_dir)
    project = tmp_path / "selected"
    project.mkdir()
    (project / "package.json").write_text('{"scripts":{"test":"echo synthetic"}}')
    request = {"action": "inspect", "id": "a" * 32, "path": str(project),
               "tool": "codex", "label": "HTTP setup check"}
    assert _post(endpoint, {**request, "extra": True})[0] == 400
    status, result = _post(endpoint, request)
    assert status == 200 and result["active"]["state"] == "inspected"
    assert str(project) not in json.dumps(result)
    assert _post(endpoint, {"action": "prepare", "id": request["id"]})[0] == 200
    status, result = _post(endpoint, {"action": "handoff", "id": request["id"]})
    assert status == 200 and "Task for the coding harness" in result["brief"]
    backup = _get(f"{base}/api/setup-improvements/export?token={token}")[1]
    assert _post(endpoint, {"action": "clear", "confirm": True})[0] == 200
    restore = f"{base}/api/setup-improvements/restore?token={token}"
    assert _post(restore, {"action": "restore", "backup": backup})[0] == 200
    assert _get(endpoint)[1]["active"]["label"] == "HTTP setup check"


def test_rejects_a_foreign_host_header(running_server) -> None:
    # DNS-rebinding defense: even with the correct token, a request whose Host is
    # not a loopback name is refused, so the token is not the only line of
    # defense against a malicious web page that resolved its own name to
    # 127.0.0.1.
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    port = int(base.rsplit(":", 1)[1])
    target = f"/api/ping?token={token}"
    assert _raw_status(port, f"GET {target} HTTP/1.1", {"Host": "evil.example.com"}) == 403
    assert _raw_status(port, f"GET {target} HTTP/1.1", {"Host": f"127.0.0.1:{port}"}) == 200
    assert _raw_status(port, f"GET {target} HTTP/1.1", {"Host": f"localhost:{port}"}) == 200


def test_rejects_a_non_numeric_content_length(running_server) -> None:
    # A malformed Content-Length must not raise into the worker; it returns 400.
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    port = int(base.rsplit(":", 1)[1])
    status = _raw_status(
        port,
        f"POST /api/checkin?token={token} HTTP/1.1",
        {"Host": f"127.0.0.1:{port}", "Content-Length": "not-a-number"},
    )
    assert status == 400


def _local_today(data_dir: Path) -> str:
    """The day the SERVER will use: schedule-local, not UTC.

    These assertions used to compare against `datetime.now(UTC).date()`, which
    is the same string only while the local zone happens to share UTC's date.
    On a UTC+3 machine that is false between 00:00 and 03:00 local, so the
    suite went red at midnight on 2026-07-26 with no code change behind it.
    """
    from practicegraph.analysis.schedule import local_day, read_schedule_profile

    return local_day(datetime.now(UTC), read_schedule_profile(data_dir)).isoformat()


def test_view_endpoint_serves_the_shared_view_model(running_server) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    status, model = _get(f"{base}/api/view?token={token}")
    assert status == 200
    assert model["schema"] == VIEW_SCHEMA
    assert model["day"] == _local_today(_server.config.data_dir)
    assert "playbook" in model and "advisor" in model
    encoded = json.dumps(model)
    assert "practicegraph suggest dismiss" not in encoded  # sentences only


def _wait_for_refresh(server, timeout: float = 20.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not server.refresh_state()["running"] and server._refresh_finished is not None:
            return
        time.sleep(0.02)
    raise AssertionError("background refresh did not finish")


def test_refresh_worker_reads_local_history_before_any_public_pull(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field report 2026-09-17 (a user's first run: "does not load the
    data"). The reader's own logs need no network and are the point of the
    page, so they are ingested first and each finished step repaints; public
    pulls, which a managed network can stall, come after. The feature scan is
    the worker's job too - the view only ever reads its last result."""
    server, _url, _store = running_server
    while server.refresh_state()["running"]:  # nothing may be mid-flight
        threading.Event().wait(0.02)
    server._refresh_finished = None  # let this test's view start a fresh worker
    events: list[str] = []
    original_ingest = uiserver_module.ingest

    def ingest(*args, **kwargs):
        events.append("ingest")
        return original_ingest(*args, **kwargs)

    original_features = uiserver_module.read_features

    def features(env):
        events.append("features")
        return original_features(env)

    def pull_news(store, config, now):
        events.append("news")
        return "pulled"

    def pull_model_catalog(store, config, now):
        events.append("model_catalog")
        return "pulled"

    def pull_models(store, config, now):
        events.append("models")
        return "pulled"

    def pull_ratecard(store, config, now):
        events.append("ratecard")
        return "pulled"

    monkeypatch.setattr(uiserver_module, "ingest", ingest)
    monkeypatch.setattr(uiserver_module, "read_features", features)
    monkeypatch.setattr(uiserver_module, "pull_public_news", pull_news)
    monkeypatch.setattr(uiserver_module, "pull_public_model_catalog", pull_model_catalog)
    monkeypatch.setattr(uiserver_module, "pull_public_models", pull_models)
    monkeypatch.setattr(uiserver_module, "pull_public_ratecard", pull_ratecard)
    server.invalidate_view()
    server.view_json()
    _wait_for_refresh(server)
    assert events == ["ingest", "features", "news", "model_catalog", "models", "ratecard"]
    assert [item.tool for item in server._features] == ["claude_code", "codex"]


def test_view_reports_a_running_refresh_and_never_loops_on_its_own_repaint(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page polls quickly only while ``refresh.running`` is true. A
    finished refresh repaints the view, and the repainted view asks for a
    refresh again - without a floor that is an endless loop of full ingests."""
    server, _url, _store = running_server
    while server.refresh_state()["running"]:  # nothing may be mid-flight
        threading.Event().wait(0.02)
    server._refresh_finished = None
    release = threading.Event()
    started: list[int] = []

    def slow_ingest(*_args: object, **_kwargs: object) -> list[object]:
        started.append(1)
        release.wait(20)
        return []

    monkeypatch.setattr(uiserver_module, "ingest", slow_ingest)
    server.invalidate_view()
    running = json.loads(server.view_json())["refresh"]
    assert running["running"] is True and running["seconds"] >= 0
    release.set()
    _wait_for_refresh(server)
    finished = json.loads(server.view_json())["refresh"]
    assert finished == {"running": False, "seconds": 0, "fetching_rates": False}
    server.invalidate_view()
    server.view_json()  # inside the floor: answers from the store, starts nothing
    assert len(started) == 1


def _counting_view_model(monkeypatch: pytest.MonkeyPatch, gate=None) -> list[int]:
    """Count view builds; `gate(n)` runs inside build n (to hold it open)."""
    builds: list[int] = []
    real = uiserver_module.view_model

    def counted(*args: object, **kwargs: object) -> object:
        builds.append(1)
        if gate is not None:
            gate(len(builds))
        return real(*args, **kwargs)

    monkeypatch.setattr(uiserver_module, "view_model", counted)
    return builds


def test_concurrent_view_requests_share_one_build(
    running_server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build takes seconds on a long history. Requests that arrive during
    one used to start their own, the page polls every few seconds while a
    refresh runs, and the builds slowed each other until none finished inside
    the poll interval: a pile-up at full CPU (field report 2026-09-29)."""
    server, _url, _store = running_server
    monkeypatch.setattr(server, "_refresh_async", lambda: None)
    builds = _counting_view_model(monkeypatch, gate=lambda _n: time.sleep(0.3))
    server.invalidate_view()
    answers: list[dict[str, object]] = []
    threads = [threading.Thread(target=lambda: answers.append(json.loads(server.view_json())))
               for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(answers) == 6 and len(builds) == 1


def test_a_change_during_a_build_is_followed_by_exactly_one_more(
    running_server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, _url, _store = running_server
    monkeypatch.setattr(server, "_refresh_async", lambda: None)
    inside, release = threading.Event(), threading.Event()

    def hold_first(n: int) -> None:
        if n == 1:
            inside.set()
            release.wait(5)

    builds = _counting_view_model(monkeypatch, gate=hold_first)
    server.invalidate_view()
    first = threading.Thread(target=server.view_json)
    first.start()
    assert inside.wait(5)
    server.invalidate_view()  # new data landed while the build was reading
    waiters = [threading.Thread(target=server.view_json) for _ in range(3)]
    for thread in waiters:
        thread.start()
    release.set()
    for thread in [first, *waiters]:
        thread.join(10)
    # The build that started before the change is not served as current, and
    # the requests that waited share a single rebuild.
    assert len(builds) == 2


def test_switching_currency_converts_the_cached_view_without_rebuilding(
    running_server, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from practicegraph.analysis import fx

    server, url, _store = running_server
    base, token = url.split("/?token=")
    monkeypatch.setattr(server, "_refresh_async", lambda: None)
    rates = fx.FxRates("2026-09-29", {"EUR": Decimal(1), "USD": Decimal("1.25")})
    (tmp_path / "catalog").mkdir(exist_ok=True)
    (tmp_path / "catalog" / fx.FX_FILE_NAME).write_text(json.dumps(fx.rates_to_json(rates)))
    builds = _counting_view_model(monkeypatch)
    server.invalidate_view()
    assert _get(f"{base}/api/view?token={token}")[1]["currency"]["code"] == "USD"
    for code in ("EUR", "USD", "EUR"):
        assert _post(f"{base}/api/currency?token={token}", {"code": code})[0] == 200
        assert _get(f"{base}/api/view?token={token}")[1]["currency"]["code"] == code
    assert len(builds) == 1


def test_the_refresh_worker_reads_history_under_the_served_rate_card(
    running_server, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Costs are priced as logs are read, and a change of card re-reads the
    whole history. The worker started before the server had loaded the
    downloaded card, so a fresh start read history under the built-in card
    and the next refresh re-read all of it again under the downloaded one:
    two full re-reads on every launch (field report 2026-09-29)."""
    from practicegraph.analysis.ratecard import (
        CATALOG_DIR_NAME,
        RATE_CARD_FILE_NAME,
        active_rate_card,
        rate_card_artifact,
        reset_active_rate_card,
    )

    server, _url, _store = running_server
    while server.refresh_state()["running"]:  # nothing may be mid-flight
        time.sleep(0.02)
    doc = rate_card_artifact()
    doc["rate_card_version"] = "rates-2099-01-01"
    (tmp_path / CATALOG_DIR_NAME).mkdir(exist_ok=True)
    (tmp_path / CATALOG_DIR_NAME / RATE_CARD_FILE_NAME).write_text(json.dumps(doc))
    priced_with: list[str] = []

    def record(*_args: object, **_kwargs: object) -> list[object]:
        priced_with.append(active_rate_card().version)
        return []

    monkeypatch.setattr(uiserver_module, "ingest", record)
    try:
        reset_active_rate_card()  # as in a process that has not built a view yet
        server._refresh_finished = None
        server._refresh_async()
        _wait_for_refresh(server)
        assert priced_with == ["rates-2099-01-01"]
    finally:
        reset_active_rate_card()


def test_schedule_endpoint_confirms_and_updates_the_view(running_server) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    update = {
        "timezone_name": "Europe/Sofia",
        "working_days": [0, 1, 2, 3, 4],
        "work_start": "09:00",
        "work_end": "18:00",
        "quiet_start": "22:00",
        "quiet_end": "07:00",
        "weekend_mode": "exceptional",
    }
    status, body = _post(f"{base}/api/schedule?token={token}", update)
    assert status == 200
    assert body["schedule"]["confirmed"] is True  # type: ignore[index]
    assert body["schedule"]["timezone_name"] == "Europe/Sofia"  # type: ignore[index]

    status, model = _get(f"{base}/api/view?token={token}")
    assert status == 200
    assert model["schema"] == "practicegraph.view/2"
    assert model["schedule"]["version"] == 1  # type: ignore[index]
    assert model["local_day"]


@pytest.mark.parametrize(
    "body",
    [
        {"timezone_name": "Mars/Base"},
        {
            "timezone_name": "Europe/Sofia",
            "working_days": [0, 1, 2, 3, 4],
            "work_start": "09:00",
            "work_end": "18:00",
            "quiet_start": "22:00",
            "quiet_end": "07:00",
            "weekend_mode": "exceptional",
            "extra": True,
        },
    ],
)
def test_schedule_endpoint_rejects_open_or_invalid_bodies(
    running_server, body: dict[str, object]
) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    status, response = _post(f"{base}/api/schedule?token={token}", body)
    assert status == 400
    assert response == {"error": "invalid_schedule"}


def test_actions_write_through_the_cli_paths(running_server) -> None:
    _server, url, store = running_server
    base, token = url.split("/?token=")
    today = _local_today(_server.config.data_dir)

    status, body = _post(f"{base}/api/checkin?token={token}", {"rating": 4})
    assert status == 200 and body["ok"] is True
    assert store.meta_get(f"checkin:{today}") == "4"
    status, _body = _post(f"{base}/api/checkin?token={token}", {"rating": 9})
    assert status == 400

    status, body = _post(
        f"{base}/api/dismiss?token={token}",
        {"tip_id": "protect-a-deep-work-block"},
    )
    assert status == 200
    assert "protect-a-deep-work-block" in store.dismissed_tips()
    status, body = _post(
        f"{base}/api/dismiss?token={token}",
        {"suggestion_id": "keep-sessions-warm"},
    )
    assert status == 200
    status, _body = _post(f"{base}/api/dismiss?token={token}", {"tip_id": "nope"})
    assert status == 400


def test_block_endpoint_records_focus_timer_events(running_server) -> None:
    """The in-page focus timer records lifecycle events through /api/block —
    the same local counters the protocol path used, no external process."""
    _server, url, store = running_server
    base, token = url.split("/?token=")
    from practicegraph.analysis.focus import block_counters

    today = _local_today(_server.config.data_dir)
    for event in ("block-started", "block-completed", "break-started"):
        status, body = _post(f"{base}/api/block?token={token}", {"event": event})
        assert status == 200 and body["ok"] is True
    counters = block_counters(store, today)
    assert counters.get("block-started") == 1
    assert counters.get("block-completed") == 1
    assert counters.get("break-started") == 1
    # An unknown event is rejected (closed vocabulary).
    status, _body = _post(f"{base}/api/block?token={token}", {"event": "bogus"})
    assert status == 400


def test_day_close_endpoint_marks_the_day_and_is_idempotent(running_server) -> None:
    """Close the day (A2): a valid day writes the local flag through the meta
    table; closing twice is a no-op 200; a malformed day is refused; the token
    still gates the action."""
    _server, url, store = running_server
    base, token = url.split("/?token=")
    from practicegraph.analysis.dayclose import is_day_closed

    today = _local_today(_server.config.data_dir)
    assert is_day_closed(store, today) is False
    status, body = _post(f"{base}/api/day-close?token={token}", {"day": today})
    assert status == 200 and body["ok"] is True
    assert is_day_closed(store, today) is True
    # Idempotent: closing again still 200s and leaves it closed.
    status, body = _post(f"{base}/api/day-close?token={token}", {"day": today})
    assert status == 200 and body["ok"] is True
    assert is_day_closed(store, today) is True
    # A malformed day is refused with the closed error.
    status, err = _post(f"{base}/api/day-close?token={token}", {"day": "2026-13-40"})
    assert status == 400 and err == {"error": "invalid_day"}
    # A missing day field is refused too.
    status, _err = _post(f"{base}/api/day-close?token={token}", {})
    assert status == 400
    # The token gates the action (no token -> forbidden, nothing written).
    status, _err = _post(f"{base}/api/day-close", {"day": today})
    assert status == 403


def test_economy_intro_dismissal_is_token_gated_and_idempotent(
    running_server,
) -> None:
    """The first-open retrospective dismissal (W1.1/AM-3): token-gated,
    permanent, idempotent — a meta flag, never on the wire."""
    from practicegraph.analysis.economy import ECONOMY_INTRO_META_KEY

    _server, url, store = running_server
    base, token = url.split("/?token=")
    assert store.meta_get(ECONOMY_INTRO_META_KEY) is None
    status, body = _post(f"{base}/api/economy-intro?token={token}", {})
    assert status == 200 and body["ok"] is True
    assert store.meta_get(ECONOMY_INTRO_META_KEY) == "1"
    # Idempotent — dismissing again still 200s.
    status, body = _post(f"{base}/api/economy-intro?token={token}", {})
    assert status == 200 and body["ok"] is True
    # The token gates the action.
    status, _err = _post(f"{base}/api/economy-intro", {})
    assert status == 403


def test_vocabulary_endpoint_survives_but_the_view_never_carries_the_card(
    running_server,
) -> None:
    """The glossary left the interface on 2026-08-21 (page-diet rule: a
    computed key with no reader is carrying cost), so the view never carries
    a vocabulary key at all now. The dismissal endpoint survives as a
    token-gated idempotent no-op so an older client that still posts to it
    gets an honest 200 rather than an error."""
    from practicegraph.analysis.vocabulary import VOCABULARY_META_KEY

    _server, url, store = running_server
    base, token = url.split("/?token=")
    assert store.meta_get(VOCABULARY_META_KEY) is None
    _status, before = _get(f"{base}/api/view?token={token}")
    assert "vocabulary" not in before

    status, body = _post(f"{base}/api/vocabulary?token={token}", {})
    assert status == 200 and body["ok"] is True
    assert store.meta_get(VOCABULARY_META_KEY) == "1"
    _status, after = _get(f"{base}/api/view?token={token}")
    assert "vocabulary" not in after

    status, body = _post(f"{base}/api/vocabulary?token={token}", {})
    assert status == 200 and body["ok"] is True  # idempotent
    status, _err = _post(f"{base}/api/vocabulary", {})
    assert status == 403  # the token gates the action


def test_billing_mode_accepts_only_the_closed_enum(running_server, tmp_path: Path) -> None:
    """The confirm-once billing lens (W1.1/AM-1): a closed enum written into
    config.json; unknown values are refused, never coerced."""
    from practicegraph.config import read_prefs

    _server, url, _store = running_server
    base, token = url.split("/?token=")
    assert read_prefs(tmp_path).billing_mode == "unknown"
    status, body = _post(f"{base}/api/billing-mode?token={token}", {"mode": "subscription"})
    assert status == 200 and body["ok"] is True
    assert read_prefs(tmp_path).billing_mode == "subscription"
    status, err = _post(f"{base}/api/billing-mode?token={token}", {"mode": "credits"})
    assert status == 400 and err == {"error": "invalid_mode"}
    status, _err = _post(f"{base}/api/billing-mode?token={token}", {})
    assert status == 400
    assert read_prefs(tmp_path).billing_mode == "subscription"
    # The token gates the action.
    status, _err = _post(f"{base}/api/billing-mode", {"mode": "api"})
    assert status == 403


def test_billing_choices_preserve_other_tools_and_legacy_default(running_server) -> None:
    from practicegraph.config import read_prefs

    _server, url, store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/billing-mode?token={token}"
    assert _post(endpoint, {"mode": "subscription"})[0] == 200
    assert _post(endpoint, {"mode": "api", "tool": "codex"})[0] == 200
    assert _post(endpoint, {"mode": "mixed", "tool": "claude_code"})[0] == 200
    prefs = read_prefs(store.path.parent)
    assert prefs.billing_mode == "subscription"
    assert prefs.billing_by_tool == {"codex": "api", "claude_code": "mixed"}
    for body in [
        {"mode": "api", "tool": "untrusted"}, {"mode": "api", "tool": []},
        {"mode": {}, "tool": "codex"}, {"mode": "credits", "tool": "codex"},
    ]:
        assert _post(endpoint, body)[0] == 400
    assert _post(f"{base}/api/billing-mode", {"mode": "unknown", "tool": "codex"})[0] == 403
    assert read_prefs(store.path.parent) == prefs


def _wait_for_rates(server: UiServer) -> None:
    deadline = time.monotonic() + 5
    while server.refresh_state()["fetching_rates"] and time.monotonic() < deadline:
        time.sleep(0.02)


def test_currency_endpoint_is_a_closed_set_and_fetches_missing_rates(
    running_server, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from practicegraph.analysis import fx
    from practicegraph.config import read_prefs

    server, url, _store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/currency?token={token}"
    _status, model = _get(f"{base}/api/view?token={token}")
    assert model["currency"]["code"] == "USD" and model["currency"]["missing"] is False
    assert model["refresh"]["fetching_rates"] is False

    pulls: list[str] = []

    def pull(_store: object, config: object, _now: object) -> str:
        pulls.append(read_prefs(tmp_path).currency)
        rates = fx.FxRates("2026-09-29", {"EUR": Decimal(1), "USD": Decimal("1.25")})
        catalog = tmp_path / "catalog"
        catalog.mkdir(exist_ok=True)
        (catalog / fx.FX_FILE_NAME).write_text(json.dumps(fx.rates_to_json(rates)))
        return "pulled"

    monkeypatch.setattr(uiserver_module, "pull_fx_rates", pull)
    status, body = _post(endpoint, {"code": "EUR"})
    assert status == 200 and body == {"ok": True}
    _wait_for_rates(server)
    # The rates were missing, so the change downloaded them at once.
    assert pulls == ["EUR"] and read_prefs(tmp_path).currency == "EUR"
    _status, model = _get(f"{base}/api/view?token={token}")
    assert model["currency"]["code"] == "EUR" and model["currency"]["factor"] == "0.8"
    assert model["currency"]["date"] == "2026-09-29"

    # Cached rates are reused; switching back to USD needs none.
    assert _post(endpoint, {"code": "USD"})[0] == 200
    assert _post(endpoint, {"code": "EUR"})[0] == 200
    _wait_for_rates(server)
    assert pulls == ["EUR"]

    for bad in ({"code": "eur"}, {"code": "XAU"}, {"code": 1}, {}):
        status, err = _post(endpoint, bad)
        assert status == 400 and err == {"error": "invalid_currency"}
    assert read_prefs(tmp_path).currency == "EUR"
    assert _post(f"{base}/api/currency", {"code": "USD"})[0] == 403


def test_a_currency_without_rates_is_shown_in_usd_and_says_so(
    running_server, tmp_path,
) -> None:
    server, url, _store = running_server
    base, token = url.split("/?token=")
    # The fixture's pull downloads nothing (a blocked network, say).
    assert _post(f"{base}/api/currency?token={token}", {"code": "JPY"})[0] == 200
    _wait_for_rates(server)
    _status, model = _get(f"{base}/api/view?token={token}")
    currency = model["currency"]
    assert currency["code"] == "USD" and currency["requested"] == "JPY"
    assert currency["missing"] is True and currency["prefix"] == "$"


def test_profile_endpoint_is_a_closed_enum(running_server, tmp_path) -> None:
    """The audience switch (PRODUCTIVITY_PROFILE): a closed enum into
    config.json, exactly the billing-lens shape."""
    from practicegraph.config import read_prefs

    _server, url, _store = running_server
    base, token = url.split("/?token=")
    assert read_prefs(tmp_path).profile == "coding"
    status, body = _post(f"{base}/api/profile?token={token}", {"profile": "productivity"})
    assert status == 200 and body["ok"] is True
    assert read_prefs(tmp_path).profile == "productivity"
    status, err = _post(f"{base}/api/profile?token={token}", {"profile": "manager"})
    assert status == 400 and err == {"error": "invalid_profile"}
    assert read_prefs(tmp_path).profile == "productivity"
    status, _err = _post(f"{base}/api/profile", {"profile": "coding"})
    assert status == 403


def test_drain_endpoint_takes_one_bracket_a_day(running_server) -> None:
    """The felt-drain probe: a closed bracket or a
    skip, once per local day; unknown values refused, a second answer
    refused rather than overwritten."""
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    status, err = _post(f"{base}/api/drain?token={token}", {"felt": "exhausted"})
    assert status == 400 and err == {"error": "invalid_drain"}
    status, body = _post(f"{base}/api/drain?token={token}", {"felt": "worn"})
    assert status == 200 and body["ok"] is True
    status, _err = _post(f"{base}/api/drain?token={token}", {"felt": "fresh"})
    assert status == 400
    status, _err = _post(f"{base}/api/drain", {"felt": "fine"})
    assert status == 403


def test_calibration_endpoint_takes_only_the_estimate(running_server) -> None:
    """The client posts a bracket and nothing else — the session it belongs to
    is resolved server-side, which is what keeps the identity (and the answer)
    out of the browser. An unknown bracket is refused, never coerced."""
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    status, err = _post(f"{base}/api/calibration?token={token}", {"felt": "about an hour"})
    assert status == 400 and err == {"error": "invalid_estimate"}
    # Nothing pending on the fixture history -> refused, never invented.
    status, _err = _post(f"{base}/api/calibration?token={token}", {"felt": "1_2h"})
    assert status == 400
    status, _err = _post(f"{base}/api/calibration?token={token}", {})
    assert status == 400
    # The token gates the action.
    status, _err = _post(f"{base}/api/calibration", {"felt": "1_2h"})
    assert status == 403


def test_root_serves_the_app_shell_without_data(running_server) -> None:
    """The shell and assets are public to the loopback (they carry nothing
    personal); the token gates every /api/ path — data and actions."""
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    with urllib.request.urlopen(f"{base}/", timeout=5) as response:
        assert response.status == 200
        page = response.read().decode("utf-8")
        assert "PracticeGraph" in page or "root" in page
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    status, _body = _get(f"{base}/api/nope?token={token}")
    assert status == 404


def test_presence_endpoint_reports_a_boolean(running_server) -> None:
    _server, url, _store = running_server
    base, token = url.split("/?token=")
    status, body = _get(f"{base}/api/presence?token={token}")
    assert status == 200
    assert isinstance(body["working"], bool)


# --- Robustness: the properties that make restart/refresh survivable. ---


def test_preferred_port_is_stable_per_dir_and_distinct_across_dirs() -> None:
    """The window relies on a consistent origin across restarts, so the port a
    given install prefers must be deterministic — and different installs must not
    fight over the same one."""
    from practicegraph.uiserver import _preferred_port

    a1 = _preferred_port(Path("C:/ProgramData/PracticeGraph"))
    a2 = _preferred_port(Path("C:/ProgramData/PracticeGraph"))
    b = _preferred_port(Path("C:/Users/someone/AppData/Local/PracticeGraph"))
    assert a1 == a2  # stable for the same dir
    assert a1 != b  # distinct between installs
    assert 49000 <= a1 < 51000  # in the intended band


def test_a_live_engine_of_another_version_is_replaced_not_reused(
    running_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Field report 2026-09-17: the new version was installed, but a process
    from six days earlier still answered its own token, so every start
    "reused" it and the new code never ran. Reuse now requires the same
    version; an instance of another version (or one too old to report a
    version) is published over, and its supersession watchdog stands it down."""
    server, _url, _store = running_server
    port = server.server_address[1]
    uiserver_module._write_state_atomic(tmp_path, port, server.token)

    assert uiserver_module._instance_version(port, server.token) == uiserver_module.__version__
    assert uiserver_module._existing_instance(tmp_path) is not None  # same version: reuse
    assert uiserver_module._instance_version(port, "wrong-token") is None

    # The live instance now answers as an older engine would (the handler and
    # this process share one module, so the answer is what gets replaced).
    real_version = uiserver_module._instance_version
    monkeypatch.setattr(
        uiserver_module, "_instance_version",
        lambda p, t: "0.2.11" if real_version(p, t) is not None else None,
    )
    assert uiserver_module._instance_is_ours(port, server.token)   # still identified...
    assert uiserver_module._existing_instance(tmp_path) is None    # ...but not reused
    monkeypatch.setattr(
        uiserver_module, "_instance_version",
        lambda p, t: "" if real_version(p, t) is not None else None,
    )
    assert uiserver_module._existing_instance(tmp_path) is None    # too old to say

    # The start that follows binds its own port next to the old instance and
    # publishes over its endpoint.
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path)
    created = create_server(env)
    assert created is not None
    replacement, _ = created
    try:
        assert replacement.server_address[1] != port
        uiserver_module._write_state_atomic(
            tmp_path, replacement.server_address[1], replacement.token)
        published = uiserver_module._read_state(tmp_path)
        assert published == (replacement.server_address[1], replacement.token)
    finally:
        replacement.server_close()


def test_two_servers_never_share_a_port(tmp_path: Path) -> None:
    """The core anti-collision property: with one server already bound and
    serving, a second create_server for the same install gets a DIFFERENT port —
    the exclusive bind makes stealing the first's port impossible."""
    build_fixture_history(tmp_path)
    env = dict(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path)
    first = create_server(env)
    assert first is not None
    s1, _url1 = first
    thread = threading.Thread(target=s1.serve_forever, daemon=True)
    thread.start()
    try:
        second = create_server(env)
        assert second is not None
        s2, _url2 = second
        try:
            assert s2.server_address[1] != s1.server_address[1]
        finally:
            s2.server_close()
    finally:
        s1.shutdown()
        s1.server_close()


def test_existing_instance_is_identity_checked(running_server) -> None:
    """A stale ui.json whose port was reused by an unrelated process must read as
    'no instance' — reuse is gated on our own token answering /api/ping, not a
    bare socket probe."""
    from practicegraph.uiserver import (
        _existing_instance,
        _instance_is_ours,
        _write_state_atomic,
    )

    server, url, _store = running_server
    port = server.server_address[1]
    token = url.split("/?token=")[1]
    data_dir = server.config.data_dir

    # Our live server, correct token -> recognized.
    assert _instance_is_ours(port, token) is True
    # Same port, wrong token -> not ours (an impostor on a reused port).
    assert _instance_is_ours(port, "not-the-real-token") is False

    # ui.json pointing at our live server -> found.
    _write_state_atomic(data_dir, port, token)
    found = _existing_instance(data_dir)
    assert found is not None and str(port) in found
    # ui.json pointing at a dead port -> treated as no instance.
    _write_state_atomic(data_dir, 51987, "sometoken")
    assert _existing_instance(data_dir) is None


def test_readonly_store_serves_the_view_and_fails_writes_soft(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Machine-store installs (NFR-SEC-1 ACL): a per-user server cannot write
    the service-owned store. The view must still serve — from persisted data,
    skipping ingest — and interactive writes must return a closed 503 code
    instead of resetting the connection."""
    import sqlite3

    server, url, _store = running_server

    def denied(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(uiserver_module, "ingest", denied)
    server.invalidate_view()
    status, view = _get(url.replace("/?token=", "/api/view?token="))
    assert status == 200
    assert "advisor" in view  # the full view model, served read-only

    monkeypatch.setattr(uiserver_module, "record_checkin", denied)
    token = url.split("token=")[1]
    status, body = _post(
        f"http://127.0.0.1:{server.server_address[1]}/api/checkin?token={token}",
        {"rating": 4},
    )
    assert status == 503
    assert body == {"error": "store_readonly"}


def test_service_context_never_spawns_a_shared_dashboard(tmp_path: Path) -> None:
    from practicegraph.config import resolve
    from practicegraph.uiserver import ensure_ui_server_process

    env = {"PRACTICEGRAPH_DATA_DIR": str(tmp_path), "PRACTICEGRAPH_SCAN_PROFILES": "1"}
    assert ensure_ui_server_process(env, resolve(env)) == "skipped_machine_mode_disabled"


def test_system_context_cannot_publish_or_open_personal_state(tmp_path, monkeypatch) -> None:
    from practicegraph import winsec
    from practicegraph.uiserver import _write_state_atomic

    monkeypatch.setattr(winsec, "is_local_system", lambda: True)
    with pytest.raises(PermissionError):
        _write_state_atomic(tmp_path, 49998, "synthetic-token")
    with pytest.raises(PermissionError):
        create_server({"PRACTICEGRAPH_DATA_DIR": str(tmp_path)})
    assert not (tmp_path / "ui.json").exists()


@pytest.mark.parametrize("blocker", ["ingest", "pull_public_news", "read_features"])
def test_view_never_waits_on_blocking_io(
    running_server, monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    """Field incidents (2026-07-19): a domain VM and a corporate laptop both
    hung on "Reading your local record" with almost no data. Two unbounded
    steps ran inside the view request — the log crawl, and the public news
    fetch (which outlives its own timeout on a managed network, where DNS
    and proxy negotiation are outside the socket deadline). Both now run in
    a background worker: a view answers from the persisted store even while
    either is stuck."""
    import time

    server, url, _store = running_server
    hang = threading.Event()

    def stuck(*_args: object, **_kwargs: object) -> object:
        hang.wait(30)
        return []

    monkeypatch.setattr(uiserver_module, blocker, stuck)
    server.invalidate_view()
    started = time.monotonic()
    status, view = _get(url.replace("/?token=", "/api/view?token="))
    elapsed = time.monotonic() - started
    hang.set()
    assert status == 200
    assert "advisor" in view
    assert elapsed < 10, f"view blocked on {blocker} for {elapsed:.1f}s"


def test_serve_never_deletes_the_published_endpoint_before_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inversion of the 2026-07-20 field fix, deliberate (field report
    2026-07-28): serve() used to clear a stale ui.json BEFORE binding, and on
    a fresh install — where every spawn is cold (onefile unpack, AV scan, the
    upgrade rescan) — each new start deleted the PREVIOUS instance's live
    file and then sat unbound for seconds. The published endpoint spent the
    whole start-up storm absent, the shell's poll missed, and its fallback
    opened a browser. Publish already replaces the file atomically, and the
    shell treats a dead/foreign endpoint exactly like an absent one (watchdog
    keeps waiting), so the stale file must now survive until the moment the
    new server replaces it."""
    from practicegraph.store import Store
    from practicegraph.uiserver import UI_STATE_FILE

    data_dir = tmp_path / "kept"
    env = {**FIXTURE_ENV, "PRACTICEGRAPH_DATA_DIR": str(data_dir)}
    data_dir.mkdir()
    Store(data_dir / "state.db").migrate()  # store exists: no first-run init
    stale = data_dir / UI_STATE_FILE
    stale.write_text(
        '{"port": 50093, "token": "old-token-from-a-prior-install"}',
        encoding="utf-8",
    )

    # Stop at the bind, exactly where a cold start spends its seconds.
    def _no_server(_env: dict[str, str]) -> None:
        raise OSError("bind refused (test stops here)")

    monkeypatch.setattr(uiserver_module, "create_server", _no_server)
    assert uiserver_module.serve(env, open_browser=False) == 1
    assert stale.exists(), (
        "a slow or failed start must never take down the previously published endpoint"
    )


def test_a_first_run_creates_the_store_instead_of_refusing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole first-run path, and it used to be a dead end.

    `ui serve` printed "no local state yet - run: practicegraph init" and
    exited 1 on a machine with no store. But the app is the front door, and
    neither installer puts a `practicegraph` binary on PATH — so the only
    instruction on screen named a command the user did not have. The shell fell
    back to the newest rendered report, which on a new machine is empty, and
    the product looked like it had come up blank. Every fresh install hit this.

    There is nothing to ask: creating the store prompts for nothing, touches no
    network, and leaves sharing off.
    """
    from practicegraph.store import Store

    data_dir = tmp_path / "fresh"
    env = {**FIXTURE_ENV, "PRACTICEGRAPH_DATA_DIR": str(data_dir)}
    monkeypatch.setattr(
        uiserver_module,
        "pull_public_news",
        lambda _store, _config, _now: "skipped_recently",
        raising=False,
    )
    assert not Store.in_data_dir(data_dir).exists()

    # serve() blocks in serve_forever, so stop at the point the store exists:
    # bind failure is the first thing after initialization.
    def _no_server(_env: dict[str, str]) -> None:
        raise OSError("bind refused (test stops here)")

    monkeypatch.setattr(uiserver_module, "create_server", _no_server)
    assert uiserver_module.serve(env, open_browser=False) == 1

    # The point: it initialized on the way through rather than bailing out.
    assert Store.in_data_dir(data_dir).exists()

    # And sharing stays off — a silent init must not imply consent.
    from practicegraph.consent import read_consent

    assert read_consent(data_dir).emission_enabled is False


def test_serving_an_upgraded_store_migrates_before_any_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field report 2026-08-21 (macOS VM): a July-era store under the 0.1.27
    engine failed every /api/view with `no such column: git_commit_attempts`
    until one `agent run --once` happened to migrate it. Migrations ran only on
    the ingest path, and both native shells spawn `ui serve` on window-open — so
    a freshly upgraded install served reads from a pre-migration schema, and the
    app came up broken for as long as the first tick took to arrive. Opening the
    server is a migration point now, so an upgraded store can never be read at
    the old shape.

    The background refresh is stubbed out deliberately: it is the tick this test
    must prove the view no longer depends on.
    """
    from practicegraph.store import STORE_SCHEMA_VERSION, Store

    monkeypatch.setattr(UiServer, "_refresh_async", lambda self: None)
    data_dir = tmp_path / "upgraded"
    build_store_at_schema(data_dir / "state.db", 10)
    env = {**FIXTURE_ENV, "PRACTICEGRAPH_DATA_DIR": str(data_dir)}

    created = create_server(env)
    assert created is not None
    server, _url = created
    try:
        view = json.loads(server.view_json())
    finally:
        server.server_close()

    assert view["schema"] == VIEW_SCHEMA
    assert Store.in_data_dir(data_dir).schema_version() == STORE_SCHEMA_VERSION


def test_a_superseded_server_stands_down_and_leaves_the_new_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The convoy drain (field report 2026-07-28): instances that lose the
    ui.json race must not serve invisibly forever. When a DIFFERENT live
    instance owns the published endpoint, the older server shuts itself down
    — and its exit cleanup leaves the new instance's file untouched."""
    import time as _time

    from practicegraph.store import Store
    from practicegraph.uiserver import (
        UI_STATE_FILE,
        _write_state_atomic,
        create_server,
        serve,
    )

    monkeypatch.setattr(uiserver_module, "SUPERSESSION_CHECK_S", 0.2)
    monkeypatch.setattr(
        uiserver_module,
        "pull_public_news",
        lambda _store, _config, _now: "skipped_recently",
        raising=False,
    )
    data_dir = tmp_path / "convoy"
    data_dir.mkdir()
    Store(data_dir / "state.db").migrate()
    env = {**FIXTURE_ENV, "PRACTICEGRAPH_DATA_DIR": str(data_dir)}

    first_done = threading.Event()
    first_exit: list[int] = []

    def _run_first() -> None:
        first_exit.append(serve(env, open_browser=False))
        first_done.set()

    first = threading.Thread(target=_run_first, daemon=True)
    first.start()
    deadline = _time.time() + 10
    while _time.time() < deadline:
        if (data_dir / UI_STATE_FILE).exists():
            break
        _time.sleep(0.05)
    assert (data_dir / UI_STATE_FILE).exists(), "first server never published"

    # A second live instance takes over the published endpoint.
    created = create_server(env)
    assert created is not None
    second, _url = created
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    _write_state_atomic(data_dir, second.server_address[1], second.token)

    assert first_done.wait(timeout=10), "superseded server never stood down"
    assert first_exit == [0]
    # The old instance's exit cleanup must not remove the NEW state file.
    state = json.loads((data_dir / UI_STATE_FILE).read_text(encoding="utf-8"))
    assert state["port"] == second.server_address[1]
    second.shutdown()
    second.server_close()


def test_day_close_pieces_probe_rider_is_no_peek(running_server) -> None:
    """The W4 probe rides the day-close POST: a felt count is journaled with
    the server-computed measured side, the response NEVER carries the
    measured count (no-peek), and a junk value neither blocks the close nor
    writes a pair."""
    _server, url, store = running_server
    base, token = url.split("/?token=")
    today = _local_today(_server.config.data_dir)

    status, body = _post(f"{base}/api/day-close?token={token}", {"day": today, "pieces": 3})
    assert status == 200 and body == {"ok": True}  # nothing measured leaks
    raw = store.meta_get(f"wu_probe:{today}")
    assert raw is not None
    pair = json.loads(raw)
    assert pair["felt"] == 3
    assert "measured" in pair

    # Junk pieces on another close attempt: close stays 200, no overwrite.
    status, body = _post(f"{base}/api/day-close?token={token}", {"day": today, "pieces": "lots"})
    assert status == 200
    assert json.loads(store.meta_get(f"wu_probe:{today}"))["felt"] == 3


def test_feed_day_serves_archived_editions_through_the_strict_parse(
    running_server,
    tmp_path,
) -> None:
    """The dated shelf behind news and build ideas: closed channel enum,
    closed day shape, re-parsed on read - a tampered file is an empty day."""
    import json as _json

    _server, url, _store = running_server
    base, token = url.split("/?token=")
    shelf = tmp_path / "catalog" / "history" / "news"
    shelf.mkdir(parents=True)
    edition = {
        "news_version": "day-one",
        "items": [
            {
                "id": "day-one",
                "kind": "release",
                "title": "Edition one",
                "hook": "A compact control surface keeps common actions close.",
                "summary": "The project maps a small keyboard to controls.",
                "why": "A concrete example of shaping tools around work.",
                "url": "https://example.org/one",
                "source": "Example",
            }
        ],
    }
    (shelf / "2026-07-01.json").write_text(_json.dumps(edition), encoding="utf-8")
    status, body = _get(f"{base}/api/feed-day?token={token}&channel=news&day=2026-07-01")
    assert status == 200
    assert [i["id"] for i in body["items"]] == ["day-one"]

    status, body = _get(f"{base}/api/feed-day?token={token}&channel=news&day=2026-07-02")
    assert status == 200 and body["items"] == []
    status, _body = _get(f"{base}/api/feed-day?token={token}&channel=diary&day=2026-07-01")
    assert status == 400
    status, _body = _get(f"{base}/api/feed-day?token={token}&channel=news&day=..%2F..%2Fsecret")
    assert status == 400
    status, _body = _get(f"{base}/api/feed-day?channel=news&day=2026-07-01")
    assert status == 403


def test_pin_model_endpoint_validates_against_the_catalog_and_edits_safely(
    running_server,
    tmp_path,
) -> None:
    """The one-click default: model validated against the served ladder and
    translated to the config's own value; the file edit runs the pin
    protocol (backup, atomic swap, verify)."""
    import json as _json

    server, url, _store = running_server
    base, token = url.split("/?token=")
    claude_dir = tmp_path / "pinhomes" / "claude"
    codex_dir = tmp_path / "pinhomes" / "codex"
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    server.env = dict(server.env)
    server.env["PRACTICEGRAPH_CLAUDE_HOME"] = str(claude_dir)
    server.env["PRACTICEGRAPH_CODEX_HOME"] = str(codex_dir)

    # A model outside the ladder is refused before any file is touched.
    status, err = _post(
        f"{base}/api/pin-model?token={token}",
        {"tool": "claude_code", "model": "made-up", "scope": "machine"},
    )
    assert status == 400 and err == {"error": "invalid_pin"}

    # No built-in recommendation authorizes a config write before a catalog arrives.
    status, err = _post(
        f"{base}/api/pin-model?token={token}",
        {"tool": "claude_code", "model": "Claude Sonnet", "scope": "machine"},
    )
    assert status == 400 and err == {"error": "invalid_pin"}
    from synthetic_model_catalog_fixture import SAMPLE_CATALOG_DOCUMENT

    catalog_dir = _store.path.parent / "catalog"
    catalog_dir.mkdir(exist_ok=True)
    (catalog_dir / "model-catalog.json").write_text(
        _json.dumps(SAMPLE_CATALOG_DOCUMENT),
        encoding="utf-8",
    )

    # The catalog names "Claude Sonnet"; the config receives "sonnet".
    status, body = _post(
        f"{base}/api/pin-model?token={token}",
        {"tool": "claude_code", "model": "Claude Sonnet", "effort": "high", "scope": "machine"},
    )
    assert status == 200 and body["ok"] is True and body["outcome"] == "pinned"
    written = _json.loads((claude_dir / "settings.json").read_text())
    assert written == {"model": "sonnet", "effortLevel": "high"}

    # Codex: the id is the value; comments and sections survive the edit.
    (codex_dir / "config.toml").write_text(
        '# keep me\n[mcp_servers.x]\ncommand = "docker"\n', encoding="utf-8"
    )
    status, body = _post(
        f"{base}/api/pin-model?token={token}",
        {"tool": "codex", "model": "gpt-5.6-terra", "effort": "medium", "scope": "machine"},
    )
    assert status == 200 and body["outcome"] == "pinned"
    text = (codex_dir / "config.toml").read_text()
    assert 'model = "gpt-5.6-terra"' in text and "# keep me" in text

    # A project pin resolves ONLY through the sessions' own recorded cwds.
    status, err = _post(
        f"{base}/api/pin-model?token={token}",
        {
            "tool": "claude_code",
            "model": "Claude Haiku",
            "scope": "project",
            "project": "NotARealProject",
        },
    )
    assert status == 400 and err == {"error": "unknown_project"}

    real = tmp_path / "work" / "Alpha"
    real.mkdir(parents=True)
    proj_logs = claude_dir / "projects" / "enc"
    proj_logs.mkdir(parents=True)
    (proj_logs / "s.jsonl").write_text(
        _json.dumps({"type": "user", "cwd": str(real)}) + "\n", encoding="utf-8"
    )
    status, body = _post(
        f"{base}/api/pin-model?token={token}",
        {"tool": "claude_code", "model": "Claude Haiku", "scope": "project", "project": "Alpha"},
    )
    assert status == 200 and body["outcome"] == "pinned"
    project_written = _json.loads((real / ".claude" / "settings.json").read_text())
    assert project_written == {"model": "haiku"}

    # A refusal travels as its own outcome, never a silent no-op.
    (codex_dir / "config.toml").write_text("model = broken [", encoding="utf-8")
    status, body = _post(
        f"{base}/api/pin-model?token={token}",
        {"tool": "codex", "model": "gpt-5.6-sol", "scope": "machine"},
    )
    assert status == 409 and body["outcome"] == "refused_unreadable"


def test_capability_review_and_skip_are_closed_local_actions(running_server) -> None:
    from practicegraph.analysis.capability_ledger import (
        CAPABILITY_SKIP_KEY,
        PracticeEntry,
        read_practice,
        read_reviews,
        write_practice,
    )

    _server, url, store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/capability-review?token={token}"
    assert _post(endpoint, {"feedback": "helpful", "raw": "private"})[0] == 400
    assert _post(endpoint, {"feedback": "helpful"})[0] == 409
    write_practice(
        store,
        PracticeEntry(
            "run_verification",
            "Verify",
            "software",
            "2026-09-05",
            "private-unit",
            "2026-09-05T10:00:00+00:00",
            0,
        ),
    )
    assert _post(endpoint, {"feedback": "not_tried"})[0] == 200
    assert read_practice(store) is None
    assert read_reviews(store)[0].feedback == "not_tried"
    assert _post(f"{base}/api/capability-outcome?token={token}", {"outcome": "skip"})[0] == 200
    assert store.meta_get(CAPABILITY_SKIP_KEY) == _local_today(_server.config.data_dir)


def test_clearing_coach_history_requires_explicit_confirmation(running_server) -> None:
    from practicegraph.analysis.capability_ledger import (
        CAPABILITY_OUTCOMES_KEY,
        CAPABILITY_PRACTICE_KEY,
        CAPABILITY_REVIEWS_KEY,
    )

    _server, url, store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/capability-clear?token={token}"
    store.meta_set(CAPABILITY_REVIEWS_KEY, "old private history")
    store.meta_set("other_private_ledger", "keep")
    for body in ({}, {"confirm": False}, {"confirm": 1}, {"confirm": True, "extra": 1}):
        assert _post(endpoint, body)[0] == 400
    assert store.meta_get(CAPABILITY_REVIEWS_KEY) == "old private history"
    assert _post(endpoint, {"confirm": True})[0] == 200
    assert store.meta_get(CAPABILITY_REVIEWS_KEY) == "[]"
    assert store.meta_get(CAPABILITY_OUTCOMES_KEY) == "[]"
    assert store.meta_get(CAPABILITY_PRACTICE_KEY) == "null"
    assert store.meta_get("other_private_ledger") == "keep"


def test_learning_api_requires_token_and_uses_closed_private_state(running_server):
    from practicegraph.analysis.training import KEY

    _server, url, store = running_server
    base, token = url.split("/?token=")
    endpoint = f"{base}/api/training?token={token}"
    assert _get(f"{base}/api/training")[0] == 403
    assert _get(f"{base}/api/training/export")[0] == 403
    assert _post(f"{base}/api/training", {"action": "preferences"})[0] == 403
    prefs = {"action": "preferences", "goal": "verify_results", "tool": "codex",
             "free_only": True, "max_minutes": 60}
    status, value = _post(endpoint, prefs)
    assert status == 200 and value["prefs"]["tool"] == "codex"
    assert "verify_results" in store.meta_get(KEY)
    assert _post(endpoint, {**prefs, "secret": "must-not-be-saved"})[0] == 400
    assert "must-not-be-saved" not in store.meta_get(KEY)
    status, backup = _get(f"{base}/api/training/export?token={token}")
    assert status == 200 and backup["schema"] == "practicegraph.learning-backup/1"
    for body in ({"action": "clear"}, {"action": "clear", "confirm": 1}):
        assert _post(endpoint, body)[0] == 400
    assert _post(endpoint, {"action": "clear", "confirm": True})[0] == 200
    assert store.meta_get(KEY) is None


def test_token_prices_api_is_gated_and_returns_verified_cache(running_server):
    from tools.token_prices import build

    server, url, _store = running_server
    root, query = url.split("?", 1)
    assert _get(root + "api/token-prices")[0] == 403
    fixture = Path(__file__).parent / "fixtures/token-prices-research.json"
    research = json.loads(fixture.read_text())
    document = build(research)
    target = server.config.data_dir / "catalog" / "token-prices.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(document))
    code, body = _get(root + "api/token-prices?" + query)
    assert code == 200
    assert body["edition"] == document
    assert body["changes"] == []


def test_manual_catalog_download_is_authenticated_and_reports_result(running_server, monkeypatch):
    _server, url, _store = running_server
    root, query = url.split("?", 1)
    calls = []
    def pull(*args, **kwargs):
        calls.append(kwargs)
        return "invalid_artifact"
    monkeypatch.setattr(uiserver_module, "pull_public_token_prices", pull)
    assert _post(root + "api/token-prices/refresh", {})[0] == 403
    assert not calls
    assert _post(
        root + "api/token-prices/refresh?" + query, {"url": "https://other.example"},
    )[0] == 400
    code, body = _post(root + "api/token-prices/refresh?" + query, {})
    assert code == 200 and calls == [{"manual": True}]
    assert body["refresh_result"] == "invalid_artifact"
    assert body["download"]["last_attempt"]
