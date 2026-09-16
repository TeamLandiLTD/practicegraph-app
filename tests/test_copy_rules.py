"""The copy rules (docs/COPY_RULES.md), enforced.

Owner call 2026-08-23: explain metrics, do not post them. The card face
carries a plain title and a headline sentence; method, source and caveat
live behind the ⓘ. These caps keep the voice from drifting back: titles
are names, hints are one breath, and the glyph-and-jargon delta phrasing
("▲ 61 vs your prior 28 days") never returns to a face string.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).parents[1] / "ui" / "src" / "App.jsx"
SRC = Path(__file__).parents[1] / "src" / "practicegraph"

TITLE_MAX = 32
HINT_MAX = 220
HEADLINE_MAX = 160

# Phrases that belong to the old voice. They may appear in comments and in
# the ⓘ, never in a rendered title, meta line or headline.
BANNED_ON_FACE = (
    "vs your prior",
    "not a measurement",
    "association ·",
    "guidance ·",
)


def _balanced(source: str, start: int) -> str:
    """The text inside the brace expression starting at source[start] == '{'."""
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1:index]
    return source[start + 1:]


def _literal_text(expression: str) -> str:
    """Concatenate the string literals of a JSX prop expression."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', expression)
    return "".join(parts) if parts else ""


def _jsx_props(name: str) -> list[tuple[int, str]]:
    source = APP.read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for match in re.finditer(rf"\b{name}=", source):
        at = match.end()
        line = source.count("\n", 0, at) + 1
        if source[at] == "{":
            found.append((line, _literal_text(_balanced(source, at))))
        elif source[at] == '"':
            end = source.index('"', at + 1)
            found.append((line, source[at + 1:end]))
    return found


def test_card_titles_are_names_not_sentences() -> None:
    # Native title attributes are tooltips, not card headings. Inspect Head's
    # literal title only; concatenating both arms of a ternary invents copy.
    source = APP.read_text(encoding="utf-8")
    long = [(source.count("\n", 0, match.start()) + 1, match[1])
            for match in re.finditer(r'<Head\b[^>]*\btitle="([^"]+)"', source)
            if len(match[1]) > TITLE_MAX]
    assert long == [], long


def test_hints_are_one_breath() -> None:
    long = [(line, len(text)) for line, text in _jsx_props("hint")
            if len(text) > HINT_MAX]
    assert long == [], long


def test_the_old_voice_is_off_the_card_face() -> None:
    source = APP.read_text(encoding="utf-8")
    offenders: list[tuple[int, str]] = []
    for line, text in _jsx_props("meta") + _jsx_props("title"):
        for phrase in BANNED_ON_FACE:
            if phrase in text:
                offenders.append((line, phrase))
    # Delta phrasing is rendered from data, so scan the JSX render code for
    # the literal pattern rather than a prop.
    for number, line in enumerate(source.splitlines(), 1):
        if "vs your prior" in line and "//" not in line.split("vs your prior")[0]:
            offenders.append((number, "vs your prior"))
    assert offenders == [], offenders


# Sentences that narrated the interface or repeated a caveat on the face
# (cut 2026-09-10). They may return only behind the ⓘ - never as a literal
# in a rendered component, never in the CLI's replies.
GONE_FROM_FACES = (
    "Subscription amounts are comparisons",
    "Select a bar to inspect",
    "This period applies to",
    "Skills you already have are marked",
    "That is the overview",
    "not measured yet",
    "states no description",
    "Choose 10,000 hours",
    "why these:",
    "Catalog source",
    "edition dated",
    "Editorial starting point",
)
GONE_FROM_CLI = ("enforced by contract tests", "recognition band")


def test_the_narration_stays_cut() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted(APP.parent.glob("*.jsx")):
        if path.name.endswith(".test.jsx"):
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in GONE_FROM_FACES:
            if phrase in text:
                offenders.append((path.name, phrase))
    cli = (SRC / "cli.py").read_text(encoding="utf-8")
    for phrase in GONE_FROM_CLI:
        if phrase in cli:
            offenders.append(("cli.py", phrase))
    assert offenders == [], offenders


def _catalog_entries() -> list[tuple[str, str, str]]:
    """(module, key, value) for every COPY catalog in the engine."""
    import importlib

    entries: list[tuple[str, str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        names = re.findall(r"^([A-Z_]+_COPY): dict\[str, str\] = \{", text, re.M)
        if not names:
            continue
        module_name = "practicegraph." + ".".join(
            path.relative_to(SRC).with_suffix("").parts
        )
        module = importlib.import_module(module_name)
        for name in names:
            catalog = getattr(module, name)
            for key, value in catalog.items():
                entries.append((f"{module_name}.{name}", key, value))
    return entries


def test_engine_titles_and_hints_respect_the_caps() -> None:
    offenders = []
    for where, key, value in _catalog_entries():
        if key in ("title",) and len(value) > TITLE_MAX:
            offenders.append((where, key, len(value)))
        if key in ("hint",) and len(value) > HINT_MAX:
            offenders.append((where, key, len(value)))
        if key in ("eyebrow",):
            for phrase in BANNED_ON_FACE:
                if phrase in value:
                    offenders.append((where, key, phrase))
    assert offenders == [], offenders
