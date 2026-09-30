"""End-to-end: the real CLI on real-shaped logs renders the feature
(NFR-QLT-2 — fixture-only proof is insufficient)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURE_ENV, GOLDENS, build_store_at_schema
from practicegraph.agent import initialize
from practicegraph.analysis.schedule import ScheduleUpdate, save_schedule_profile
from practicegraph.cli import main

REPORT_ARGS = [
    "report",
    "--date",
    "2026-07-02",
    "--generated-at",
    "2026-07-02T18:00:00+00:00",
]


def _confirm_fixture_schedule(data_dir: Path) -> None:
    save_schedule_profile(
        data_dir,
        ScheduleUpdate(
            timezone_name="UTC",
            working_days=(0, 1, 2, 3, 4),
            work_start="09:00",
            work_end="18:00",
            quiet_start="22:00",
            quiet_end="07:00",
            weekend_mode="exceptional",
        ),
    )


def _subprocess_env(tmp_path: Path, initialized: bool = True) -> dict[str, str]:
    env = os.environ.copy()
    env.update(FIXTURE_ENV)
    env["PRACTICEGRAPH_DATA_DIR"] = str(tmp_path / "data")
    if initialized:
        initialize(env)  # a healthy install is an initialized one
        _confirm_fixture_schedule(Path(env["PRACTICEGRAPH_DATA_DIR"]))
    return env


def test_report_in_process_matches_golden(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The initialized product path is byte-golden, including observation context."""
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(data_dir))
    initialize(dict(os.environ))
    _confirm_fixture_schedule(data_dir)
    assert main(REPORT_ARGS) == 0
    out = capsys.readouterr().out
    assert out == (GOLDENS / "daily_report.txt").read_bytes().decode("utf-8")


def test_report_via_real_cli_subprocess(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "practicegraph", *REPORT_ARGS],
        capture_output=True,
        env=_subprocess_env(tmp_path, initialized=True),
    )
    assert proc.returncode == 0, proc.stderr.decode()
    # Windows consoles translate \n on write; normalize before the byte compare.
    assert proc.stdout.replace(b"\r\n", b"\n") == (GOLDENS / "daily_report.txt").read_bytes()


def test_report_with_history_renders_trend_and_focus(tmp_path: Path) -> None:
    """With an initialized store the report gains the history-backed sections
    (M1/M3/M4) — exercised through the real CLI."""
    proc = subprocess.run(
        [sys.executable, "-m", "practicegraph", *REPORT_ARGS],
        capture_output=True,
        env=_subprocess_env(tmp_path, initialized=True),
    )
    assert proc.returncode == 0, proc.stderr.decode()
    out = proc.stdout.decode("utf-8")
    assert "TREND (LAST 7 DAYS)" in out
    assert "* 2026-07-02" in out
    assert "FOCUS (local only)" in out
    assert "Longest uninterrupted block: 4 min" in out


def test_report_writes_byte_exact_html(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(data_dir))
    initialize(dict(os.environ))
    _confirm_fixture_schedule(data_dir)
    out_file = tmp_path / "report.html"
    assert main([*REPORT_ARGS, "--html", str(out_file)]) == 0
    capsys.readouterr()  # drain
    assert out_file.read_bytes() == (GOLDENS / "daily_report.html").read_bytes()


