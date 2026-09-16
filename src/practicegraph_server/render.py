"""Fleet dashboard renderer (FR-DSH) in the product design language: warm
paper ground, teal signal, mono figures, and the design system's honest
suppression pattern ("withheld, not zero").

Server-rendered, script-free HTML (NFR-SEC-2): no script, no external
references, inline vector charts, every dynamic value escaped. Pure function
of (view-model, generated_at) — deterministic and byte-golden-testable.
Suppressed cells render as "unavailable", never zero, never interpolated
(FR-DSH-2); days without data render as "no data" so absence is never
confused with suppression.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from practicegraph.report.design import (
    TOKENS_CSS,
    brand_header,
    esc,
    est_pill,
    suppressed_panel,
)
from practicegraph.report.format import compact, count, percent, usd
from practicegraph.wire import (
    ENGAGEMENT_KEYS,
    MATURITY_LEVEL_VALUES,
    MATURITY_SIGNAL_KEYS,
    PREMIUM_FAMILIES,
    TOOL_VALUES,
    WORK_TYPE_KEYS,
)
from practicegraph_server import SERVER_VERSION
from practicegraph_server.view import STATUS_NO_DATA, STATUS_OK, STATUS_SUPPRESSED

_DASH_CSS = """
.wrap { max-width:1100px; margin:0 auto; padding:28px 32px 72px; }
/* Live bar: idle grey dot + "as of" when static; a green breathing dot +
   "refreshing every Ns" when live (meta-refresh, no script). */
.livebar { display:inline-flex; align-items:center; gap:8px; margin:2px 0 18px;
           font-family:var(--mono); font-size:11.5px; color:var(--ink-500); }
.livebar a { color:var(--teal-700); text-decoration:none;
             border-bottom:1px solid transparent; }
.livebar a:hover { border-bottom-color:var(--teal-700); }
.livebar .livedot { width:8px; height:8px; border-radius:50%;
                    background:var(--ink-400); flex:none; }
.livebar.on { color:var(--positive-600); }
.livebar.on .livedot { background:var(--positive-600);
                       box-shadow:0 0 0 0 rgba(47,125,87,.5);
                       animation:livepulse 2.2s ease-out infinite; }
@keyframes livepulse {
  0% { box-shadow:0 0 0 0 rgba(47,125,87,.45); }
  70% { box-shadow:0 0 0 7px rgba(47,125,87,0); }
  100% { box-shadow:0 0 0 0 rgba(47,125,87,0); }
}
@media (prefers-reduced-motion: reduce) {
  .livebar.on .livedot { animation:none; }
}
header.top { display:flex; align-items:center; gap:14px; margin-bottom:6px; }
.mark { width:38px; height:38px; border-radius:10px; background:var(--teal-600);
        display:flex; align-items:center; justify-content:center; flex:none; }
.brand { display:flex; flex-direction:column; gap:1px; }
.brand .name { font-weight:700; font-size:16px; color:var(--ink-900);
               letter-spacing:-.01em; }
.brand .name b { color:var(--teal-700); font-weight:700; }
.brand .sub { font-size:12.5px; color:var(--ink-500); }
header.top .trust { margin-left:auto; }
h1 { font-family:var(--sans); font-size:30px; font-weight:800;
     letter-spacing:-.015em; color:var(--ink-900); margin:14px 0 0; }
p.pagesub { font-size:14px; color:var(--ink-600); margin:4px 0 26px; }
p.pagesub .ctx { color:var(--ink-400); }
p.pagesub a { color:var(--teal-700); font-weight:600; text-decoration:none;
              border-bottom:1px solid var(--teal-200); }
.driftsplit { font-family:var(--mono); font-size:10.5px; color:var(--ink-400);
              white-space:nowrap; margin-top:2px; }
.onboard .step { margin-top:12px; }
.onboard .step .t { font-size:13.5px; font-weight:600; color:var(--ink-800); }
.onboard code { display:block; font-family:var(--mono); font-size:12px;
                color:var(--ink-700); background:var(--paper-50);
                border:1px solid var(--border-hair); border-radius:var(--r-sm);
                padding:8px 10px; margin-top:6px; overflow-x:auto; }
.stack { display:flex; flex-direction:column; gap:20px; }
.herostrip { display:grid; grid-template-columns:1.3fr 1fr 1fr 1fr; gap:24px;
             align-items:center; }
.herostrip > div + div { border-left:1px solid var(--border-hair); padding-left:24px; }
.stat .v { font-family:var(--mono); font-variant-numeric:tabular-nums;
           font-weight:600; color:var(--ink-900); }
.stat .v.hero { font-size:44px; letter-spacing:-.02em; line-height:1.05; }
.stat .v.lg { font-size:26px; }
.stat .l { font-size:11.5px; font-weight:600; letter-spacing:.08em;
           text-transform:uppercase; color:var(--ink-500); margin-bottom:7px; }
