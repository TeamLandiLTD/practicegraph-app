"""Single-file offline HTML daily report (FR-RPT-1, FR-RPT-2) — the compact
one-page variant, in the product design language.

Hard rules (invariant, enforced by tests): no script, no external references,
no ``src`` attributes, only in-page anchor hrefs, every dynamic value escaped.
Byte-reproducible (FR-RPT-3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from practicegraph.analysis.aggregate import DailySnapshot
from practicegraph.report.design import TOKENS_CSS, brand_header, esc, est_pill
from practicegraph.report.format import compact, count, percent, usd

if TYPE_CHECKING:
    from practicegraph.report.shell import ShellExtras

_PAGE_CSS = """
.wrap { max-width:720px; margin:0 auto; padding:28px 28px 60px; }
header.top { display:flex; align-items:center; gap:14px; margin-bottom:22px; }
.mark { width:38px; height:38px; border-radius:10px; background:var(--teal-600);
        display:flex; align-items:center; justify-content:center; flex:none; }
.brand { display:flex; flex-direction:column; gap:1px; }
.brand .name { font-weight:700; font-size:16px; color:var(--ink-900);
               letter-spacing:-.01em; }
.brand .name b { color:var(--teal-700); font-weight:700; }
.brand .sub { font-size:12.5px; color:var(--ink-500); }
header.top .trust { margin-left:auto; }
.stack { display:flex; flex-direction:column; gap:18px; }
.figure { font-family:var(--mono); font-variant-numeric:tabular-nums; font-size:44px;
          font-weight:600; color:var(--ink-900); letter-spacing:-.02em; line-height:1; }
.ratecard { font-family:var(--mono); font-size:11.5px; color:var(--ink-500);
            margin-top:10px; }
table.usage { border-collapse:collapse; width:100%; font-size:13.5px; }
table.usage th { text-align:left; font-size:11.5px; font-weight:600;
                 letter-spacing:.08em; text-transform:uppercase; color:var(--ink-500);
                 padding:0 10px 10px 0; }
table.usage td { padding:9px 10px 9px 0; border-top:1px solid var(--border-hair);
                 color:var(--ink-800); }
table.usage th.n, table.usage td.n { text-align:right; font-family:var(--mono);
  font-variant-numeric:tabular-nums; }
table.usage td.model { font-family:var(--mono); font-size:12.5px; color:var(--ink-600); }
.srcline { display:flex; gap:12px; align-items:baseline; padding:10px 0;
           border-top:1px solid var(--border-hair); font-size:13.5px; }
.srcline:first-of-type { border-top:none; }
.srcline .nm { font-weight:600; color:var(--ink-800); min-width:140px; }
.srcline .meta { font-family:var(--mono); font-size:12px; color:var(--ink-500);
                 flex:1; }
.observation h1 { margin:8px 0 10px; font-family:var(--serif); font-size:26px;
                  line-height:1.2; font-weight:500; }
.observation .meta { font-family:var(--mono); font-size:11.5px;
                     color:var(--teal-700); margin-top:12px; }
