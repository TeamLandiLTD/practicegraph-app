"""Adopting a machine-wide store into the per-user directory.

The rule under test is a refusal: adopt ONLY into an empty directory, never
compare, never merge, never delete. It is safe precisely because the store is
derived — every row comes from log files the agent re-reads with cursors — so
"keep what is here and let the next ingest catch up" always converges, while a
merge can produce a history nobody can reconstruct.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from practicegraph.migrate import (
    adopt_machine_store,
)


def _machine_store(root: Path, marker: str = "machine") -> Path:
    """A plausible pre-2026-07-26 %ProgramData% install."""
    old = root / "ProgramData" / "PracticeGraph"
    (old / "catalog").mkdir(parents=True)
    (old / "reports").mkdir()
    conn = sqlite3.connect(old / "state.db")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('origin', ?)", (marker,))
    conn.commit()
    conn.close()
    (old / "config.json").write_text('{"billing_mode": "api"}', encoding="utf-8")
    (old / "catalog" / "rate-card.json").write_text("{}", encoding="utf-8")
    (old / "ui.json").write_text('{"port": 49193, "token": "stale"}', encoding="utf-8")
    (old / "status.json").write_text("{}", encoding="utf-8")
    (old / "org_token.bin").write_bytes(b"\x01\x02")
    (old / "reports" / "daily-2026-07-01.html").write_text("x", encoding="utf-8")
    return old


def _env(root: Path) -> dict[str, str]:
    return {"ProgramData": str(root / "ProgramData")}


def _origin(db: Path) -> str:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT value FROM meta WHERE key='origin'").fetchone()[0]
    finally:
        conn.close()


def test_shared_history_is_never_copied_into_a_personal_store(tmp_path: Path) -> None:
    old = _machine_store(tmp_path)
    user = tmp_path / "LocalAppData" / "PracticeGraph"
    before = {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    assert adopt_machine_store(user, _env(tmp_path)) == "skipped_machine_privacy"
    assert not user.exists()
    assert before == {p.relative_to(old): p.read_bytes() for p in old.rglob("*") if p.is_file()}


def test_existing_personal_history_is_untouched(tmp_path: Path) -> None:
    _machine_store(tmp_path)
    user = tmp_path / "user"
    user.mkdir()
    (user / "state.db").write_bytes(b"synthetic personal state")
    adopt_machine_store(user, _env(tmp_path))
    assert (user / "state.db").read_bytes() == b"synthetic personal state"


def test_missing_shared_history_is_also_a_noop(tmp_path: Path) -> None:
    user = tmp_path / "user"
    assert adopt_machine_store(user, {}) == "skipped_machine_privacy"
    assert not user.exists()
