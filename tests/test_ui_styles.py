from __future__ import annotations

import re
from pathlib import Path

STYLESHEET = Path(__file__).parents[1] / "ui" / "src" / "styles.css"


def _rule_for(selector: str) -> str:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", stylesheet)
    assert match is not None, f"missing CSS rule for {selector}"
    return match.group(1)


def test_machine_noticed_is_a_gradient_card() -> None:
    rule = _rule_for(".noticed")

    assert "background: linear-gradient(135deg, var(--teal-50), var(--card))" in rule
    assert "border: 1px solid var(--teal-100)" in rule
    assert "border-radius: 18px" in rule
    assert "box-shadow: var(--elev-1)" in rule


def test_skill_install_controls_keep_commands_selectable() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")

    assert ".skpreview" in css
    assert ".skactions" in css
    assert ".skfailure" in css
    assert ".skcommand" in css
    assert "user-select: text" in css


def test_layout_cards_can_shrink_to_narrow_viewports() -> None:
    rule = _rule_for(".layout > .card")

    assert "min-width: 0" in rule


def test_conditioning_readings_are_stacked_horizontal_rows() -> None:
    strip = _rule_for(".condstrip")
    row = _rule_for(".cond")

    assert "grid-template-columns: 1fr" in strip
    assert "grid-template-columns: minmax(110px, 0.7fr) minmax(0, 1.6fr)" in row
    assert 'grid-template-areas: "head read" "value track"' in row


def test_narrow_dashboard_rules_prevent_intrinsic_overflow() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    trajectories = stylesheet.index("/* Trajectories */")
    final_narrow_dimensions = stylesheet.rindex("@media (max-width: 560px)")

    assert final_narrow_dimensions > trajectories
    assert ".tabs { width: 100%; }" in stylesheet


def test_conditioning_explanations_expand_inline() -> None:
    explanation = _rule_for(".condmore")

    assert "position: absolute" not in explanation
    assert "grid-column: 1 / -1" in explanation
    assert "max-height: 0" in explanation
    assert "overflow: hidden" in explanation


def test_news_rows_are_headline_only_disclosures() -> None:
    entry = _rule_for(".newsentry")
    title = _rule_for(".newstitlebutton")
    focus = _rule_for(".newstitlebutton:focus-visible")
    reveal = _rule_for(".newsreveal")
    expanded = _rule_for(".newsentry.is-revealed .newsreveal")

    assert "border-top: 1px solid var(--hair)" in entry
    assert "width: 100%" in title
    assert "background: transparent" in title
    assert "text-align: left" in title
    assert "outline: 2px solid var(--teal-500)" in focus
    assert "display: grid" in reveal
    assert "grid-template-rows: 0fr" in reveal
    assert "grid-template-rows: 1fr" in expanded


def test_news_teaser_is_one_line_until_revealed() -> None:
    teaser = _rule_for(".newsteaser")
    collapsed = _rule_for(".newsentry:not(.is-revealed) .newsteaser")
    revealed = _rule_for(".newsentry.is-revealed .newsteaser")

    assert "display: block" in teaser
    assert "font-size: 13px" in teaser
    assert "white-space: nowrap" in collapsed
    assert "overflow: hidden" in collapsed
    assert "text-overflow: ellipsis" in collapsed
    assert "white-space: normal" in revealed


def test_visible_news_metadata_keeps_the_open_link_at_the_row_end() -> None:
    metadata_link = _rule_for(".newsmeta .newslink")

    assert "margin-left: auto" in metadata_link


def test_no_entry_animation_can_leave_content_invisible() -> None:
    """Content must be readable without any animation having run.

    `animation-fill-mode: both` holds the *start* frame until the animation
    plays, so a keyframe that begins at `opacity: 0` makes visibility depend on
    the animation completing AND being repainted. On macOS, WKWebView finished
    all ~22 `rise` animations — computed opacity 1, playState "finished" — and
    never repainted the composited layers. The dashboard rendered blank: right
    DOM, right styles, no pixels. Chromium (Windows) and Safari were both fine,
    so nothing caught it until the app was run on a Mac.

    Motion is welcome; motion that gates legibility is not.
    """
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    filling = {
        name
        for name, shorthand in re.findall(
            r"animation:\s*([\w-]+)?([^;}]*)", stylesheet
        )
        if "both" in shorthand or "backwards" in shorthand
        for name in ([name] if name else re.findall(r"([\w-]+)", shorthand)[:1])
    }
    # `animation: rise 0.45s ease both` puts the name first; `animation: 0.45s
    # ease both rise` (minified order) puts it last. Catch both spellings.
    filling |= {
        match[-1]
        for match in re.findall(r"animation:\s*([^;}]*(?:both|backwards)[^;}]*)", stylesheet)
        for match in [re.findall(r"[a-zA-Z][\w-]*", match)]
        if match and match[-1] not in {"both", "backwards", "ease", "linear", "forwards"}
    }
    assert filling, "expected to find fill-mode animations to check"

    for name in sorted(filling):
        block = re.search(
            rf"@keyframes\s+{re.escape(name)}\s*\{{(.*?)\n\}}", stylesheet, re.S
        )
        if block is None:
            continue
        assert "opacity: 0" not in block.group(1), (
            f"@keyframes {name} starts at opacity 0 and is used with a filling "
            f"animation, so anything it decorates is invisible until the "
            f"animation runs and repaints. Animate transform instead."
        )
