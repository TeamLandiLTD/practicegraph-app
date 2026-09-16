from __future__ import annotations

import re
from pathlib import Path

from practicegraph.privacy import leak_findings, lexicon_violations

APP_SOURCE = Path(__file__).parents[1] / "ui" / "src" / "App.jsx"
UI_SRC = Path(__file__).parents[1] / "ui" / "src"
GIT_ATTRIBUTES = Path(__file__).parents[1] / ".gitattributes"


def test_client_copy_passes_the_same_lexicon_gate_as_engine_copy() -> None:
    """The gap this closes: every Python copy catalog is scanned for the
    clinical/pathology lexicon (FR-FOC-8) and for leaked paths and keys, but
    nothing ever read the client. Hundreds of lines of user-facing prose live
    as JSX literals — card hints, the DIM_EXPLAIN and STAT_EXPLAIN tables, the
    teaching copy — and reached the page unscanned. The words a person reads
    are the same words whichever file they were typed in.

    Scans quoted strings rather than the whole source so that identifiers and
    CSS class names are not mistaken for prose (`.brainstorm` would otherwise
    trip a lexicon term that only matters in a sentence)."""
    literal = re.compile(r'"([^"\\\n]{12,})"' r"|'([^'\\\n]{12,})'")
    for path in sorted(UI_SRC.glob("*.js*")):
        if path.name.endswith(".test.jsx"):
            continue  # fixtures, not copy the page renders
        source = path.read_text(encoding="utf-8")
        for match in literal.finditer(source):
            text = match.group(1) or match.group(2)
            if text.startswith(("http", "/api/", "./", "../")):
                continue  # endpoints and imports, checked elsewhere
            assert lexicon_violations(text) == [], f"{path.name}: {text}"
            assert leak_findings(text) == [], f"{path.name}: {text}"


def test_html_entrypoints_are_normalized_to_lf() -> None:
    attributes = GIT_ATTRIBUTES.read_text(encoding="utf-8").splitlines()

    assert "ui/index.html text eol=lf" in attributes
    assert "webui/index.html text eol=lf" in attributes


def test_dashboard_does_not_render_weekly_reading() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "<WeeklyReading view={view} />" not in source


def test_navigation_keeps_stateful_workflows_mounted_once() -> None:
    """Direct category navigation and saved links are exercised in
    Reading.test.jsx; shared controllers must keep their single owner here."""
    source = APP_SOURCE.read_text(encoding="utf-8")
    dashboard = source.split("export function Dashboard")[1].split("export default function App")[0]
    for component in ("PracticeHistory", "LearningPaths", "CapabilityCoach"):
        assert dashboard.count(f"<{component} ") == 1, component
    # Improve setup left the app on 2026-09-11 (owner call); its engine and
    # module stay, nothing mounts them.
    assert "<SetupImprovements " not in dashboard
    lead = dashboard.split("  return (")[0]
    for hook in ("usePracticeTime()", "useLearningPaths()"):
        assert hook in lead
    assert "useSetupImprovements(" not in lead
    assert "<InstallNotices view={view} />" in dashboard
    assert "<UpdateBanner view={view} />" in dashboard


def test_retired_personal_kpis_have_no_render_path() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")
    for retired in ("<Conditioning", "<PracticeStats", "<StateOfYou", "<WeekSpark"):
        assert retired not in source
    assert source.count("<WorkPatterns view={view}") == 1
    assert "<QuietHours view={view}" not in source
    assert "<VerificationCard view={view}" not in source


def test_skill_shelf_has_collapsed_preview_and_safe_source_link() -> None:
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert '<details className="skpreview">' in source
    assert "Preview instructions" in source
    assert 'target="_blank" rel="noreferrer"' in source
    # Two deep links, one per tool: install_command is the Codex flow
    # ($skill-installer), claude_install_command the Claude Code fetch.
    assert "Copy for Codex" in source
    assert "Copy for Claude Code" in source
    assert "Paste it into a Codex task" in source
    assert "Paste it into a Claude Code session" in source


