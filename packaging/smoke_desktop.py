"""Exercise a built desktop engine using only synthetic records and disabled catalogs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from http.client import HTTPConnection
from pathlib import Path


def smoke(engine: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    engine = engine.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="practicegraph-desktop-smoke-") as temporary:
        data = Path(temporary) / "data"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRACTICEGRAPH_")}
        env.update(
            PRACTICEGRAPH_DATA_DIR=str(data),
            PRACTICEGRAPH_CLAUDE_HOME=str(root / "tests/fixtures/claude_code"),
            PRACTICEGRAPH_CODEX_HOME=str(root / "tests/fixtures/codex"),
            PRACTICEGRAPH_CLAUDE_DESKTOP=str(Path(temporary) / "absent-desktop"),
        )
        for key in (
            "API_BASE",
            "CONTENT_BASE",
            "SKILLS",
            "NEWS",
            "MODELS",
            "ADVISOR",
            "RATECARD",
            "LICENSE",
            "UPDATE",
            "DOCS",
            "MODEL_CATALOG",
            "BUILD_IDEAS",
            "COMMUNITY",
            "HARNESS_PLAYBOOKS",
            "TRAINING",
        ):
            env[f"PRACTICEGRAPH_{key}_URL"] = ""
        subprocess.run(
            [str(engine), "init"], env=env, check=True, timeout=45, stdout=subprocess.DEVNULL
        )
        subprocess.run(
            [str(engine), "agent", "run", "--once"],
            env=env,
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
        )
        assert (data / "state.db").is_file(), "Engine did not initialize a store"
        assert (data / "status.json").is_file(), "Engine did not finish the fixture tick"
        process = subprocess.Popen(
            [str(engine), "ui", "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 30
            endpoint = None
            while time.monotonic() < deadline:
                try:
                    endpoint = json.loads((data / "ui.json").read_text())
                    break
                except (OSError, ValueError):
                    assert process.poll() is None, "UI server exited during startup"
                    time.sleep(0.1)
            assert endpoint, "UI endpoint not published"
            headers = {"X-PracticeGraph-Token": endpoint["token"]}
            connection = HTTPConnection("127.0.0.1", endpoint["port"], timeout=45)
            for path in ("/api/ping", "/", "/api/view", "/api/practice-time", "/api/training"):
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
                body = response.read()
                assert response.status == 200, (
                    f"Packaged endpoint failed: {path} ({response.status})"
                )
                if path == "/":
                    assert b"assets/index-" in body, "Built dashboard was not bundled"
                else:
                    assert isinstance(json.loads(body), dict), f"Invalid endpoint response: {path}"
            connection.request("GET", "/api/view")
            response = connection.getresponse()
            response.read()
            assert response.status == 403, "Unauthenticated local API request was accepted"
            connection.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("Desktop engine smoke passed: fixture tick, dashboard, private APIs, authentication")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    smoke(parser.parse_args().engine)
