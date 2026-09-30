"""Legacy machine-store migration is intentionally disabled.

A shared database cannot establish which activity belongs to the current user.
New per-user installs rebuild derived history from that user's own source logs.
The old directory is preserved for an administrator to review separately.
"""

from pathlib import Path

ADOPTED_FILES: tuple[str, ...] = ()
ADOPTED_DIRS: tuple[str, ...] = ()
SKIPPED = ("state.db", "config.json", "catalog", "ui.json", "org_token.bin", "reports")


def machine_data_dir(env: dict[str, str]) -> Path:
    return Path(env.get("ProgramData") or r"C:\ProgramData") / "PracticeGraph"


def adopt_machine_store(data_dir: Path, env: dict[str, str]) -> str:
    return "skipped_machine_privacy"
