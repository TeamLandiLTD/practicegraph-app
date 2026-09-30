import React, { useEffect, useRef, useState } from "react";
import { compact, convertedNote, count, setBillingMode, usd, usdAbout, centsAbout } from "./api.js";
import { Info } from "./Info.jsx";

const TOOLS = [["claude_code", "Claude Code"], ["codex", "Codex"]];
const MODES = [["unknown", "Not specified"], ["subscription", "Subscription"],
  ["api", "Pay per use (API)"], ["mixed", "Subscription and API"]];

export function toolKey(tool) {
  if (/codex/i.test(tool)) return "codex";
  if (/claude/i.test(tool)) return "claude_code";
  return tool;
}

export function billingMode(view, tool) {
  return view.billing?.by_tool?.[toolKey(tool)]
    || view.billing?.default_mode || view.economy?.billing_mode || "unknown";
}

export function moneyLabel(view, tool) {
  const modes = tool ? [billingMode(view, tool)]
    : TOOLS.map(([id]) => billingMode(view, id));
  return modes.every(mode => mode === "api")
    ? "Estimated API usage cost" : "Estimated value at API prices";
}

export function BillingNote({view}) {
  return <p className="billing-note">{TOOLS.every(([tool]) => billingMode(view, tool) === "api")
    ? "Estimated from recorded tokens. Your provider's bill may differ."
    : "This is a price comparison, not a subscription charge."}</p>;
}

export function UsageBasis({ view }) {
  return <Info className="usage-basis" text={`${moneyLabel(view)} at listed API rates — on a subscription `
    + "a comparison, not a charge; your bill may differ. The current day is still filling in."
    + (convertedNote() ? ` ${convertedNote()}` : "")} />;
}

// Unpriced turns are named on the face only when they move the total (one
// percent or more); below that the ⓘ already says the total is an estimate.
export function unpricedLine(unpriced, turns) {
  if (!unpriced || (turns != null && unpriced * 100 < turns)) return null;
  return `${count(unpriced)} turns have no listed price, so the total runs low.`;
}

export function BillingSettings({ view, refresh }) {
  // The save takes milliseconds; the view it feeds is rebuilt from the whole
  // history, which takes seconds on a long one and minutes during a first
  // scan. One shared busy flag used to disable BOTH dropdowns for that whole
  // rebuild, so a second click "did nothing". Show the choice at once and let
  // the reload catch up. Saves still run one at a time: the server rewrites
  // the whole per-tool map, so two in flight could undo each other.
  const [chosen, setChosen] = useState({});
  const [error, setError] = useState(false);
  const queue = useRef(Promise.resolve());
  useEffect(() => {
    // Drop a choice once the view reports it, so a later outside change shows.
    setChosen(current => {
      const open = Object.entries(current).filter(([tool, mode]) => billingMode(view, tool) !== mode);
      return open.length === Object.keys(current).length ? current : Object.fromEntries(open);
    });
  }, [view]);
  const save = (tool, mode) => {
    setChosen(current => ({ ...current, [tool]: mode })); setError(false);
    queue.current = queue.current.then(async () => {
      let saved = false;
      try { saved = await setBillingMode(mode, tool); } catch { saved = false; }
      if (!saved) {
        setError(true);
        setChosen(current => {
          if (current[tool] !== mode) return current;
          const { [tool]: _, ...rest } = current;
          return rest;
        });
        return;
      }
      refresh?.();
    });
  };
  return <details className="stat-details billing-settings">
    <summary>How model use is billed</summary>
    <p className="muted">How you pay for each tool. It changes the label, not the count.</p>
    <div className="billing-choices">{TOOLS.map(([tool, name]) => (
      <label key={tool}>{name}
        <select value={chosen[tool] ?? billingMode(view, tool)}
          onChange={event => save(tool, event.target.value)}>
          {MODES.map(([mode, label]) => <option key={mode} value={mode}>{label}</option>)}
        </select>
      </label>
    ))}</div>
    {error ? <p role="alert">That billing choice did not save. Try again.</p> : null}
  </details>;
}

export function AllowanceCard({ view, now = Date.now() }) {
  const buckets = view.runway?.buckets || [];
  if (!buckets.length) return null;
  const sourceName = source => source === "codex_cli" ? "Codex"
    : ["claude_code", "claude_code_cli"].includes(source) ? "Claude Code" : "Source not recorded";
  return <section className="card allowance">
    <div className="chead"><h2>Last recorded usage limits<Info text={"Snapshots your tools wrote to their own "
      + "logs, independent of the usage period below. Reset and account details show only when a log recorded them."} /></h2></div>
    <div className="allowance-readings">{buckets.map((b, index) => {
      const timestamp = Date.parse(b.observed_at);
      const valid = Number.isFinite(timestamp) && timestamp <= now;
      const recent = valid && now - timestamp <= 15 * 60_000;
      const label = b.bucket === "other" && b.window_minutes > 0
        ? `${count(b.window_minutes)}-minute window` : b.label;
      return <div className="allowance-row" key={`${b.source_id}-${b.window_minutes}-${index}`}>
        <div><strong>{sourceName(b.source_id)} · {label}</strong>
          <p className="muted">{valid
            ? <>observed <time dateTime={b.observed_at}>{new Date(timestamp).toLocaleString()}</time>
                {recent ? "" : " · older reading"}</>
            : "observation time unknown"}
            {b.resets_at && Number.isFinite(Date.parse(b.resets_at))
              ? <> · {Date.parse(b.resets_at) <= now ? "reset passed" : "resets"}{" "}
                  <time dateTime={b.resets_at}>{new Date(b.resets_at).toLocaleString()}</time></>
              : null}
            {b.account_hash ? ` · account ${b.account_hash.slice(0, 6)}` : ""}</p>
        </div>
        <div className="allowance-value">{b.latest_pct}% <span>used</span></div>
      </div>;
    })}</div>
  </section>;
}