def test_no_score_about_the_person_reaches_the_page() -> None:
    """The plan's do-not-build list, enforced (2026-07-26).

    §7: "Do not build ... any score that points at the person." The engine
    still scores six dimensions 0-100 and still needs to, because those scores
    RANK which habit deserves the one recommendation the page is allowed to
    make — a routing decision. What changed is that none of them is rendered.
    An overall grade is the purest form of the thing the list forbids: it
    invites a reading of the person rather than of the work, which is exactly
    where Kluger & DeNisi found feedback does its harm.

    What the person sees instead is the measurement and which way it moved
    against their own prior window."""
    source = APP_SOURCE.read_text(encoding="utf-8")

    # The composite grade is gone from the header...
    assert "overall <b>{perf.overall}</b>" not in source
    assert "perf.delta_vs_prior >= 0 ?" not in source
    # ...and the per-dimension mark is gone from every row (the whole
    # six-dimension card left in the page diet, 2026-08-13).
    assert "{r.score}<small>" not in source
    assert 'className={`score ${dir}`}' not in source
    # Behavioral tests in Reliance.test.jsx check neutral deltas with explicit units.
    assert "deltaPhrase(" not in source
    assert "percentage points" in source


def _machine_guarded_regions(source: str) -> list[tuple[int, int]]:
    """Line spans inside `<?ifdef $(var.MachineInstall) ?>` blocks."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    depth_machine = 0
    for number, line in enumerate(source.splitlines(), 1):
        text = line.strip()
        if text.startswith("<?ifdef $(var.MachineInstall)"):
            stack.append(number)
            depth_machine += 1
        elif text.startswith("<?ifdef") or text.startswith("<?ifndef"):
            stack.append(-1)
        elif text.startswith("<?endif"):
            opened = stack.pop() if stack else -1
            if opened > 0:
                spans.append((opened, number))
                depth_machine -= 1
    return spans


def test_a_per_user_install_never_writes_a_machine_hive() -> None:
    r"""The bug a clean-VM install found on 2026-07-27, pinned.

    The protocol handler wrote `HKLM\SOFTWARE\Classes\practicegraph`. In the
    per-machine package that was fine; in the per-user one it needs
    administrator rights, so setup died mid-install with "Could not write value
    URL Protocol to key \SOFTWARE\Classes\practicegraph". `EventSource` was
    the same bug one component later, and would have surfaced the moment the
    first was fixed.

    Every HKLM write must therefore live inside a `MachineInstall` guard. HKMU
    is the correct root for anything both flavours need: it resolves to HKCU
    per-user and HKLM per-machine.

    Bench testing cannot catch this - the MSI builds, validates and passes the
    payload gate either way. Only an actual install on a machine where you are
    not admin does, so the check lives here instead."""
    source = (
        Path(__file__).parents[1] / "packaging" / "msi" / "Package.wxs"
    ).read_text(encoding="utf-8")
    guarded = _machine_guarded_regions(source)

    unguarded: list[tuple[int, str]] = []
    for number, line in enumerate(source.splitlines(), 1):
        if 'Root="HKLM"' not in line:
            continue
        if any(start < number < end for start, end in guarded):
            continue
        unguarded.append((number, line.strip()))

    assert not unguarded, (
        "HKLM outside a MachineInstall guard - a per-user install cannot write "
        f"these and setup will fail mid-way: {unguarded}"
    )
    # And the shared components use the root that resolves per flavour.
    assert r'Root="HKMU" Key="SOFTWARE\Classes\practicegraph"' in source
    assert (
        r'Root="HKMU" Key="SOFTWARE\Microsoft\Windows\CurrentVersion\Run"'
        in source
    )


def test_the_machine_guard_parser_actually_finds_the_guards() -> None:
    """A scanner that silently matches nothing would pass forever. Prove it
    sees the real guards before trusting what it says about HKLM."""
    source = (
        Path(__file__).parents[1] / "packaging" / "msi" / "Package.wxs"
    ).read_text(encoding="utf-8")
    spans = _machine_guarded_regions(source)
    assert len(spans) >= 4, spans
    # The service is the canonical machine-only component; it must be inside one.
    service_line = next(
        number
        for number, line in enumerate(source.splitlines(), 1)
        if "<ServiceInstall Id=" in line
    )
    assert any(start < service_line < end for start, end in spans)
