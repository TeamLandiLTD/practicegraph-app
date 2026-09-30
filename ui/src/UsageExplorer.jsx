import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { compact, count, currencyCode, fetchUsage, usd, usdAbout } from "./api.js";
import { AllowanceCard, BillingNote, BillingSettings, moneyLabel, UsageBasis, unpricedLine } from "./Usage.jsx";
import { Info } from "./Info.jsx";

const PERIODS = [["today", "Today"], ["7d", "Last 7 days"], ["30d", "Last 30 days"],
  ["90d", "Last 90 days"], ["all", "All retained history"]];
// The reader's own previous period, named in their words (COPY_RULES rule 2).
const PREVIOUS = {today: "yesterday", "7d": "the week before", "30d": "the month before",
  "90d": "the 90 days before"};
const toolName = tool => tool === "codex" ? "Codex" : tool === "claude_code" ? "Claude Code" : tool;
const dateLabel = value => new Date(value).toLocaleString([], {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
const categoryName = {instructions: "Visible instructions", user: "User-role messages",
  assistant: "Assistant messages", tools: "Tool arguments and results", other: "Other visible text"};

// "up from $374 the week before": the comparison the number needs, in one line.
export function comparedLine(period, current, previous) {
  const name = PREVIOUS[period];
  if (!name || !previous) return null;
  const now = usdAbout(current), then = usdAbout(previous.cost_micro_usd);
  if (now === then) return `about the same as ${name}`;
  return `${current > previous.cost_micro_usd ? "up" : "down"} from ${then} ${name}`;
}

export const pricedAmount = row => row.unpriced_turns > 0
  ? row.unpriced_turns >= row.assistant_turns ? "Price unknown" : `${usd(row.cost_micro_usd)} + unpriced`
  : usd(row.cost_micro_usd);

const WEEKDAY = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
// A single day is not a trend: show it beside the day before, on one scale.
function DayStrip({today, yesterday}) {
  const scale = Math.max(1, today.cost_micro_usd, yesterday?.cost_micro_usd || 0);
  const width = value => `${Math.round(1000 * value / scale) / 10}%`;
  const name = p => `${WEEKDAY[new Date(p.day + "T00:00:00Z").getUTCDay()]} ${p.day}`;
  const rows = [["Today", today, "now"], ...(yesterday ? [["Yesterday", yesterday, "ghost"]] : [])];
  return <div className="usage-trend">
    <h3>Usage over the period <span className="chart-unit">{currencyCode()} at listed rates</span>{yesterday ? <span className="chart-legend"><i className="swatch now" />today so far<i className="swatch ghost" />the whole of yesterday</span> : null}</h3>
    <div className="day-strip" role="group" aria-label="Today against yesterday">
      {rows.map(([label, p, tone]) => <div key={label} className={`day-row ${tone}`}>
        <div className="day-label"><strong>{label}</strong><small>{name(p)}</small></div>
        <div className="day-track"><span className="day-bar" style={{"--bar-width": width(p.cost_micro_usd)}} /></div>
        <div className="day-value">{usd(p.cost_micro_usd)}<small>{p.assistant_turns != null ? `${count(p.assistant_turns)} assistant turns` : ""}</small></div>
      </div>)}
    </div>
  </div>;
}

function Trend({series, previous}) {
  if (series.length === 1) return <DayStrip today={series[0]} yesterday={previous?.series?.[0] || (previous ? {day: previous.to_day, cost_micro_usd: previous.cost_micro_usd, assistant_turns: null} : null)} />;
  const ghost = previous?.series?.length === series.length ? previous.series : null;
  // The scale is this period's; a taller day in the period before is capped and marked.
  const peak = Math.max(1, ...series.map(p => p.cost_micro_usd));
  const top = series.reduce((best, p) => p.cost_micro_usd > best.cost_micro_usd ? p : best, series[0]);
  const [hovered, setHovered] = useState(null);
  const [pinned, setPinned] = useState(null);
  const shown = pinned || hovered;
  const index = shown ? series.indexOf(shown) : -1;
  const height = value => `${Math.max(2, 100 * value / peak)}%`;
  const label = p => `${p.day} · ${usd(p.cost_micro_usd)}${p.assistant_turns != null ? ` · ${count(p.assistant_turns)} assistant turns` : ""}`;
  const ticks = series.length <= 31;
  return <div className="usage-trend">
    <h3>Usage over the period <span className="chart-unit">{currencyCode()} at listed rates</span>{ghost ? <span className="chart-legend"><i className="swatch now" />this period<i className="swatch ghost" />the period before</span> : null}</h3>
    <div className="chart-plot" onMouseLeave={() => setHovered(null)}>
      <div className="chart-grid" aria-hidden="true">
        {[[peak, "top"], [peak / 2, "mid"], [0, "base"]].map(([value, key]) =>
          <div key={key} className={`chart-gridline chart-gridline-${key}`}><span>{usd(value)}</span></div>)}
      </div>
      {ghost ? <div className="usage-ghosts" aria-hidden="true">
        {ghost.map(p => <span key={p.day} className={`usage-ghost${p.cost_micro_usd > peak ? " over" : ""}`} style={{"--bar-height": p.cost_micro_usd > peak ? "100%" : height(p.cost_micro_usd)}} />)}
      </div> : null}
      <div className="usage-bars" role="group" aria-label="Usage trend">
        {series.map(p => <button key={p.day} type="button" className={`usage-bar${p === top && p.cost_micro_usd > 0 ? " peak" : ""}`}
          aria-pressed={pinned === p}
          style={{"--bar-height": height(p.cost_micro_usd)}}
          aria-label={`${p.day}: ${usd(p.cost_micro_usd)} at listed rates`}
          onFocus={() => setHovered(p)} onBlur={() => setHovered(null)} onMouseEnter={() => setHovered(p)}
          onClick={() => setPinned(pinned === p ? null : p)}>{p === top && p.cost_micro_usd > 0 ? <em className="peak-label">{usd(p.cost_micro_usd)}</em> : null}<span /></button>)}
      </div>
      {shown ? <div role="tooltip" className={`chart-tooltip${pinned ? " pinned" : ""}`}
        style={{"--tip-x": `${(index + 0.5) / series.length * 100}%`, "--tip-y": height(shown.cost_micro_usd)}}>
        {label(shown)}{ghost ? ` · before ${usd(ghost[index].cost_micro_usd)}` : ""}</div> : null}
      {ticks ? <div className="chart-ticks" aria-hidden="true">{series.map(p => <span key={p.day}>{WEEKDAY[new Date(p.day + "T00:00:00Z").getUTCDay()]}</span>)}</div> : null}
    </div>
    <div className="usage-chart-labels"><span>{series[0]?.day}</span><span>{series.at(-1)?.day}</span></div>
  </div>;
}

function SessionDetail({period, id, close}) {
  const panel = useRef(null);
  const [state, setState] = useState({data: null, error: false});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    panel.current?.scrollIntoView?.({block: "start"});
    panel.current?.focus({preventScroll: true});
  }, [id]);
  useEffect(() => {
    const abort = new AbortController(); let live = true;
    setState({data: null, error: false});
    fetchUsage(period, 0, id, abort.signal).then(data => {
      if (live) setState({data, error: false});
    }).catch(() => {if (live) setState({data: null, error: true});});
    return () => {live = false; abort.abort();};
  }, [period, id, retry]);
  const data = state.data;
  return <section ref={panel} tabIndex={-1} className="usage-session-detail" aria-label="Selected session">
    <div className="chead"><h3>Session evidence<Info text="Counts for the selected period. A test attempt is not a passing result." /></h3>
      <button onClick={close}>Back to sessions</button></div>
    {state.error ? <p role="alert">Session evidence could not be read. <button onClick={() => setRetry(r => r + 1)}>Try again</button></p>
      : !data ? <p role="status">Reading session counters…</p> : <>
      <p>{toolName(data.session.tool)} · {dateLabel(data.session.first_ts)} to {dateLabel(data.session.last_ts)}</p>
      <p>{pricedAmount(data.session)} at listed rates · {count(data.session.assistant_turns)} assistant turns
        {" · "}{count(data.session.tool_calls)} tool calls · {count(data.session.test_run_attempts)} recorded test attempts.</p>
      {data.session.relation ? <p>Provider-declared {data.session.relation === "fork" ? "fork" : "delegated session"}.
        {data.session.replay_status === "matched_prefix" ? ` ${count(data.session.replay_records)} matching inherited records excluded from usage.`
          : data.session.relation === "fork" ? " Inherited usage could not be fully established; totals may include replay." : ""}</p> : null}
      <h4>Transcript composition<Info text={"From the latest transcript file, inherited and compacted material "
        + "included, at about four characters per token; hidden instructions, tool schemas and images are left out — "
        + "not a measurement of the current context window."} /></h4>
      {!data.context.available ? <p>Composition is unavailable for this retained session. Refresh after local logs have been indexed.</p> : <>
        <p className="muted">Latest transcript as of {data.context.observed_at ? dateLabel(data.context.observed_at) : "its last scan"}.</p>
        <div className="usage-composition">{data.context.components.map(c => <div key={c.category}>
          <span>{categoryName[c.category]}</span><strong>~{compact(c.estimated_tokens)}</strong>
          <meter min="0" max={Math.max(1, ...data.context.components.map(p => p.characters))} value={c.characters}
            aria-label={`${categoryName[c.category]}: ${count(c.characters)} characters`} />
        </div>)}</div>
        <p>{data.context.latest_input_tokens != null
          ? `${count(data.context.latest_input_tokens)} input tokens recorded for the latest provider request.`
          : "Latest provider input size is not recorded."}</p>
        {data.context.compactions > 0 ? <p>{count(data.context.compactions)} compaction markers in the available transcript.</p> : null}
      </>}
    </>}
  </section>;
}

