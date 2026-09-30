"""The model catalog: available models and reasoning efforts, served.

The benchmark artifact (model_intelligence) carries MEASUREMENTS and stays
empty until real Artificial Analysis numbers are minted. This catalog is
the other half the models page needs — what is AVAILABLE on each harness
and what the sensible floor is: the model ladder (strongest / everyday /
fast, with what each is for), the reasoning-effort dial's levels, and
which level is the recommended pinned default. All of it is teaching
content, marked guidance on the page, never a measurement.

Served like docs/skills/news (FR-EMT-4): published JSON that TeamLandi
organizes centrally, a closed parser that refuses ANY deviation, and an
explicit empty state before the first valid download. The
page marks the person's own pins against this catalog — what is currently
set versus the catalog's recommended floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEMA = "practicegraph.model-catalog/1"
CATALOG_FILE_NAME = "model-catalog.json"

_ROOT_KEYS = frozenset({"schema", "catalog_version", "closing", "tools"})
_TOOL_KEYS = frozenset(
    {"tool", "models", "efforts", "practices", "practices_source", "switch"}
)
_MODEL_KEYS = frozenset({"model", "role", "when", "price_note"})
# "pin" is optional per model row: the exact value the tool's own config
# takes for this model ("opus" for Claude Code's settings.json, the model
# id itself for Codex). Absent = the display name doubles as the value.
_MODEL_KEYS_OPTIONAL = _MODEL_KEYS | {"pin"}
_EFFORT_KEYS = frozenset({"level", "when", "tone", "recommended"})
_PRACTICE_KEYS = frozenset({"title", "body"})
_TOOLS = ("claude_code", "codex")
_ROLES = ("strongest", "everyday", "fast")
_TONES = frozenset({"good", "neutral", "costly"})
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,500}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .+()_-]{0,79}\Z")
_LEVEL = re.compile(r"[a-z][a-z-]{0,15}\Z")
_MAX_MODELS = 6
_MAX_EFFORTS = 6
_MAX_PRACTICES = 6
_MAX_SWITCH_STEPS = 6


@dataclass(frozen=True)
class CatalogModel:
    model: str
    role: str        # strongest | everyday | fast
    when: str
    price_note: str
    # The exact value the tool's own config takes for this model, or None
    # when the catalog does not say. None = the one-click pin stays off for
    # this row - a guess written into someone's config is worse than no
    # button.
    pin: str | None


@dataclass(frozen=True)
class CatalogEffort:
    level: str
    when: str
    tone: str        # good | neutral | costly
    recommended: bool


@dataclass(frozen=True)
class CatalogPractice:
    title: str
    body: str


@dataclass(frozen=True)
class CatalogTool:
    tool: str
    models: tuple[CatalogModel, ...]
    efforts: tuple[CatalogEffort, ...]
    # The best-practice teaching the owner's coach report established: a
    # few titled facts (with their stated source) and the switch steps.
    practices: tuple[CatalogPractice, ...]
    practices_source: str    # may be empty; shown under the fold when set
    switch: tuple[str, ...]  # how to change the model/effort, as steps


@dataclass(frozen=True)
class ModelCatalog:
    catalog_version: str
    closing: str             # the one-line rule the section ends on
    tools: tuple[CatalogTool, ...]


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or _TEXT.fullmatch(value) is None:
        return None
    return value if len(value) <= limit else None


def parse_model_catalog(raw: object) -> ModelCatalog | None:
    """Parse a closed catalog document, returning None on any invalid input."""
    if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS:
        return None
    if raw["schema"] != SCHEMA:
        return None
    version = raw["catalog_version"]
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        return None
    closing = _text(raw["closing"], 200)
    if closing is None:
        return None
    raw_tools = raw["tools"]
    if not isinstance(raw_tools, list) or len(raw_tools) != len(_TOOLS):
        return None
    tools: list[CatalogTool] = []
    for raw_tool, expected in zip(raw_tools, _TOOLS, strict=True):
        if not isinstance(raw_tool, dict) or set(raw_tool) != _TOOL_KEYS:
            return None
        if raw_tool["tool"] != expected:
            return None
        raw_models = raw_tool["models"]
        raw_efforts = raw_tool["efforts"]
        if not isinstance(raw_models, list) or not 1 <= len(raw_models) <= _MAX_MODELS:
            return None
        if not isinstance(raw_efforts, list) or len(raw_efforts) > _MAX_EFFORTS:
            return None
        models: list[CatalogModel] = []
        seen_roles: set[str] = set()
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                return None
            keys = set(raw_model)
            if not keys >= _MODEL_KEYS or not keys <= _MODEL_KEYS_OPTIONAL:
                return None
            model = raw_model["model"]
            role = raw_model["role"]
            when = _text(raw_model["when"], 200)
            price_note = _text(raw_model["price_note"], 80)
            pin = raw_model.get("pin")
            if pin is not None and (
                not isinstance(pin, str) or _MODEL.fullmatch(pin) is None
            ):
                return None
            if (
                not isinstance(model, str)
                or _MODEL.fullmatch(model) is None
                or role not in _ROLES
                or role in seen_roles
                or when is None
                or price_note is None
            ):
                return None
            seen_roles.add(str(role))
            models.append(
                CatalogModel(model, str(role), when, price_note, pin)
            )
        efforts: list[CatalogEffort] = []
        seen_levels: set[str] = set()
        recommended_count = 0
        for raw_effort in raw_efforts:
            if not isinstance(raw_effort, dict) or set(raw_effort) != _EFFORT_KEYS:
                return None
            level = raw_effort["level"]
            when = _text(raw_effort["when"], 200)
            tone = raw_effort["tone"]
            recommended = raw_effort["recommended"]
            if (
                not isinstance(level, str)
                or _LEVEL.fullmatch(level) is None
                or level in seen_levels
                or when is None
                or tone not in _TONES
                or type(recommended) is not bool
            ):
                return None
            seen_levels.add(level)
            recommended_count += 1 if recommended else 0
            efforts.append(CatalogEffort(level, when, str(tone), recommended))
        # A dial with levels must point at exactly one recommended floor;
        # a harness with no pinnable dial ships an empty efforts list.
        if efforts and recommended_count != 1:
            return None
        raw_practices = raw_tool["practices"]
        raw_source = raw_tool["practices_source"]
        raw_switch = raw_tool["switch"]
        if not isinstance(raw_practices, list) or len(raw_practices) > _MAX_PRACTICES:
            return None
        if not isinstance(raw_switch, list) or len(raw_switch) > _MAX_SWITCH_STEPS:
            return None
        source = "" if raw_source == "" else _text(raw_source, 200)
        if source is None:
            return None
        practices: list[CatalogPractice] = []
        for raw_practice in raw_practices:
            if (
                not isinstance(raw_practice, dict)
                or set(raw_practice) != _PRACTICE_KEYS
            ):
                return None
            title = _text(raw_practice["title"], 90)
            body = _text(raw_practice["body"], 500)
            if title is None or body is None:
                return None
            practices.append(CatalogPractice(title, body))
        steps: list[str] = []
        for raw_step in raw_switch:
            step = _text(raw_step, 160)
            if step is None:
                return None
            steps.append(step)
        tools.append(CatalogTool(
            expected, tuple(models), tuple(efforts),
            tuple(practices), source, tuple(steps),
        ))
    return ModelCatalog(version, closing, tuple(tools))


EMPTY_CATALOG = ModelCatalog("unavailable", "", ())
