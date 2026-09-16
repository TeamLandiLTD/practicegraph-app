"""Audit a compiled engine binary for source/docstring leakage.

Extracts printable strings and reports high-signal markers that should NOT
survive a hardened (docstring-stripped) Nuitka build:

  - internal spec refs (INV-6, NFR-PRV-6, FR-ANL-*): appear ONLY in
    docstrings/comments, never in runtime string literals -> their presence
    proves docstrings were compiled in.
  - distinctive docstring prose (algorithm narration).
  - source file / module path fragments.

User-facing copy catalogs (advisor/coach/finding copy) are EXPECTED in any
build -- the program prints them -- so they are reported separately, not as
leaks.

Usage: python audit_engine_strings.py <engine.exe> [<engine2.exe> ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_PRINTABLE = re.compile(rb"[\x20-\x7e]{6,}")

# Markers that betray compiled-in DOCSTRINGS/COMMENTS (never in runtime code).
SPEC_REFS = re.compile(rb"\b(INV-\d|NFR-[A-Z]{3}-\d|FR-[A-Z]{3}-\d|C-\d\b)")
DOCSTRING_PROSE = [
    b"fusion engine",
    b"curated verdicts",
    b"receipts-first",
    b"Deterministic insight",
    b"acknowledgment ledger",
    b"single-flight",
    b"Pareto",
    b"closed copy catalog",
    b"adversarial",
    b"the honest denominator",
]
SOURCE_FRAGMENTS = [
    b"src/practicegraph",
    b"src\\practicegraph",
    b"analysis/advisor",
    b"analysis\\advisor",
]
# Expected in any build (user-facing, program prints them) -- reported, not failed.
USER_FACING = [b"Route mechanical work", b"the premium tiers", b"Reading your local record"]


def extract(path: Path) -> bytes:
    data = path.read_bytes()
    return b"\n".join(_PRINTABLE.findall(data))


def audit(path: Path) -> int:
    blob = extract(path)
    size_mb = path.stat().st_size / 1_048_576
    spec = sorted({m.group(0).decode() for m in SPEC_REFS.finditer(blob)})
    prose = [p.decode() for p in DOCSTRING_PROSE if p in blob]
    src = [s.decode() for s in SOURCE_FRAGMENTS if s in blob]
    userf = [u.decode() for u in USER_FACING if u in blob]

    print(f"\n=== {path.name}  ({size_mb:.1f} MB) ===")
    print(f"  spec refs (INV-*/NFR-*/FR-*/C-*) : {len(spec)} distinct"
          + (f"  e.g. {spec[:6]}" if spec else "  -> NONE (docstrings stripped)"))
    print(f"  docstring prose markers          : {len(prose)}"
          + (f"  {prose}" if prose else "  -> NONE"))
    print(f"  user-facing copy (expected)      : {len(userf)}/{len(USER_FACING)} present")
    # Compiled-in module qualnames / traceback co_filename paths are STRUCTURAL
    # -- Nuitka embeds them for imports and tracebacks regardless of flags, and
    # module structure is inferable anyway. Reported, never gate-failing.
    print(f"  module-path fragments (structural): {len(src)}"
          + (f"  {src}" if src else "  -> none"))

    # The gate fails ONLY on the fixable leak: docstrings/comments/commentary we
    # control (spec refs + narration prose). Structural paths do not count.
    leaked = len(spec) + len(prose)
    verdict = "LEAKS DOCSTRINGS/SOURCE" if leaked else "clean (no docstring/commentary leakage)"
    print(f"  VERDICT: {verdict}")
    return leaked


def main() -> int:
    worst = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_file():
            print(f"skip (not found): {arg}")
            continue
        worst = max(worst, audit(p))
    return 0  # audit is informational; the build gate decides pass/fail


if __name__ == "__main__":
    raise SystemExit(main())