export function UsageExplorer({view, refresh}) {
  const [period, setPeriod] = useState("7d");
  const page = 0;
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState({data: null, error: false});
  useEffect(() => {
    const abort = new AbortController(); let live = true;
    setState(previous => previous.data?.period === period && previous.data?.page === page
      ? {...previous, error: false} : {data: null, error: false});
    fetchUsage(period, page, "", abort.signal).then(data => {
      if (live) setState({data, error: false});
    }).catch(() => {if (live) setState({data: null, error: true});});
    return () => {live = false; abort.abort();};
  }, [period, page, retry, view.generated_at]);
  const data = state.data;
  const choose = next => setPeriod(next);
  const compared = data ? comparedLine(period, data.summary.cost_micro_usd, data.previous) : null;
  const incomplete = data ? unpricedLine(data.summary.unpriced_turns, data.summary.assistant_turns) : null;
  return <div className="layout col usage-explorer">
    <section className="card usage-overview">
      <div className="usage-controls"><h1>Usage</h1>
      <div className="tabs" role="group" aria-label="Usage period">{PERIODS.map(([id, label]) =>
        <button key={id} aria-pressed={period === id} onClick={() => choose(id)}>{label}</button>)}</div>
      <button onClick={() => {setRetry(r => r + 1); refresh?.();}}>Refresh usage</button></div>
      {state.error ? <p role="alert">Usage could not be loaded. <button onClick={() => setRetry(r => r + 1)}>Try again</button></p>
        : !data ? <p role="status">Reading local usage…</p> : <>
        <div className="usage-hero">
        <div className="usage-story">
        <div className="eyebrow">{moneyLabel(view)}<UsageBasis view={view} /></div>
        <div className="usage-headline">
          <div className="usage-total">{data.summary.unpriced_turns > 0 && data.summary.unpriced_turns >= data.summary.assistant_turns ? "Price unknown" : usdAbout(data.summary.cost_micro_usd)}</div>
          {compared ? <p className={`usage-delta ${compared.startsWith("up") ? "up" : compared.startsWith("down") ? "down" : "flat"}`}>{compared}</p> : null}
        </div>
        <BillingNote view={view} />
        <p className="muted">{data.from_day} to {data.to_day} · {count(data.summary.sessions)} sessions
          {" · "}{compact(data.summary.tokens_total)} recorded tokens</p>
        {incomplete ? <p className="usage-incomplete">{incomplete}</p> : null}
        {data.unresolved_forks > 0 ? <p className="usage-incomplete">{count(data.unresolved_forks)} forked sessions have unconfirmed inherited history. Their recorded totals may include replay.</p> : null}
        </div>
        {data.summary.assistant_turns ? <Trend key={period} series={data.series} previous={data.previous} /> : null}
        </div>
        {!data.summary.assistant_turns ? <p>No assistant usage is recorded for this period. Choose a longer period or refresh after using a supported harness.</p> : <>
          <details className="stat-details"><summary>By model<Info text="Prompt and output tokens over the period; reasoning counts once, inside output." /></summary>
            <p>{count(data.drivers.prompt_tokens)} prompt tokens · {count(data.drivers.output_tokens)} output tokens.</p>
            <p>{data.drivers.cache_pct != null ? `${data.drivers.cache_pct}% of recorded prompt tokens came from cache.` : "Cache reuse cannot be calculated without prompt usage."}</p>
            <div className="table-scroll"><table><thead><tr><th>Tool / model</th><th className="n">Turns</th><th className="n">Listed-rate estimate</th></tr></thead>
              <tbody>{data.summary.models.map(m => <tr key={m.tool + m.model}><td>{toolName(m.tool)} · {m.model}</td>
                <td className="n">{count(m.assistant_turns)}</td><td className="n">{pricedAmount(m)}</td></tr>)}</tbody></table></div>
          </details>
        </>}
      </>}
      <BillingSettings view={view} refresh={refresh} />
    </section>
    <AllowanceCard view={view} />

  </div>;
}
