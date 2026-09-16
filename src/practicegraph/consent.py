"""Consent state (FR-CNS-1).

Emission consent is opt-in and defaults to OFF. State is stored locally in the
data directory as ``consent.json`` with the decision timestamp. A missing or
unreadable file means consent OFF (fail-open to the private default).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CONSENT_FILE_NAME = "consent.json"

ConsentFileState = Literal["absent", "ok", "invalid"]


@dataclass(frozen=True, slots=True)
class ConsentState:
    emission_enabled: bool
    decided_at: str | None
    file_state: ConsentFileState


def read_consent(data_dir: Path) -> ConsentState:
    path = data_dir / CONSENT_FILE_NAME
    if not path.is_file():
        return ConsentState(emission_enabled=False, decided_at=None, file_state="absent")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ConsentState(emission_enabled=False, decided_at=None, file_state="invalid")
    if not isinstance(raw, dict) or not isinstance(raw.get("emission_enabled"), bool):
        return ConsentState(emission_enabled=False, decided_at=None, file_state="invalid")
    decided_at = raw.get("decided_at")
    return ConsentState(
        emission_enabled=raw["emission_enabled"],
        decided_at=decided_at if isinstance(decided_at, str) else None,
        file_state="ok",
    )