.stat .c { font-size:12.5px; color:var(--ink-500); margin-top:5px; }
.estnote { margin-top:18px; padding-top:14px; border-top:1px solid var(--border-hair);
           font-size:11.5px; color:var(--ink-400); display:flex; align-items:center;
           gap:8px; }
.estnote .pill { font-weight:600; color:var(--ink-500);
                 border:1px solid var(--border-soft); border-radius:var(--r-pill);
                 padding:0 6px; font-size:10px; }
.answers { display:grid; grid-template-columns:repeat(3,1fr); gap:20px; }
.answer { display:flex; flex-direction:column; gap:12px; border-top:3px solid
          var(--teal-600); }
.answer.positive { border-top-color:var(--positive-600); }
.answer.caution { border-top-color:var(--caution-600); }
.answer .eyebrow.pos { color:var(--positive-600); }
.answer .eyebrow.cau { color:var(--caution-600); }
.answer .eyebrow.tea { color:var(--teal-700); }
.answer .serif { font-family:var(--serif); font-style:italic; font-size:15px;
                 color:var(--ink-700); line-height:1.4; }
table.days { border-collapse:collapse; width:100%; font-size:13.5px; }
table.days th { text-align:left; font-size:11.5px; font-weight:600;
                letter-spacing:.08em; text-transform:uppercase; color:var(--ink-500);
                padding:0 10px 10px 0; }
table.days td { padding:10px 10px 10px 0; border-top:1px solid var(--border-hair);
                color:var(--ink-800); vertical-align:top; }
table.days th.n, table.days td.n { text-align:right; font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
table.days td.day { font-family:var(--mono); font-size:12.5px;
                    color:var(--ink-600); white-space:nowrap; }
table.days td.nodata { color:var(--ink-400); }
.brk { display:flex; flex-direction:column; gap:11px; }
.brkrow { display:grid; grid-template-columns:130px 1fr auto auto; gap:12px;
          align-items:center; }
.brkrow .lab { font-size:13.5px; font-weight:600; color:var(--ink-800); }
.brkrow .track { height:9px; background:var(--paper-100);
                 border-radius:var(--r-pill); overflow:hidden; }
.brkrow .fill { display:block; height:100%; background:var(--teal-500);
                border-radius:var(--r-pill); }
.brkrow .val { font-family:var(--mono); font-variant-numeric:tabular-nums;
               font-size:13px; font-weight:600; color:var(--ink-900); }
.brkrow .pct { font-family:var(--mono); font-size:11.5px; color:var(--ink-500);
               width:38px; text-align:right; }
svg.spend .axis { stroke:var(--border-hair); stroke-width:1; }
svg.spend text { font-family:var(--mono); font-size:10px; fill:var(--ink-400); }
.legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:12px; }
.legend span { display:inline-flex; align-items:center; gap:6px; font-size:12px;
               color:var(--ink-600); }
.legend i { width:10px; height:10px; border-radius:3px; display:inline-block; }
@media (max-width:860px){ .herostrip,.answers { grid-template-columns:1fr; }
  .herostrip > div + div { border-left:none; padding-left:0; } }