export function UsageOverview({ view, refresh }) {
  const ranges = view.ranges || [];
  const [active, setActive] = useState("today");
  const range = ranges.find(r => r.range_id === active);
  const total = range || view.totals;
  return <section className="card usage-overview">
    <div className="chead"><h2>Usage by period</h2></div>
    <div className="tabs" role="group" aria-label="Usage period">
      {[{ range_id: "today", label: "Today" }, ...ranges].map(tab => (
        <button key={tab.range_id} className={active === tab.range_id ? "on" : ""}
          aria-pressed={active === tab.range_id} onClick={() => setActive(tab.range_id)}>{tab.label}</button>
      ))}
    </div>
    <p className="usage-label">{moneyLabel(view)}<UsageBasis view={view} /></p>
    <div className="usage-total">{usdAbout(total.cost_micro_usd)}</div>
    <BillingNote view={view} />
    <p className="muted">{range ? `${range.from_day} to ${range.to_day} · UTC accounting dates`
      : `UTC accounting day ${view.accounting_day_utc}`}</p>
    {unpricedLine(total.unpriced_turns, total.assistant_turns)
      ? <p className="usage-incomplete">{unpricedLine(total.unpriced_turns, total.assistant_turns)}</p> : null}
    <details className="stat-details"><summary>Usage details for {range?.label.toLowerCase() || "today"}</summary>
      <p>{usd(total.cost_micro_usd)} at listed rates
        {total.assistant_turns != null ? ` · ${count(total.assistant_turns)} assistant turns` : ""}
        {total.sessions != null ? ` · ${count(total.sessions)} sessions` : ""}
        {total.tokens_total != null ? ` · ${compact(total.tokens_total)} tokens` : ""}
        {range ? ` · ${range.active_days} days with recorded usage` : ""}</p>
      <p className="muted">Includes recorded agent and subagent usage. Counts describe activity, not completed work.</p>
      {range?.models?.length ? <div className="table-scroll"><table>
        <thead><tr><th>Tool / billing basis</th><th>Model</th><th className="n">Turns</th>
          <th className="n">Listed-rate estimate</th></tr></thead>
        <tbody>{range.models.map(row => <tr key={row.tool + row.model}>
          <td>{row.tool}<small className="table-note">{moneyLabel(view, row.tool)}</small></td>
          <td className="model">{row.model}</td><td className="n">{count(row.assistant_turns)}</td>
          <td className="n">{usd(row.cost_micro_usd)}</td></tr>)}</tbody>
      </table></div> : null}
    </details>
    <BillingSettings view={view} refresh={refresh} />
  </section>;
}

export function EconomyCard({ view }) {
  const eco = view.economy;
  if (!eco) return null;
  return <section className="card economy">
    <div className="chead"><h2>Usage drivers</h2></div>
    <p className="muted">Separate analysis · {eco.window_days} days ending {view.accounting_day_utc} (UTC).
      The period selector above applies to the usage total.</p>
    {eco.lever ? <div className="ecolever"><h3>{eco.lever.title}</h3><p>{eco.lever.body}</p>
      {eco.lever.id === "advisor" ? <a href="#advice">Read the guidance</a> : null}</div> : null}
    <details className="stat-details"><summary>Context, cache and reasoning details</summary>
      <p>{eco.cache_hit_pct}% of recorded prompt tokens came from cache.</p>
      <p>{centsAbout(eco.cost_per_priced_turn_micro_usd)} per priced assistant turn at listed rates.</p>
      {eco.reasoning ? <p>{eco.reasoning.line}</p> : null}
      {eco.churn ? <p>{eco.churn.line}</p> : null}
      {eco.wow_cost_delta_pct != null ? <p>Last 7 accounting days versus the preceding 7:
        estimated usage cost {eco.wow_cost_delta_pct === 0 ? "was unchanged"
          : `was ${Math.abs(eco.wow_cost_delta_pct)}% ${eco.wow_cost_delta_pct > 0 ? "higher" : "lower"}`}.
        Different work volumes and tasks can change this comparison.</p> : null}
      {view.sessions?.receipt ? <p>{view.sessions.receipt.line}</p> : null}
      {view.sessions?.tax ? <p>{view.sessions.tax.line}</p> : null}
    </details>
  </section>;
}

export function GroupedUsage({ view }) {
  const wu = view.work_units;
  if (!wu?.available) return null;
  return <details className="card stat-details grouped-usage">
    <summary>Grouped usage · experimental</summary>
    <p>Usage is grouped by project, branch and gaps in recorded activity.
      These groups are not confirmed tasks or completed deliverables.</p>
    <UsageBasis view={view} />
    <p>{count(wu.units)} groups in {wu.window_days} days.
      Median {usd(wu.median_cost_micro_usd)}; 90th percentile {usd(wu.p90_cost_micro_usd)}.</p>
    <p className="muted">The 90th percentile is the estimate at or below which roughly 90% of groups fall.</p>
    {wu.top?.length ? <div className="table-scroll"><table><thead><tr><th>Group started</th>
      <th className="n">Turns</th><th className="n">Listed-rate estimate</th></tr></thead><tbody>
      {wu.top.map((row, index) => <tr key={index}><td>{row.started_day}</td>
        <td className="n">{count(row.assistant_turns)}</td><td className="n">{usd(row.cost_micro_usd)}</td></tr>)}
    </tbody></table></div> : null}
  </details>;
}
