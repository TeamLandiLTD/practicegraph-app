"""Public data contract for privately curated setup guidance; no executable recipes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "practicegraph.harness-playbooks/1"
CLIENT_RECIPE_VERSION = 1
RECIPES = {
    "verification_setup": "verification_gap",
    "failure_investigation": "repeated_failures",
    "instruction_map": "instruction_gap",
}
STACKS = ("python", "javascript")
TOOLS = ("codex", "claude_code")
ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SEMVER = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}\Z")


def text_value(value: object, limit: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value.strip()) <= limit
        and all(ord(c) >= 32 or c == "\n" for c in value)
    )


def safe_source(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 1000:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and all(ord(c) > 32 for c in value)
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class PlaybookEdition:
    version: str
    published_on: str
    entries: tuple[dict[str, Any], ...]


def valid_entry(entry: Any) -> bool:
    fields = {
        "id",
        "revision",
        "recipe",
        "detector",
        "stacks",
        "harnesses",
        "title",
        "explanation",
        "guidance",
        "limitations",
        "sources",
        "reviewed_on",
        "status",
        "successor",
        "min_client_recipe_version",
    }
    if not isinstance(entry, dict) or set(entry) != fields:
        return False
    try:
        if (
            not isinstance(entry["id"], str)
            or not ID.fullmatch(entry["id"])
            or type(entry["revision"]) is not int
            or not 1 <= entry["revision"] <= 100000
            or entry["recipe"] not in RECIPES
            or entry["detector"] != RECIPES[entry["recipe"]]
            or type(entry["min_client_recipe_version"]) is not int
            or not 1 <= entry["min_client_recipe_version"] <= 10000
            or entry["status"] not in ("active", "withdrawn")
            or (
                entry["successor"] is not None
                and (
                    not isinstance(entry["successor"], str)
                    or not ID.fullmatch(entry["successor"])
                    or entry["successor"] == entry["id"]
                )
            )
        ):
            return False
        stacks = entry["stacks"]
        if (
            not isinstance(stacks, list)
            or not stacks
            or len(stacks) > len(STACKS)
            or any(s not in STACKS for s in stacks)
            or len(set(stacks)) != len(stacks)
        ):
            return False
        harnesses = entry["harnesses"]
        if not isinstance(harnesses, list) or not 1 <= len(harnesses) <= len(TOOLS):
            return False
        for harness in harnesses:
            if (
                not isinstance(harness, dict)
                or set(harness) != {"tool", "min_version", "max_version"}
                or harness["tool"] not in TOOLS
            ):
                return False
            for key in ("min_version", "max_version"):
                value = harness[key]
                if value is not None and (
                    not isinstance(value, str) or not SEMVER.fullmatch(value)
                ):
                    return False
            low, high = harness["min_version"], harness["max_version"]
            if low and high and version_tuple(low) > version_tuple(high):
                return False
        if len({h["tool"] for h in harnesses}) != len(harnesses):
            return False
        if any(
            not text_value(entry[key], bound)
            for key, bound in (
                ("title", 120),
                ("explanation", 1000),
                ("guidance", 2000),
                ("limitations", 1000),
            )
        ):
            return False
        if (
            not isinstance(entry["sources"], list)
            or not 1 <= len(entry["sources"]) <= 5
            or not all(safe_source(s) for s in entry["sources"])
        ):
            return False
        return bool(date.fromisoformat(entry["reviewed_on"]).isoformat() == entry["reviewed_on"])
    except (TypeError, ValueError, KeyError):
        return False


def parse_playbooks(value: object) -> PlaybookEdition | None:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "playbooks_version",
        "published_on",
        "entries",
    }:
        return None
    try:
        if (
            value["schema"] != SCHEMA
            or not isinstance(value["playbooks_version"], str)
            or not VERSION.fullmatch(value["playbooks_version"])
            or date.fromisoformat(value["published_on"]).isoformat() != value["published_on"]
            or not isinstance(value["entries"], list)
            or len(value["entries"]) > 100
            or not all(valid_entry(entry) for entry in value["entries"])
        ):
            return None
        entries = value["entries"]
        if len({entry["id"] for entry in entries}) != len(entries) or any(
            entry["reviewed_on"] > value["published_on"] for entry in entries
        ):
            return None
        return PlaybookEdition(value["playbooks_version"], value["published_on"], tuple(entries))
    except (TypeError, ValueError):
        return None


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def compatible(
    entry: dict[str, Any], tools: tuple[str, ...], versions: dict[str, str | None]
) -> bool:
    if entry["min_client_recipe_version"] > CLIENT_RECIPE_VERSION or not tools:
        return False
    for tool in tools:
        declared = next((h for h in entry["harnesses"] if h["tool"] == tool), None)
        if declared is None:
            return False
        low, high = declared["min_version"], declared["max_version"]
        if low is None and high is None:
            continue  # Explicitly version-independent guidance.
        installed = versions.get(tool)
        if not installed or not SEMVER.fullmatch(installed):
            return False  # Prerelease/version ambiguity is not compatibility evidence.
        current = version_tuple(installed)
        if (low and current < version_tuple(low)) or (high and current > version_tuple(high)):
            return False
    return True
