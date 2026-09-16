"""The PracticeGraph design language, ported from the mytokenindex design
system (Claude Design export, 2026-06-24).

Shared tokens and snippets for every HTML surface. Hard rules still apply
(FR-RPT-2/NFR-SEC-2): no scripts, no external references — so the brand
webfonts (Schibsted Grotesk / Newsreader / IBM Plex Mono) are substituted
with the system stacks the design system itself prescribes for offline
artifacts. Inline SVGs deliberately omit xmlns (valid in HTML, and keeps
"http" out of the document).
"""

from __future__ import annotations

import html

# Palette + primitives — mirrors tokens/colors.css, typography.css, spacing.css.
TOKENS_CSS = """
:root {
  --paper-0:#fcfbf8; --paper-50:#f6f3ec; --paper-100:#efe9df; --paper-200:#e3dccd;
  --ink-900:#181d1b; --ink-800:#232b28; --ink-700:#39423d; --ink-600:#55605a;
  --ink-500:#727d76; --ink-400:#97a09a;
  --teal-900:#0f3730; --teal-800:#15473d; --teal-700:#1b574b; --teal-600:#226c5f;
  --teal-500:#2f8576; --teal-400:#4ea191; --teal-300:#82bcae; --teal-200:#b6d8cf;
  --teal-100:#dbece7; --teal-50:#ecf4f1;
  --sage-500:#738069; --sage-200:#cdd4c2; --sage-100:#e4e8dc;
  --positive-600:#2f7d57; --positive-100:#dcefe2;
  --caution-600:#a9761b; --caution-100:#f4e7cc;
  --critical-600:#b04a30;
  --suppressed-fg:#8d958f; --suppressed-bg:#efece4; --suppressed-line:#cdc7ba;
  --border-hair:#e7e0d3; --border-soft:#d8cfbd;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --serif:Georgia,"Iowan Old Style","Times New Roman",Times,serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
  --r-sm:6px; --r-md:10px; --r-lg:14px; --r-pill:999px;
  --sh-sm:0 1px 3px rgba(24,40,35,.06),0 1px 2px rgba(24,40,35,.04);
  --sh-md:0 4px 12px rgba(24,40,35,.07),0 1px 3px rgba(24,40,35,.05);
}
* { box-sizing:border-box; }
html,body { margin:0; }
body {
  font-family:var(--sans); background:var(--paper-0); color:var(--ink-700);
  font-size:15px; line-height:1.5; -webkit-font-smoothing:antialiased;
}
.num { font-family:var(--mono); font-variant-numeric:tabular-nums lining-nums;
       font-feature-settings:"tnum" 1,"lnum" 1; }
.eyebrow { font-size:11.5px; font-weight:600; letter-spacing:.08em;
           text-transform:uppercase; color:var(--ink-500); }
.card { background:#fff; border:1px solid var(--border-hair); border-radius:var(--r-lg);
        box-shadow:var(--sh-sm); padding:22px 24px; }
.grid { display:grid; gap:18px; }
.g-2 { grid-template-columns:1fr 1fr; }
.g-3 { grid-template-columns:repeat(3,1fr); }
.muted { color:var(--ink-500); }
.lede { font-family:var(--serif); font-style:italic; font-size:16px;
        color:var(--ink-700); line-height:1.5; }
.sec-head { display:flex; align-items:baseline; justify-content:space-between;
            margin-bottom:16px; gap:12px; }
.sec-head .meta { font-family:var(--mono); font-size:11px; color:var(--ink-400); }
.est { display:inline-block; vertical-align:middle; margin-left:10px;
       font-family:var(--sans); font-size:11px; font-weight:600; color:var(--ink-400);
       border:1px solid var(--border-soft); border-radius:var(--r-pill); padding:2px 8px; }
.tag { font-size:11px; font-weight:600; padding:2px 8px; border-radius:var(--r-pill); }
.tag.steady { background:var(--positive-100); color:var(--positive-600); }
.tag.emerging { background:var(--caution-100); color:var(--caution-600); }
.tag.leading { background:var(--teal-100); color:var(--teal-800); }
.tag.quiet { background:var(--suppressed-bg); color:var(--suppressed-fg); }
.trust { display:inline-flex; align-items:center; gap:8px; padding:8px 14px;
         border-radius:var(--r-pill); background:var(--teal-50);
         border:1px solid var(--teal-200); color:var(--teal-800);
         font-size:12.5px; font-weight:600; white-space:nowrap; }
.trust svg { width:15px; height:15px; }
.suppressed-panel { display:flex; gap:12px; align-items:flex-start; padding:16px 18px;
  border-radius:var(--r-lg); background:var(--suppressed-bg);
  background-image:repeating-linear-gradient(45deg,transparent,transparent 7px,
    rgba(141,149,143,.12) 7px,rgba(141,149,143,.12) 8px);
  border:1px dashed var(--suppressed-line); color:var(--suppressed-fg); }
.suppressed-panel svg { flex:none; margin-top:1px; width:20px; height:20px; }
.suppressed-panel .t { font-size:14px; font-weight:600; color:var(--ink-700);
                       margin-bottom:3px; }
.suppressed-panel .d { font-size:13px; line-height:1.45; }
.suppressed-panel .m { font-family:var(--mono); font-size:11.5px; margin-top:7px; }
.foot { margin-top:28px; padding-top:18px; border-top:1px solid var(--border-hair);
        display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;
        font-size:12px; color:var(--ink-400); font-family:var(--mono); }
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def mark_svg(size: int = 22) -> str:
    """The brand mark: three rising bars in teal tints (assets/mark.svg)."""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 22 22" fill="none" '
        'aria-hidden="true">'
        '<rect x="3" y="11.5" width="3" height="5" rx="1" fill="#dbece7"></rect>'
        '<rect x="9.5" y="8" width="3" height="8.5" rx="1" fill="#82bcae"></rect>'
        '<rect x="16" y="4.5" width="3" height="12" rx="1" fill="#f3f8f6"></rect>'
        "</svg>"
    )


SHIELD_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 '
    '1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 '
    '1z"/><path d="m9 12 2 2 4-4"/></svg>'
)

LOCK_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
    "</svg>"
)

CHEVRON_SVG = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<polyline points="9 18 15 12 9 6"/></svg>'
)

EYE_OFF_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 '
    '2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 '
    '5.39-1.61"/><line x1="2" y1="2" x2="22" y2="22"/></svg>'
)


def brand_header(product_suffix: str, sub: str, trust_text: str) -> str:
    """Header: mark + practice|graph wordmark + subtitle + trust cue pill."""
    return (
        '<header class="top">'
        f'<span class="mark" aria-hidden="true">{mark_svg()}</span>'
        '<span class="brand">'
        f'<span class="name">practice<b>graph</b>{esc(product_suffix)}</span>'
        f'<span class="sub">{esc(sub)}</span>'
        "</span>"
        f'<span class="trust">{SHIELD_SVG} {esc(trust_text)}</span>'
        "</header>"
    )


def est_pill() -> str:
    return '<span class="est">estimate</span>'


def suppressed_panel(title: str, detail: str, meta: str) -> str:
    """The honest k-anonymity state: quiet hatched surface, eye-off glyph,
    fixed language — withheld, not zero (SuppressedPanel)."""
    return (
        '<div class="suppressed-panel">'
        f"{EYE_OFF_SVG}"
        f'<div><div class="t">{esc(title)}</div>'
        f'<div class="d">{esc(detail)}</div>'
        f'<div class="m">{esc(meta)}</div></div>'
        "</div>"
    )
