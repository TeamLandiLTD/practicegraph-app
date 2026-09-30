"""Optional provider wording with a conservative acceptance boundary.

The deterministic composer owns all observations. Number equality cannot
verify that a paraphrase preserves meaning: "rose 10%" and "fell 10%" have
identical numbers. Until reviewed alternatives exist, only whitespace changes
to the exact source sentence are accepted, including from historical caches.

When enabled explicitly, Claude Code or Codex may send the supplied dashboard
sentences to its provider and incur provider charges. It is off by default.
No raw logs are supplied by this module. A rejected response falls back to the
original sentence; this path must never be described as verified free paraphrase.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from practicegraph.privacy import leak_findings, lexicon_violations

# A provider runner: (argv, prompt, timeout_s) -> raw model text. Injectable so
# the pipeline is testable without spawning a real CLI.
Runner = Callable[[tuple[str, ...], str, float], str]

STYLE_SCHEMA = "practicegraph.reflection_style/1"
STYLE_META_KEY = "reflection_style_cache"
# The coach cues restyle through the same pipeline but cache separately so the
# two never collide (each keyed by a fingerprint of its own source text).
COACH_STYLE_META_KEY = "coach_style_cache"

# A styled line may not wander far from the source in length (a reading, not
# an essay) and must not collapse to nothing.
STYLE_MIN_CHARS = 24
STYLE_MAX_CHARS = 320

# The numeric shapes a sentence can carry — the same family the app highlights
# in mono: bare integers with optional thousands separators, percents, and
# "65h 15m"-style durations. A rewrite may reuse these tokens and reorder the
# words; it may not introduce a number the source never had, nor drop one.
_NUM_TOKEN = re.compile(r"\d[\d,]*(?::\d{2})?(?:h\s*\d{1,2}m)?%?|\d{1,2}:\d{2}")


@dataclass(frozen=True, slots=True)
class StyleProvider:
    """A local CLI that reads a prompt on stdin and prints text on stdout.

    Closed set — the endpoint only ever shells out to a provider named here,
    never an arbitrary command from config."""

    key: str
    argv: tuple[str, ...]


# The user's own installed agents. Both read a prompt from stdin in
# non-interactive mode and emit plain text — exactly what a stylist needs.
STYLE_PROVIDERS: dict[str, StyleProvider] = {
    "claude": StyleProvider("claude", ("claude", "-p")),
    "codex": StyleProvider("codex", ("codex", "exec", "-")),
}


def _number_sequence(text: str) -> list[str]:
    """The statistic tokens in a sentence, in reading order, normalized so
    '65h 15m' and '65h15m' compare equal (spacing never decides acceptance).

    Order is preserved on purpose: a faithful rewrite may reorder words freely
    but must tell the same numbers in the same sequence. Requiring the exact
    sequence — not just the same multiset — is what blocks a transposition
    ('28 of 24 days' from '24 of 28 days'), the one way equal-membership math
    could still let a false statement through."""
    return [token.replace(" ", "") for token in _NUM_TOKEN.findall(text) if token]


def facts_fingerprint(reflections: list[dict[str, str]]) -> str:
    """A stable id for exactly this set of composed sentences. Keying the
    cache on the source text means the same facts always map to one styled
    set (determinism), and any change in what the composer said invalidates
    the rewrite automatically."""
    basis = json.dumps([(line["id"], line["text"]) for line in reflections], sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _prompt(reflections: list[dict[str, str]]) -> str:
    """Assemble the styling instruction. The model is told plainly: keep the
    meaning and every number, change only the words, one line out per line in.
    Tone guidance mirrors PRINCIPLES.md so the rewrite stays observational."""
    lines = "\n".join(
        f"{index + 1}. [{line['tone']}] {line['text']}" for index, line in enumerate(reflections)
    )
    return (
        "You are rewriting short reflective sentences shown at the top of a "
        "personal, private dashboard about someone's own AI-coding habits. "
        "Rewrite each numbered line below so it says the SAME thing in fresh, "
        "natural, human wording.\n\n"
        "Hard rules:\n"
        "- Keep every number exactly as written (counts, percents, durations "
        "like '65h 15m'). Do not add, drop, or change any number.\n"
        "- Keep the meaning and the same subject. A 'watch' line stays an "
        "observation; a 'question' line stays a gentle question; a 'steady' "
        "line stays encouraging.\n"
        "- Calm, plain, second person. No praise inflation, no alarm, no "
        "clinical or medical words, no diagnosis, no emojis, no metaphors "
        "about brains or addiction.\n"
        "- One rewritten line per input line, same order. Output ONLY the "
        "lines, numbered the same way, nothing else.\n\n"
        f"{lines}\n"
    )


def _parse_numbered(raw: str, count: int) -> list[str] | None:
    """Pull `count` lines back out of the model's numbered reply. Tolerant of
    '1.', '1)', stray blank lines and surrounding chatter; strict about
    getting exactly the right number of non-empty lines. Strips a leading
    tone tag the model may echo back from the prompt ('[watch] ...') so the
    label never leaks into the rendered sentence."""
    out: list[str] = []
    for line in raw.splitlines():
        match = re.match(r"\s*(\d{1,2})[.)]\s+(.*\S)\s*$", line)
        if match:
            text = re.sub(r"^\[(?:watch|question|steady)\]\s*", "", match.group(2))
            out.append(text.strip())
    return out if len(out) == count else None


# Belt and braces on the encoding fix above. The subprocess is now read as
# UTF-8, so this should never fire - but a rewrite that reaches the person
# with "â€"" where an em-dash belongs is indistinguishable from the product
# being broken, and it persists in the style cache until the facts change.
# Cheaper to refuse the line and keep the deterministic one.
_MOJIBAKE_MARKERS = ("â€", "Ã¢", "â€™", "Ã©", "�")


def _mojibake(text: str) -> bool:
    """True when the text carries the signature of UTF-8 bytes decoded as
    cp1252/latin-1 (or a replacement character from a failed decode)."""
    return any(marker in text for marker in _MOJIBAKE_MARKERS)


def _accept(source: str, styled: str) -> str | None:
    """Return the styled sentence iff it is a faithful, clean restyle of the
    source; otherwise None (caller keeps the deterministic sentence).

    Free paraphrases cannot be proved equivalent by matching numbers: 'rose'
    and 'fell' carry the same numbers and opposite claims. Until the composer
    supplies explicitly reviewed alternatives, accept only whitespace changes.
    Legacy cached paraphrases pass this same check and fall back to source."""
    styled = styled.strip().strip('"').strip()
    if not (STYLE_MIN_CHARS <= len(styled) <= STYLE_MAX_CHARS):
        return None
    # Exact number sequence: same statistics, same order. Catches added,
    # dropped, duplicated, AND transposed numbers — a model cannot restate the
    # data, only rephrase around it.
    if _number_sequence(styled) != _number_sequence(source):
        return None
    if lexicon_violations(styled) or leak_findings(styled):
        return None
    if _mojibake(styled):
        return None
    if " ".join(styled.split()) != " ".join(source.split()):
        return None
    return styled


def restyle_reflections(
    reflections: list[dict[str, str]],
    provider: StyleProvider,
    runner: Runner | None = None,
    timeout_s: float = 45.0,
) -> list[dict[str, str]]:
    """Rewrite the wording of each reflection via `provider`, keeping the
    composer's `id`/`tone`/numbers and falling back per-line to the original
    on any failure. `runner(argv, prompt, timeout_s) -> str` is injectable so
    the whole pipeline is testable without spawning a real model.

    Total failure (no CLI, timeout, garbled reply) degrades to the input
    unchanged — the band never breaks because a stylist was unavailable."""
    if not reflections:
        return reflections
    call = runner if runner is not None else _run_cli
    try:
        raw = call(provider.argv, _prompt(reflections), timeout_s)
    except Exception:
        return reflections
    parsed = _parse_numbered(raw or "", len(reflections))
    if parsed is None:
        return reflections
    styled: list[dict[str, str]] = []
    for source_line, candidate in zip(reflections, parsed, strict=True):
        accepted = _accept(source_line["text"], candidate)
        styled.append({**source_line, "text": accepted} if accepted else source_line)
    return styled


def _run_cli(argv: tuple[str, ...], prompt: str, timeout_s: float) -> str:
    """Shell out to the local provider CLI: prompt on stdin, text on stdout.
    Never raises for the caller's benefit — any failure returns ''.

    The executable is resolved to an ABSOLUTE path first (shutil.which). Passing
    an absolute path makes the OS ignore the working directory and PATH search
    when launching, so a binary planted in the process CWD cannot be picked up;
    if the provider is not on PATH at all, styling is skipped and the
    deterministic text stands. (The service never reaches here — restyle is
    refused under LocalSystem; see agent._restyle_items.) No argument carries
    user content beyond the prompt, which is passed on stdin."""
    executable = shutil.which(argv[0])
    if not executable:
        return ""
    try:
        completed = subprocess.run(
            [executable, *argv[1:]],
            input=prompt,
            capture_output=True,
            text=True,
            # UTF-8 both ways, explicitly. `text=True` alone uses the platform
            # default, which on Windows is cp1252: the CLI emits UTF-8, Python
            # decoded it as cp1252, and every em-dash became "â€"" - stored in
            # the style cache and rendered to the person for as long as the
            # cache held. Seen live on 2026-07-27, and only then, because the
            # LocalSystem service used to skip restyle entirely; moving to a
            # per-user install ran this path for the first time.
            #
            # `errors="replace"` so a stray byte degrades one character rather
            # than raising and silently disabling the whole feature.
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except Exception:
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def cached_styled(
    reflections: list[dict[str, str]], cache_raw: str | None
) -> list[dict[str, str]] | None:
    """The styled band for exactly today's facts, if a valid same-fingerprint
    cache exists AND still passes validation (copy rules can tighten between
    runs; a cached line that would no longer be accepted is dropped to its
    source). Returns None when there is no usable cache — the caller then
    shows the deterministic band (and may schedule a refresh)."""
    if not cache_raw:
        return None
    try:
        cache = json.loads(cache_raw)
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(cache, dict)
        or cache.get("schema") != STYLE_SCHEMA
        or cache.get("fingerprint") != facts_fingerprint(reflections)
    ):
        return None
    styled_map = cache.get("styled")
    if not isinstance(styled_map, dict):
        return None
    out: list[dict[str, str]] = []
    for line in reflections:
        candidate = styled_map.get(line["id"])
        accepted = _accept(line["text"], candidate) if isinstance(candidate, str) else None
        out.append({**line, "text": accepted} if accepted else line)
    return out


def build_style_cache(
    reflections: list[dict[str, str]], styled: list[dict[str, str]], now_iso: str
) -> dict[str, object]:
    """The persisted cache doc: schema, the facts fingerprint it is valid for,
    a timestamp, and the accepted rewrites by reflection id. Only lines that
    actually changed are stored; the rest render from the composer."""
    changed = {
        styled_line["id"]: styled_line["text"]
        for source_line, styled_line in zip(reflections, styled, strict=True)
        if styled_line["text"] != source_line["text"]
    }
    return {
        "schema": STYLE_SCHEMA,
        "fingerprint": facts_fingerprint(reflections),
        "generated_at": now_iso,
        "styled": changed,
    }
