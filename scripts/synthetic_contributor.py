"""Fleet-pilot phase 2: a synthetic SECOND contributor (LOCAL DEMO ONLY).

Builds a valid v2 payload from the deterministic test fixtures and posts it
to the local pilot server so a chosen day crosses the k=2 threshold — the
dashboard then demonstrably RELEASES that day while single-contributor days
stay suppressed (INV-4 shown working, both directions).

Usage:
    .venv\\Scripts\\python.exe scripts\\synthetic_contributor.py 2026-07-02
"""

from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

SERVER = "http://127.0.0.1:8321"
TOKEN = "demo-token-123"  # pilot-only credential (loopback server)


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-07-02"
    from conftest import build_fixture_history, build_fixture_payload

    store, health = build_fixture_history()
    payload = build_fixture_payload(
        store, health, emit_id=str(uuid.uuid4()), day=day, org_id="acme-eng"
    )
    request = urllib.request.Request(
        f"{SERVER}/v1/ingest",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        print(f"{day}: HTTP {response.status} "
              f"{response.read().decode('utf-8').strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