"""

UNAVAILABLE_LABEL = "unavailable (below k-anonymity threshold)"
NO_DATA_LABEL = "no data"

_WORK_TYPE_LABELS = {
    "build": "Build",
    "investigate": "Investigate",
    "converse": "Converse",
    "unknown": "Unknown",
}
_MATURITY_LABELS = {
    "cache_reuse": "Cache reuse",
    "tool_usage": "Tool usage",
    "multi_tool": "Multi-tool breadth",
    "consistency": "Consistency",
}
_MATURITY_RAMP = {
    "not_yet": "#d8d2c6",
    "emerging": "#b9a06a",
    "developing": "#4ea191",
    "leading": "#226c5f",
}
_LEVEL_LABELS = {
    "not_yet": "not yet",
    "emerging": "emerging",
    "developing": "developing",
    "leading": "leading",
}

# ---- closed verdict catalogs (pattern: report/brief.py; NFR-QLT-3) -----------
# No free text: every answer-card sentence is a fixed template chosen by a
# deterministic band function of the view-model, so the page stays
# byte-golden and every possible sentence is copy-scanned in tests.

VALUE_CACHE_STRONG_MIN = 70  # cached_pct >= 70 -> strong reuse
VALUE_CACHE_MODERATE_MIN = 40  # 40..69 -> moderate; below -> low

VALUE_VERDICTS: dict[str, str] = {
    "strong": (
        "Most context is being reused, not re-billed - the fleet is getting "
        "more done per dollar."
    ),
    "moderate": (
        "A fair share of context is reused; colder sessions still pay full "
        "price more often than they need to."
    ),
    "low": (
        "Most context is billed cold instead of reused - warming cache reuse "
        "is the clearest value lever."
    ),
    "no_data": (
        "Not enough released days carry token detail to judge cache "
        "efficiency yet."
    ),
}

WASTE_PREMIUM_HEAVY_MIN = 70  # premium share >= 70 -> heavy
WASTE_PREMIUM_MODERATE_MIN = 40  # 40..69 -> moderate; below -> light

WASTE_VERDICTS: dict[str, str] = {
    "heavy": (
        "Premium models carry most of the estimated spend - the clearest "
        "place to tighten without slowing work."
    ),
    "moderate": (
        "Premium models carry a meaningful share of estimated spend - "
        "routing routine work down a tier is the nearest saving."
    ),
    "light": (
        "Premium models are a small share of estimated spend - routing "
        "discipline is holding."
    ),
    "unpriced": (
        "Nothing in this window carries a priced estimate yet, so spend "
        "shares cannot be compared."
    ),
}

ADOPTION_VERDICTS: dict[str, str] = {
    "rising": (
        "More contributors on the latest released day than the earliest - "
        "adoption is widening."
    ),
    "steady": "Contributor counts are holding steady across released days.",
    "single_day": (
        "Only one released day so far - trend reads need more days above "
        "the threshold."
    ),
    "none": (
        "No released days yet - adoption reads stay withheld until enough "
        "contributors emit."
    ),
}

# Estimate-bias caveat (rendered as "<N> " + this template when N > 0).
UNPRICED_CAVEAT = "turn(s) used unpriced models - fleet spend is understated"

# Coverage card fixed labels (sub-k subgroups stay withheld, INV-4).
COVERAGE_WITHHELD = "withheld"
COVERAGE_NOT_DETECTED = "not detected"

# Empty-org onboarding (static copy; placeholders only, never real values).
ONBOARDING_INTRO = (
    "No aggregates have been received for this org yet. Each machine opts in "
    "from the agent side - run these on a developer machine:"
)
ONBOARDING_STEPS: tuple[tuple[str, str], ...] = (
    (
        "Point the agent at this server",
        "practicegraph config set --api-base-url <server-url> --org-id <org-id>",
    ),
    (
        "Store the org token over stdin - it is never echoed",
        "<org-token> | practicegraph config set-token",
    ),
    (
        "Preview the exact payload, then opt in",
        "practicegraph consent on",
    ),
)
ONBOARDING_NOTE = (
    "Data appears after each agent's next tick, and a day renders only once "
    "at least k contributors emit for it - withheld, not zero, until then."
)


def _value_band(cached_pct: int, prompt_tokens: int) -> str:
    """Value verdict band: released-days cache percentage, honest when no
    released day carried prompt-side token detail."""
    if prompt_tokens <= 0:
        return "no_data"
    if cached_pct >= VALUE_CACHE_STRONG_MIN:
        return "strong"
    if cached_pct >= VALUE_CACHE_MODERATE_MIN:
        return "moderate"
    return "low"


def _waste_band(premium_pct: int, priced_spend: int) -> str:
    """Waste verdict band: premium share of estimated spend."""
    if priced_spend <= 0:
        return "unpriced"
    if premium_pct >= WASTE_PREMIUM_HEAVY_MIN:
        return "heavy"
    if premium_pct >= WASTE_PREMIUM_MODERATE_MIN:
        return "moderate"
    return "light"


def _adoption_band(released_contributors: list[int]) -> str:
    """Adoption verdict band: contributor trend across released days only."""
    if not released_contributors:
        return "none"
    if len(released_contributors) == 1:
        return "single_day"
    if released_contributors[-1] > released_contributors[0]:
        return "rising"
    return "steady"

_FAMILY_COLORS: tuple[tuple[str, str], ...] = (
    ("claude_opus", "#1b574b"),
    ("claude_sonnet", "#2f8576"),
    ("claude_haiku", "#82bcae"),
    ("gpt_5_codex", "#738069"),
    ("gpt_5_mini", "#cdd4c2"),
    ("gpt_5", "#4a5b4f"),
    ("gpt_4", "#b6d8cf"),
    ("o_series", "#a9761b"),
    ("other", "#97a09a"),
)


def _family_color(family: str) -> str:
    for name, color in _FAMILY_COLORS:
        if name == family:
            return color
    return "#97a09a"


def _stat(label: str, value_html: str, caption: str, size: str = "lg") -> str:
    caption_html = f'<div class="c">{esc(caption)}</div>' if caption else ""
    return (
        f'<div class="stat"><div class="l">{esc(label)}</div>'
        f'<div class="v {size}">{value_html}</div>{caption_html}</div>'
    )


def _hero_strip(totals: dict[str, Any], k: int) -> str:
    if totals["status"] != STATUS_OK:
        label = UNAVAILABLE_LABEL if totals["status"] == STATUS_SUPPRESSED else NO_DATA_LABEL
        detail = (
            "Every day in this window is below the k-anonymity threshold - withheld, "
            "not zero."
            if totals["status"] == STATUS_SUPPRESSED
            else "No aggregates have been received for this window yet."
        )
        return (
            '<div class="card"><div class="stat">'
            '<div class="l">Estimated fleet spend</div>'
            f'<div class="v lg" style="color:var(--suppressed-fg);font-family:'
            f'var(--sans);font-weight:600;">{esc(label)}</div>'
            f'<div class="c">{esc(detail)}</div></div></div>'
        )
    tokens = totals.get("tokens_total", 0)
    cached_pct = totals.get("cached_pct", 0)
    unpriced = int(totals.get("unpriced_turns", 0))
    caveat = (
        f'<div class="estnote">{esc(count(unpriced))} {esc(UNPRICED_CAVEAT)}</div>'
        if unpriced > 0
        else ""
    )
    return (
        '<div class="card">'
        '<div class="herostrip">'
        + _stat(
            "Estimated fleet spend",
            f"{esc(usd(totals['estimated_cost_micro_usd']))}{est_pill()}",
            f"across {count(totals['days_included'])} displayable day(s)",
            size="hero",
        )
        + _stat("Token volume", esc(compact(tokens)), f"{cached_pct}% served from cache")
        + _stat(
            "Assistant turns",
            esc(count(totals["assistant_turns"])),
            "aggregated fleet-wide",
        )
        + _stat(
            "Peak daily contributors",
            esc(count(totals["max_contributors"])),
            f"k-anonymity threshold >= {count(k)}",
        )
        + "</div>"
        + '<div class="estnote"><span class="pill">est.</span>costs estimated on '
        "endpoints from local token counts and versioned rate cards - no raw data "
        "reaches this server</div>"
        + caveat
        + "</div>"
    )


def _spend_chart(days: list[dict[str, Any]]) -> str:
    width, height, base, top = 1040, 190, 160, 14
    n = max(1, len(days))
    step = width // n
    bar_width = max(6, step - 10)
    max_cost = max(
        (cell["estimated_cost_micro_usd"] for cell in days if cell["status"] == STATUS_OK),
        default=0,
    )
    parts = [
        f'<svg class="spend" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Daily estimated fleet spend, stacked by model family" '
        f'width="100%" height="{height}">'
    ]
    parts.append(
        f'<line class="axis" x1="0" y1="{base}" x2="{width}" y2="{base}"></line>'
    )
    families_seen: list[str] = []
    for index, cell in enumerate(days):
        x = index * step + (step - bar_width) // 2
        label = cell["day"][5:]  # MM-DD, deterministic slice of ISO date
        parts.append(
            f'<text x="{x + bar_width // 2}" y="{base + 14}" '
            f'text-anchor="middle">{esc(label)}</text>'
        )
        if cell["status"] == STATUS_OK and max_cost > 0:
            y = base
            spend = cell.get("spend_by_family", {})
            remainder = cell["estimated_cost_micro_usd"] - sum(spend.values())
            segments = list(spend.items())
            if remainder > 0:
                segments.append(("other", remainder))
            for family, value in segments:
                seg_height = int(value) * (base - top) // max_cost
                if seg_height <= 0:
                    continue
                y -= seg_height
                if family not in families_seen:
                    families_seen.append(family)
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{bar_width}" '
                    f'height="{seg_height}" rx="2" '
                    f'fill="{_family_color(str(family))}"></rect>'
                )
        elif cell["status"] == STATUS_SUPPRESSED:
            parts.append(
                f'<rect x="{x}" y="{base - 10}" width="{bar_width}" height="10" rx="2" '
                'fill="#efece4" stroke="#cdc7ba" stroke-dasharray="3 2"></rect>'
            )
    parts.append("</svg>")
    legend = "".join(
        f'<span><i style="background:{_family_color(f)}"></i>{esc(f)}</span>'
        for f in families_seen
    )
    legend += (
        '<span><i style="background:#efece4;border:1px dashed #cdc7ba"></i>'
        "withheld (below k)</span>"
    )
    return "".join(parts) + f'<div class="legend">{legend}</div>'


def _answer_cards(totals: dict[str, Any], days: list[dict[str, Any]]) -> str:
    """Verdict cards. Each sentence is picked from a closed catalog by a
    deterministic band function of the view-model — never free text."""
    if totals["status"] != STATUS_OK:
        return ""
    cached_pct = int(totals.get("cached_pct", 0))
    prompt_tokens = int(totals.get("prompt_tokens", 0))
    spend_by_family = totals.get("spend_by_family", {})
    total_spend = int(totals.get("estimated_cost_micro_usd", 0))
    premium = sum(int(spend_by_family.get(family, 0)) for family in PREMIUM_FAMILIES)
    premium_pct = percent(premium, total_spend)
    tools = totals.get("tools_observed_max", 0)
    released = [
        int(cell["contributors"]) for cell in days if cell["status"] == STATUS_OK
    ]
    cards = [
        (
            "positive",
            "pos",
            "Value - are we getting it?",
            f"{cached_pct}%",
            "cache efficiency",
            VALUE_VERDICTS[_value_band(cached_pct, prompt_tokens)],
        ),
        (
            "caution",
            "cau",
            "Waste - where is it?",
            f"{premium_pct}%",
            "of estimated spend on premium models",
            WASTE_VERDICTS[_waste_band(premium_pct, total_spend)],
        ),
        (
            "teal",
            "tea",
            "Adoption - is it maturing?",
            count(totals["max_contributors"]),
            f"peak daily contributors - {count(tools)} tool(s) observed",
            ADOPTION_VERDICTS[_adoption_band(released)],
        ),
    ]
    out = ['<div class="answers">']
    for tone, eyebrow_class, eyebrow, value, label, serif in cards:
        out.append(
            f'<div class="card answer {tone}">'
            f'<div class="eyebrow {eyebrow_class}">{esc(eyebrow)}</div>'
            f'<div class="stat"><div class="v lg">{esc(value)}</div>'
            f'<div class="c">{esc(label)}</div></div>'
            f'<p class="serif">{esc(serif)}</p>'
            "</div>"
        )
    out.append("</div>")
    return "".join(out)


def _coverage_card(days: list[dict[str, Any]], totals: dict[str, Any], k: int) -> str:
    """Source coverage + adoption (FR-DSH-1), aggregate level only: per-tool
    contributor counts from the latest released day (sub-k subgroups stay
    withheld, INV-4), sources detected across released days, and the
    peak-vs-latest contributor read."""
    released = [cell for cell in days if cell["status"] == STATUS_OK]
    if not released:
        return ""
    latest = released[-1]
    detected = 0
    rows: list[str] = []
    for tool in sorted(latest["tools"]):
        if any(cell["tools"].get(tool, 0) != 0 for cell in released):
            detected += 1
        value = latest["tools"][tool]
        if value == STATUS_SUPPRESSED:
            shown = COVERAGE_WITHHELD
        elif isinstance(value, int) and value > 0:
            shown = f"{count(value)} contributor(s)"
        else:
            shown = COVERAGE_NOT_DETECTED
        rows.append(
            '<div class="brkrow" style="grid-template-columns:1fr auto;">'
            f'<span class="lab" style="font-weight:500;color:var(--ink-600);">'
            f"{esc(tool)}</span>"
            f'<span class="val">{esc(shown)}</span></div>'
        )
    peak = int(totals.get("max_contributors", 0))
    return (
        '<div class="card">'
        '<div class="sec-head"><span class="eyebrow">Coverage</span>'
        f'<span class="meta">latest released day {esc(latest["day"])} &middot; '
        f"k &gt;= {esc(count(k))}</span></div>"
        f'<div class="brk">{"".join(rows)}</div>'
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">'
        f"{esc(count(detected))} of {esc(count(len(TOOL_VALUES)))} supported "
        "source(s) detected across released days. Adoption: peak released day "
        f"{esc(count(peak))} contributor(s) - latest released day "
        f'{esc(count(int(latest["contributors"])))} contributor(s). Aggregates '
        "only: adoption is read from cohort counters, never from individuals.</p>"
        "</div>"
    )


def _onboarding_card(k: int) -> str:
    """Empty-org onboarding (rendered only when every day is no_data): the
    three agent-side commands, placeholders only — never real values."""
    steps = "".join(
        f'<div class="step"><div class="t">{esc(title)}</div>'
        f"<code>{esc(command)}</code></div>"
        for title, command in ONBOARDING_STEPS
    )
    return (
        '<div class="card onboard">'
        '<div class="sec-head"><span class="eyebrow">Connect your first agents'
        f'</span><span class="meta">k &gt;= {esc(count(k))}</span></div>'
        f'<p class="muted" style="margin:0;">{esc(ONBOARDING_INTRO)}</p>'
        + steps
        + '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">'
        f"{esc(ONBOARDING_NOTE)}</p>"
        "</div>"
    )


def _day_row(cell: dict[str, Any], k: int) -> str:
    day = esc(cell["day"])
    if cell["status"] == STATUS_NO_DATA:
        return (
            f'<tr><td class="day">{day}</td>'
            f'<td class="nodata" colspan="6">{NO_DATA_LABEL}</td></tr>'
        )
    if cell["status"] == STATUS_SUPPRESSED:
        return (
            f'<tr><td class="day">{day}</td><td colspan="6" style="padding-right:0;">'
            '<div class="suppressed-panel" style="padding:12px 14px;">'
            '<div><div class="t">Insufficient contributors</div>'
            f'<div class="d">{UNAVAILABLE_LABEL} - withheld, not zero.</div>'
            f'<div class="m">needs &gt;= {count(k)} contributors</div></div>'
            "</div></td></tr>"
        )
    tools = cell["tools"]
    tool_bits = []
    for tool, value in sorted(tools.items()):
        if value == STATUS_SUPPRESSED:
            tool_bits.append(f"{esc(tool)}: withheld")
        else:
            tool_bits.append(f"{esc(tool)}: {esc(count(int(value)))}")
    drift = cell["drift"]
    drift_total = sum(drift.values())
    drift_split = (
        '<div class="driftsplit">'
        f'malformed {esc(count(int(drift["malformed"])))} &middot; '
        f'unknown field {esc(count(int(drift["unknown_field"])))} &middot; '
        f'unsupported {esc(count(int(drift["unsupported"])))}</div>'
        if drift_total > 0
        else ""
    )
    return (
        f'<tr><td class="day">{day}</td>'
        f'<td class="n">{esc(count(cell["contributors"]))}</td>'
        f'<td class="n">{esc(usd(cell["estimated_cost_micro_usd"]))}</td>'
        f'<td class="n">{esc(count(cell["assistant_turns"]))}</td>'
        f'<td class="n">{esc(count(cell["sessions"]))}</td>'
        f"<td>{', '.join(tool_bits)}</td>"
        f'<td class="n">{esc(count(drift_total))}{drift_split}</td></tr>'
    )


def _breakdown_card(title: str, rows: list[tuple[str, int]], total: int) -> str:
    out = [
        '<div class="card">'
        f'<div class="sec-head"><span class="eyebrow">{esc(title)}</span>'
        '<span class="meta">est. $ &middot; share</span></div><div class="brk">'
    ]
    for label, value in rows:
        pct = percent(value, total)
        out.append(
            f'<div class="brkrow"><span class="lab">{esc(label)}</span>'
            f'<span class="track"><span class="fill" style="width:{pct}%"></span>'
            f'</span><span class="val">{esc(usd(value))}</span>'
            f'<span class="pct">{pct}%</span></div>'
        )
    out.append("</div></div>")
    return "".join(out)


def _work_type_card(totals: dict[str, Any]) -> str:
    """Fleet work-type mix (FR-DSH-1) — session counts, not person buckets."""
    sessions = totals.get("work_type_sessions", {})
    total_sessions = sum(int(sessions.get(key, 0)) for key in WORK_TYPE_KEYS)
    if total_sessions == 0:
        return ""
    out = [
        '<div class="card">'
        '<div class="sec-head"><span class="eyebrow">Where fleet sessions went'
        '</span><span class="meta">by work type &middot; structural</span></div>'
        '<div class="brk">'
    ]
    for key in WORK_TYPE_KEYS:
        value = int(sessions.get(key, 0))
        if value == 0:
            continue
        pct = percent(value, total_sessions)
        out.append(
            f'<div class="brkrow"><span class="lab">{esc(_WORK_TYPE_LABELS[key])}'
            f'</span><span class="track"><span class="fill" style="width:{pct}%">'
            f'</span></span><span class="val">{esc(count(value))}</span>'
            f'<span class="pct">{pct}%</span></div>'
        )
    out.append("</div>")
    out.append(
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">Classified on '
        "endpoints from event structure only - never from content.</p></div>"
    )
    return "".join(out)


def _maturity_card(totals: dict[str, Any], k: int) -> str:
    """Fleet maturity distribution (FR-DSH-1). Small level buckets would make
    individuals enumerable, so any nonzero bucket below k withholds the whole
    card (INV-4) — the design's fixed suppression language applies.

    Since 2026-08-13 the suppression happens in view.py, where the JSON route
    lives too, and a sub-k bucket arrives as the "suppressed" marker rather
    than as its raw count. The renderer treats any marker as the withhold
    trigger; it never sees, and can never leak, the small number itself."""
    distribution = totals.get("maturity_distribution", {})
    suppressed_seen = any(
        not isinstance(levels.get(level, 0), int)
        for levels in distribution.values()
        if isinstance(levels, dict)
        for level in MATURITY_LEVEL_VALUES
    )

    def bucket(levels: dict[str, Any], level: str) -> int:
        value = levels.get(level, 0)
        return value if isinstance(value, int) else 0

    reporting = max(
        (
            sum(bucket(levels, level) for level in MATURITY_LEVEL_VALUES)
            for levels in distribution.values()
            if isinstance(levels, dict)
        ),
        default=0,
    )
    if reporting == 0 and not suppressed_seen:
        return ""
    out = [
        '<div class="card">'
        '<div class="sec-head"><span class="eyebrow">Maturity distribution</span>'
        f'<span class="meta">contributor-days &middot; k &gt;= {esc(count(k))}'
        "</span></div>"
    ]
    if suppressed_seen:
        out.append(
            suppressed_panel(
                "Maturity distribution - insufficient subgroup sizes",
                f"{UNAVAILABLE_LABEL} - withheld, not zero. Level groups this small "
                "could make individuals enumerable.",
                f"every level group needs >= {count(k)} contributor-days",
            )
        )
        out.append("</div>")
        return "".join(out)
    out.append('<div class="brk">')
    for signal in MATURITY_SIGNAL_KEYS:
        levels = distribution.get(signal, {})
        segments = "".join(
            f'<span style="display:inline-block;height:100%;'
            f"width:{percent(bucket(levels, level), reporting)}%;"
            f'background:{_MATURITY_RAMP[level]}"></span>'
            for level in MATURITY_LEVEL_VALUES
            if bucket(levels, level) > 0
        )
        out.append(
            f'<div class="brkrow" style="grid-template-columns:150px 1fr auto;">'
            f'<span class="lab">{esc(_MATURITY_LABELS[signal])}</span>'
            f'<span class="track" style="height:12px;white-space:nowrap;'
            f'font-size:0;">{segments}</span>'
            f'<span class="val">{esc(count(reporting))}</span></div>'
        )
    out.append("</div>")
    legend = "".join(
        f'<span><i style="background:{_MATURITY_RAMP[level]}"></i>'
        f"{esc(_LEVEL_LABELS[level])}</span>"
        for level in MATURITY_LEVEL_VALUES
    )
    out.append(f'<div class="legend">{legend}</div>')
    out.append("</div>")
    return "".join(out)


def _augment_totals(summary: dict[str, Any]) -> dict[str, Any]:
    """Derive presentation aggregates from ok-day cells (pure, deterministic)."""
    totals = dict(summary["totals"])
    if totals["status"] != STATUS_OK:
        return totals
    ok_days = [cell for cell in summary["days"] if cell["status"] == STATUS_OK]
    tokens_total = 0
    cached = 0
    prompt_side = 0
    tools_max = 0
    unpriced = 0
    for cell in ok_days:
        tokens = cell.get("tokens", {})
        tokens_total += sum(int(v) for v in tokens.values())
        cached += int(tokens.get("cached", 0))
        prompt_side += int(tokens.get("cached", 0)) + int(tokens.get("input", 0))
        observed = sum(
            1 for v in cell.get("tools", {}).values() if isinstance(v, int) and v > 0
        )
        tools_max = max(tools_max, observed)
        unpriced += int(cell.get("unpriced_turns", 0))
    totals["tokens_total"] = tokens_total
    totals["cached_pct"] = percent(cached, prompt_side)
    totals["prompt_tokens"] = prompt_side
    totals["tools_observed_max"] = tools_max
    totals["unpriced_turns"] = unpriced
    return totals


def render_dashboard(
    summary: dict[str, Any],
    org_id: str,
    generated_at: datetime,
    refresh_seconds: int = 0,
) -> str:
    """Server-rendered dashboard. `refresh_seconds` > 0 emits a meta-refresh so
    the page re-renders itself with fresh k-anonymous aggregates on that cadence
    — a "live" mode that stays strictly script-free (NFR-SEC-2). 0 = a static
    snapshot with a link to go live."""
    generated_label = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    days: list[dict[str, Any]] = summary["days"]
    k = summary["k_threshold"]
    totals = _augment_totals(summary)

    out: list[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('<meta charset="utf-8">')
    out.append(f"<title>PracticeGraph fleet dashboard - {esc(org_id)}</title>")
    if refresh_seconds > 0:
        # Live mode, script-free (NFR-SEC-2): the page reloads itself and the
        # server re-renders with fresh aggregates. The refresh keeps the current
        # window and live cadence so successive reloads stay on the same view.
        window_days = (
            date.fromisoformat(summary["to_day"]) - date.fromisoformat(summary["from_day"])
        ).days + 1
        target = f"?days={window_days}&amp;live={refresh_seconds}"
        out.append(
            f'<meta http-equiv="refresh" content="{refresh_seconds}; url={target}">'
        )
    out.append(f"<style>{TOKENS_CSS}{_DASH_CSS}</style>")
    out.append("</head>")
    out.append("<body>")
    out.append('<div class="wrap">')
    out.append(
        brand_header(
            " fleet",
            f"Org: {org_id}",
            "fleet aggregates only - nothing personal",
        )
    )
    out.append("<h1>Fleet spend &amp; adoption</h1>")
    out.append(
        '<p class="pagesub">The 10-second read: value, waste, and who is adopting. '
        f'<span class="ctx">&middot; {esc(summary["from_day"])} to '
        f"{esc(summary['to_day'])} (UTC) &middot; k-anonymity threshold "
        f"{esc(count(k))} &middot; no person-level data exists behind this page"
        ' &middot; window <a href="?days=7">7</a> / <a href="?days=30">30</a> / '
        '<a href="?days=90">90</a> days</span></p>'
    )
    # Live bar (script-free): a green pulse + refresh cadence when live, else a
    # static "as of" stamp with a link to go live. The window is preserved on
    # both links so toggling live never changes the days shown.
    window_days = (
        date.fromisoformat(summary["to_day"]) - date.fromisoformat(summary["from_day"])
    ).days + 1
    if refresh_seconds > 0:
        out.append(
            f'<div class="livebar on">'
            '<span class="livedot" aria-hidden="true"></span>'
            f"<span>live &middot; refreshing every {esc(count(refresh_seconds))}s "
            f"&middot; {esc(generated_label)} &middot; "
            f'<a href="?days={window_days}">pause</a></span></div>'
        )
    else:
        out.append(
            '<div class="livebar">'
            '<span class="livedot" aria-hidden="true"></span>'
            f"<span>as of load &middot; {esc(generated_label)} &middot; "
            f'<a href="?days={window_days}&amp;live=15">go live</a></span></div>'
        )
    out.append('<div class="stack">')

    out.append(_hero_strip(totals, k))
    if totals["status"] == STATUS_NO_DATA:
        out.append(_onboarding_card(k))
    out.append(_answer_cards(totals, days))

    out.append("<section class=\"card\">")
    out.append(
        '<div class="sec-head"><span class="eyebrow">Estimated spend over time'
        "</span><span class=\"meta\">stacked by model family &middot; est.</span></div>"
    )
    out.append(_spend_chart(days))
    out.append(
        '<p class="muted" style="font-size:12.5px;margin:14px 0 0;">Hatched slots '
        "mark days below the k-anonymity threshold: their values are unavailable by "
        "design, never zero.</p>"
    )
    out.append("</section>")

    out.append('<section class="card">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">Days</span>'
        f'<span class="meta">k &gt;= {esc(count(k))}</span></div>'
    )
    out.append('<table class="days"><thead><tr>')
    out.append(
        "<th>Day</th>"
        '<th class="n">Contributors</th><th class="n">Est. spend</th>'
        '<th class="n">Assistant turns</th><th class="n">Sessions</th>'
        '<th>Tool coverage</th><th class="n">Drift</th></tr></thead><tbody>'
    )
    for cell in days:
        out.append(_day_row(cell, k))
    out.append("</tbody></table>")
    out.append("</section>")

    if totals["status"] == STATUS_OK:
        out.append(_coverage_card(days, totals, k))
        spend_by_family = {
            str(family): int(value)
            for family, value in totals.get("spend_by_family", {}).items()
        }
        if spend_by_family:
            family_rows = sorted(
                spend_by_family.items(), key=lambda item: (-item[1], item[0])
            )
            out.append('<div class="grid g-2" style="display:grid;gap:20px;')
            out.append('grid-template-columns:1fr 1fr;">')
            out.append(
                _breakdown_card(
                    "By model family",
                    family_rows,
                    totals["estimated_cost_micro_usd"],
                )
            )
            engagement = totals.get("engagement", {})
            out.append(
                '<div class="card">'
                '<div class="sec-head"><span class="eyebrow">Engagement (window)'
                '</span><span class="meta">closed counters</span></div>'
                '<div class="brk">'
                + "".join(
                    '<div class="brkrow" style="grid-template-columns:1fr auto;">'
                    f'<span class="lab" style="font-weight:500;color:var(--ink-600);">'
                    f"{esc(key)}</span>"
                    f'<span class="val">{esc(count(int(engagement.get(key, 0))))}'
                    "</span></div>"
                    for key in ENGAGEMENT_KEYS
                )
                + "</div></div>"
            )
            out.append("</div>")

        # M6 depth: work-type mix and maturity distribution (schema v2).
        depth_cards = _work_type_card(totals) + _maturity_card(totals, k)
        if depth_cards:
            out.append(
                '<div style="display:grid;gap:20px;grid-template-columns:1fr 1fr;">'
            )
            out.append(depth_cards)
            out.append("</div>")

    out.append("</div>")
    out.append(
        '<div class="foot">'
        "<span>aggregates only &middot; suppression is honest (unavailable, never "
        "zero)</span>"
        f"<span>generated {esc(generated_label)} &middot; practicegraph server "
        f"v{esc(SERVER_VERSION)}</span>"
        "</div>"
    )
    out.append("</div>")
    out.append("</body>")
    out.append("</html>")
    return "\n".join(out) + "\n"


def render_error(code: str) -> str:
    """Closed error page for the dashboard route (FR-DSH-4 error state)."""
    out = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>PracticeGraph fleet dashboard - error</title>",
        f"<style>{TOKENS_CSS}</style>",
        "</head>",
        "<body>",
        '<div style="max-width:640px;margin:0 auto;padding:48px 28px;">',
        '<div class="card">',
        '<div class="eyebrow" style="margin-bottom:10px;">Unavailable</div>',
        f'<p class="muted">The dashboard could not be rendered (code: {esc(code)}). '
        "Retry, and if this persists follow the server runbook.</p>",
        "</div>",
        "</div>",
        "</body>",
        "</html>",
    ]
    return "\n".join(out) + "\n"
