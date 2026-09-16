"""Closed training catalog: sourced learning choices, never inferred proficiency."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from practicegraph.analysis.harness_playbooks import ID, VERSION, safe_source, text_value

SCHEMA = "practicegraph.training/1"
GOALS = {
    "verify_results": "Verify AI-generated work",
    "harness_workflow": "Use my coding harness effectively",
    "agent_building": "Build and evaluate agents",
    "certification": "Explore a professional certification",
}
TOOLS = {"any": "Either tool", "codex": "Codex", "claude_code": "Claude Code"}
KINDS = ("course", "lab", "path", "certification")
CREDENTIALS = ("none", "completion_badge", "completion_certificate", "professional_certification")
REVIEW_DAYS = 30


@dataclass(frozen=True)
class TrainingEdition:
    version: str
    published_on: str
    entries: tuple[dict[str, Any], ...]


def _day(value: object) -> bool:
    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value


def valid_entry(entry: Any) -> bool:
    fields = {
        "id",
        "revision",
        "title",
        "provider",
        "url",
        "goal",
        "tools",
        "kind",
        "credential",
        "summary",
        "why",
        "prerequisites",
        "duration_minutes",
        "duration_basis",
        "cost",
        "cost_note",
        "steps",
        "sources",
        "reviewed_on",
        "status",
        "successor",
        "limitations",
    }
    try:
        if not isinstance(entry, dict) or set(entry) != fields:
            return False
        if (
            not isinstance(entry["id"], str)
            or not ID.fullmatch(entry["id"])
            or type(entry["revision"]) is not int
            or not 1 <= entry["revision"] <= 100000
            or entry["goal"] not in GOALS
            or entry["kind"] not in KINDS
            or entry["credential"] not in CREDENTIALS
            or entry["status"] not in ("active", "retired")
            or entry["cost"] not in ("free", "paid", "unknown")
            or not safe_source(entry["url"])
            or not _day(entry["reviewed_on"])
        ):
            return False
        # A completion certificate is not an industry/professional certification.
        professional = entry["credential"] == "professional_certification"
        if professional != (entry["kind"] == "certification") or (
            professional and entry["goal"] != "certification"
        ):
            return False
        if any(
            not text_value(entry[key], bound)
            for key, bound in (
                ("title", 120),
                ("provider", 100),
                ("summary", 500),
                ("why", 500),
                ("prerequisites", 500),
                ("cost_note", 300),
                ("limitations", 500),
            )
        ):
            return False
        tools = entry["tools"]
        if (
            not isinstance(tools, list)
            or not 1 <= len(tools) <= 2
            or any(tool not in TOOLS for tool in tools)
            or len(set(tools)) != len(tools)
            or ("any" in tools and len(tools) != 1)
        ):
            return False
        duration, basis = entry["duration_minutes"], entry["duration_basis"]
        if duration is None:
            if basis != "unknown":
                return False
        elif (
            type(duration) is not int
            or not 1 <= duration <= 120000
            or basis
            not in (
                "provider",
                "editorial_estimate",
            )
        ):
            return False
        successor = entry["successor"]
        if successor is not None and (
            not isinstance(successor, str)
            or not ID.fullmatch(successor)
            or successor == entry["id"]
            or entry["status"] != "retired"
        ):
            return False
        if (
            not isinstance(entry["sources"], list)
            or not 1 <= len(entry["sources"]) <= 5
            or not all(safe_source(url) for url in entry["sources"])
        ):
            return False
        steps = entry["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
            return False
        return all(
            isinstance(step, dict)
            and set(step) == {"title", "url", "outcome"}
            and text_value(step["title"], 120)
            and safe_source(step["url"])
            and text_value(step["outcome"], 300)
            for step in steps
        )
    except (ValueError, TypeError, KeyError):
        return False


def parse_training(value: object) -> TrainingEdition | None:
    try:
        if not isinstance(value, dict) or set(value) != {
            "schema",
            "training_version",
            "published_on",
            "entries",
        }:
            return None
        if (
            value["schema"] != SCHEMA
            or not isinstance(value["training_version"], str)
            or not VERSION.fullmatch(value["training_version"])
            or not _day(value["published_on"])
            or not isinstance(value["entries"], list)
            or len(value["entries"]) > 50
            or not all(valid_entry(e) for e in value["entries"])
        ):
            return None
        entries = value["entries"]
        ids = {entry["id"] for entry in entries}
        if len(ids) != len(entries) or any(
            entry["reviewed_on"] > value["published_on"]
            or (entry["successor"] is not None and entry["successor"] not in ids)
            for entry in entries
        ):
            return None
        # Retirements must lead to an active replacement, not a cycle of old entries.
        by_id = {entry["id"]: entry for entry in entries}
        if any(e["successor"] and by_id[e["successor"]]["status"] != "active" for e in entries):
            return None
        return TrainingEdition(value["training_version"], value["published_on"], tuple(entries))
    except (ValueError, TypeError, KeyError):
        return None