def test_report_migrates_an_upgraded_store_before_reading_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the 2026-08-21 upgrade regression. Migrations ran only
    on the agent tick, so a store left at an older schema by the previous
    release crashed `report` with `no such column: git_commit_attempts` —
    exactly like the dashboard, and on the surface the app falls back TO when
    the dashboard is unavailable. Rendering migrates first now."""
    from practicegraph.store import STORE_SCHEMA_VERSION, Store

    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(data_dir))
    build_store_at_schema(data_dir / "state.db", 10)
    _confirm_fixture_schedule(data_dir)

    assert main(REPORT_ARGS) == 0
    assert "PRACTICEGRAPH DAILY REPORT" in capsys.readouterr().out
    assert Store.in_data_dir(data_dir).schema_version() == STORE_SCHEMA_VERSION


def test_doctor_via_real_cli_subprocess(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "practicegraph", "doctor"],
        capture_output=True,
        env=_subprocess_env(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr.decode()
    document = json.loads(proc.stdout.decode("utf-8"))
    assert document["status"] == "healthy"
    assert document["schema"] == "practicegraph.doctor/1"


def test_usage_error_exits_two() -> None:
    """FR-DIA-2: command errors exit 2."""
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "--date", "not-a-date"])
    assert excinfo.value.code == 2


def test_schedule_set_and_show(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(tmp_path / "data"))
    assert main(
        [
            "schedule",
            "set",
            "--timezone",
            "Europe/Sofia",
            "--working-days",
            "mon,tue,wed,thu,fri",
            "--work-start",
            "09:00",
            "--work-end",
            "18:00",
            "--quiet-start",
            "22:00",
            "--quiet-end",
            "07:00",
            "--weekend-mode",
            "exceptional",
        ]
    ) == 0
    assert "Europe/Sofia" in capsys.readouterr().out
    assert main(["schedule", "show"]) == 0
    output = capsys.readouterr().out
    assert "confirmed: yes" in output
    assert "working days: mon,tue,wed,thu,fri" in output


def test_init_and_consent_flow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """FR-CNS-1/2: default OFF with a plain local-only statement, changeable
    any time, and `show` displays the exact boundary."""
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(tmp_path / "data"))
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)

    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "nothing leaves this machine" in out.lower()

    assert main(["consent", "status"]) == 0
    assert "OFF (default)" in capsys.readouterr().out

    assert main(["consent", "on"]) == 0
    capsys.readouterr()
    assert main(["consent", "show"]) == 0
    out = capsys.readouterr().out
    assert "Sharing: ON" in out
    assert "SYNTHETIC EXAMPLE" in out  # nothing queued yet -> labeled example
    assert "Never content," in out

    assert main(["consent", "off"]) == 0
    capsys.readouterr()
    assert main(["consent", "status"]) == 0
    assert "OFF" in capsys.readouterr().out


def test_agent_run_once_via_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(tmp_path / "data"))
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    assert main(["agent", "run", "--once"]) == 0
    out = capsys.readouterr().out
    assert "report_artifact=ok" in out
    assert "emit_build=skipped_consent_off" in out


def test_suggest_and_focus_cli_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(tmp_path / "data"))
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    assert main(["init"]) == 0
    capsys.readouterr()

    assert main(["suggest", "list"]) == 0
    assert "route-routine-to-midtier: active" in capsys.readouterr().out
    assert main(["suggest", "dismiss", "route-routine-to-midtier"]) == 0
    capsys.readouterr()
    assert main(["suggest", "list"]) == 0
    assert "route-routine-to-midtier: dismissed" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["suggest", "bogus-command"])

    assert main(["focus", "status"]) == 0
    assert "Focus coaching: ON" in capsys.readouterr().out
    assert main(["focus", "off"]) == 0
    capsys.readouterr()
    assert main(["focus", "status"]) == 0
    assert "Focus coaching: OFF" in capsys.readouterr().out
    assert main(["focus", "on"]) == 0
    capsys.readouterr()
    assert main(["focus", "dismiss", "pause-between-bursts"]) == 0
    assert "dismissed everywhere" in capsys.readouterr().out
    assert main(["focus", "dismiss", "not-a-tip"]) == 2

    assert main(["hygiene", "--age-days", "1"]) == 0
    out = capsys.readouterr().out
    assert "un-sent emits are never pruned" in out


def test_reflect_cli_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The reflection-styling switch: off by default, on names a local
    provider, off again — and every message stays local-only in tone."""
    monkeypatch.setenv("PRACTICEGRAPH_DATA_DIR", str(tmp_path / "data"))
    for key, value in FIXTURE_ENV.items():
        monkeypatch.setenv(key, value)
    assert main(["init"]) == 0
    capsys.readouterr()

    assert main(["reflect", "status"]) == 0
    assert "OFF" in capsys.readouterr().out

    assert main(["reflect", "on", "claude"]) == 0
    assert "ON via 'claude'" in capsys.readouterr().out
    assert main(["reflect", "status"]) == 0
    status = capsys.readouterr().out
    assert "ON via 'claude'" in status
    assert "shared" in status  # local-only reassurance on every status

    assert main(["reflect", "off"]) == 0
    assert "OFF" in capsys.readouterr().out

    # An unknown provider is rejected at the parser (closed choice set).
    with pytest.raises(SystemExit):
        main(["reflect", "on", "gpt-9000"])
    with pytest.raises(SystemExit):
        main(["reflect", "bogus-command"])