.observation .muted { margin-top:12px; }
"""


def render_html(
    snapshot: DailySnapshot,
    generated_at: datetime,
    app_version: str,
    rate_card_version: str,
    extras: ShellExtras | None = None,
) -> str:
    generated_label = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    tokens_total = sum(
        row.tokens.input + row.tokens.output + row.tokens.cached
        + row.tokens.cache_creation
        for row in snapshot.rows
    )
    cached = sum(row.tokens.cached for row in snapshot.rows)
    prompt_side = cached + sum(row.tokens.input for row in snapshot.rows)

    out: list[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en">')
    out.append("<head>")
    out.append('<meta charset="utf-8">')
    out.append(
        f"<title>PracticeGraph daily report {esc(snapshot.day.isoformat())}</title>"
    )
    out.append(f"<style>{TOKENS_CSS}{_PAGE_CSS}</style>")
    out.append("</head>")
    out.append("<body>")
    out.append('<div class="wrap stack">')
    report_day = (
        f"Daily report - {extras.local_day.isoformat()} "
        f"({extras.schedule.timezone_name} local) - UTC accounting day "
        f"{snapshot.day.isoformat()}"
        if extras is not None and extras.local_day is not None
        else f"Daily report - {snapshot.day.isoformat()} (UTC)"
    )
    out.append(brand_header("", report_day, "this machine only - nothing sent"))

    if extras is not None:
        observation = extras.practice_observation
        out.append(
            f'<section class="card observation" data-observation-id="'
            f'{esc(observation.observation_id)}">'
        )
        out.append('<div class="eyebrow">Work-pattern observation</div>')
        out.append(f'<h1>{esc(observation.title)}</h1>')
        out.append(f'<p>{esc(observation.body)}</p>')
        out.append(
            f'<p class="meta">{esc(observation.confidence)} &middot; '
            f'{esc(observation.period)}</p>'
        )
        out.append(f'<p class="muted">{esc(observation.caveat)}</p>')
        out.append("</section>")

    out.append('<section class="card" id="spend">')
    out.append(
        '<div class="eyebrow" style="margin-bottom:12px;">Estimated spend</div>'
    )
    out.append(
        f'<div class="figure">{esc(usd(snapshot.total_cost_micro_usd))}{est_pill()}</div>'
    )
    out.append(
        f'<div class="ratecard">rate card {esc(rate_card_version)} &middot; '
        f"{esc(count(snapshot.session_count))} sessions &middot; "
        f"{esc(compact(tokens_total))} tokens &middot; "
        f"{percent(cached, prompt_side)}% served from cache</div>"
    )
    if snapshot.total_unpriced_turns:
        out.append(
            f'<p class="muted" style="font-size:12.5px;margin:12px 0 0;">Turns without '
            f"a rate-card entry: {esc(count(snapshot.total_unpriced_turns))}</p>"
        )
    out.append("</section>")

    out.append('<section class="card" id="usage">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">Usage by tool and model</span>'
        '<span class="meta">assistant turns &middot; est. cost</span></div>'
    )
    visible_rows = [row for row in snapshot.rows if row.assistant_turns > 0]
    if visible_rows:
        out.append('<table class="usage"><thead><tr>')
        out.append(
            "<th>Tool</th><th>Model</th>"
            '<th class="n">Turns</th><th class="n">Input</th><th class="n">Cached</th>'
            '<th class="n">Output</th><th class="n">Cost</th></tr></thead><tbody>'
        )
        for row in visible_rows:
            out.append(
                f"<tr><td>{esc(row.tool)}</td>"
                f'<td class="model">{esc(row.model)}</td>'
                f'<td class="n">{esc(count(row.assistant_turns))}</td>'
                f'<td class="n">{esc(count(row.tokens.input))}</td>'
                f'<td class="n">{esc(count(row.tokens.cached))}</td>'
                f'<td class="n">{esc(count(row.tokens.output))}</td>'
                f'<td class="n">{esc(usd(row.cost_micro_usd, 4))}</td></tr>'
            )
        out.append("</tbody></table>")
    else:
        out.append('<p class="muted">No assistant activity recorded for this day.</p>')
    out.append(
        '<p class="muted num" style="font-size:12px;margin:14px 0 0;">'
        f"sessions {esc(count(snapshot.session_count))} &middot; "
        f"assistant turns {esc(count(snapshot.total_assistant_turns))} &middot; "
        f"tool calls {esc(count(snapshot.total_tool_calls))} &middot; "
        f"interruptions {esc(count(snapshot.total_interruptions))} &middot; "
        f"retries {esc(count(snapshot.total_retries))}</p>"
    )
    out.append("</section>")

    out.append('<section class="card" id="sources">')
    out.append(
        '<div class="sec-head"><span class="eyebrow">Source health</span>'
        '<span class="meta">all scanned logs</span></div>'
    )
    drift_any = False
    for health_row in snapshot.source_health:
        health = health_row.health
        if health.seen == 0:
            tag = '<span class="tag quiet">no logs found</span>'
            meta = "source paused"
        elif health.drift_detected:
            drift_any = True
            tag = '<span class="tag emerging">format drift</span>'
            meta = (
                f"{count(health.seen)} seen - {count(health.parsed)} parsed - "
                f"{count(health.skipped)} ignored - {count(health.malformed)} malformed"
                f" - {count(health.unsupported)} unrecognized - "
                f"{count(health.unknown_field)} unfamiliar fields"
            )
        else:
            tag = '<span class="tag steady">parsed</span>'
            meta = f"{count(health.seen)} seen - {count(health.parsed)} parsed"
        out.append(
            f'<div class="srcline"><span class="nm">{esc(health_row.source_id)}</span>'
            f'<span class="meta">{esc(meta)}</span>{tag}</div>'
        )
    if drift_any:
        out.append(
            '<p class="muted" style="font-size:12.5px;margin:12px 0 0;">Some records '
            "were not recognized. Parsing continued and skipped them safely; an agent "
            "update may be available. No log content was recorded.</p>"
        )
    out.append("</section>")

    out.append(
        '<div class="foot">'
        f"<span>practicegraph &middot; generated {esc(generated_label)}</span>"
        f"<span>all analysis local &middot; rate card {esc(rate_card_version)} "
        f"&middot; v{esc(app_version)}</span>"
        "</div>"
    )
    out.append("</div>")
    out.append("</body>")
    out.append("</html>")
    return "\n".join(out) + "\n"
