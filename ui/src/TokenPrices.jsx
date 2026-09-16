import React, {useEffect, useRef, useState} from "react";
import {fetchTokenPrices, refreshTokenPrices} from "./api.js";
import {providerName, pricedProviders, pricedEdition, hasBasePrices, summarizeModel, isFirstParty, developerBadge, cheaperHosts, lowestOffer, findDeals, providerList, PROVIDERS} from "./tokenRecommendations.js";

const FIELDS = ["input", "output", "cache_read", "cache_write"];
const LABELS = {input: "Input", output: "Output", cache_read: "Cache read", cache_write: "Cache write"};
const money = value => value == null ? "Unknown" : `$${String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1")}`;
const date = value => value ? new Date(value).toLocaleString("en-GB", {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"}) + " UTC" : "Not disclosed";
const known = value => value == null ? "Not disclosed" : String(value);

const WEIGHTS = {open: "open weights", closed: "closed weights"};
const band = o => o.context_band.min == null && o.context_band.max == null ? "" : `${o.context_band.min ?? 0}–${o.context_band.max ?? "and above"} input tokens`;
const ttl = o => o.pricing.cache_ttl_seconds == null ? "" : `${o.pricing.cache_ttl_seconds % 3600 === 0 ? `${o.pricing.cache_ttl_seconds / 3600} h` : `${Math.round(o.pricing.cache_ttl_seconds / 60)} min`} cache`;
const priceNote = summary => [summary.from ? `from ${providerName(summary.headline.provider)}` : "",
  summary.aliases ? summary.headline.api_model_id : "",
  summary.headline.service_tier !== "standard" ? `${summary.headline.service_tier} tier` : "", band(summary.headline), ttl(summary.headline),
  summary.tariffs > 1 ? `dearer of ${summary.tariffs} tariffs` : ""].filter(Boolean).join(" · ");

export function modelRows(edition, states, filters) {
  const price = (row, key) => Number(row.summary.headline.pricing[key]);
  return edition.models.filter(m => !filters.open || m.weights === "open").map(model => {
    const offers = edition.offers.filter(o => o.model_id === model.id && hasBasePrices(o)
      && (!filters.provider || o.provider === filters.provider)
      && (!filters.tier || o.service_tier === filters.tier)
      && (!filters.current || states[o.id] === "current"));
    // Developer first, then direct hosts, routers last; cheaper input first within each group.
    const group = o => isFirstParty(model, o) ? 0 : o.channel === "router" ? 2 : 1;
    offers.sort((a, b) => group(a) - group(b) || Number(a.pricing.input) - Number(b.pricing.input)
      || Number(a.pricing.output) - Number(b.pricing.output) || a.id.localeCompare(b.id));
    return {model, offers, summary: summarizeModel(model, offers), cheaper: cheaperHosts(model, offers), lowest: lowestOffer(model, offers)};
  }).filter(row => row.offers.length > 0).sort((a, b) => {
    const byName = a.model.developer.localeCompare(b.model.developer) || a.model.name.localeCompare(b.model.name) || a.model.id.localeCompare(b.model.id);
    if (filters.sort === "lowest") {
      const low = row => row.lowest.offer ? Number(row.lowest.offer.pricing.output) : Infinity;
      return low(a) - low(b) || byName;
    }
    if (filters.sort !== "input" && filters.sort !== "output") return byName;
    return price(a, filters.sort) - price(b, filters.sort) || byName;
  });
}


function ProviderDirectory({edition, onSelect}) {
  return <details className="token-coverage"><summary>Provider directory · {providerList(edition).length} providers</summary>
    <p>Developers, open-weight hosts, routers, and cloud platforms. Coverage status describes this edition; a missing quote does not mean a provider has no service.</p>
    <div className="token-provider-grid">{providerList(edition).map(id => {
      const coverage = edition.coverage.find(c => c.provider === id);
      const count = edition.offers.filter(o => o.provider === id).length;
      return <article key={id}><button className="link-button" onClick={() => onSelect(id)}>{providerName(id)}</button>
        <p>{PROVIDERS[id]?.[1] || "Provider"} · {count} offers · {coverage?.status.replaceAll("_", " ") || "Not yet audited"}</p>
        <small>{coverage?.notes || "No verified coverage record in this edition."}</small></article>;
    })}</div>
  </details>;
}


const DEAL_KIND = {batch: "asynchronous batch tier", "off-peak": "off-peak UTC window"};
function Deals({edition, states}) {
  const deals = findDeals(edition, states);
  if (!deals.undercuts.length && !deals.halfPrice.length && !deals.ending.length) return null;
  const pair = o => `${money(o.pricing.input)} / ${money(o.pricing.output)}`;
  const endDate = value => new Date(value).toLocaleDateString("en-GB", {year: "numeric", month: "short", day: "numeric", timeZone: "UTC"});
  return <section className="deals" aria-label="Deals right now">
    <h3>Deals right now <span className="chart-unit">computed from this edition</span></h3>
    <div className="deal-groups">
      {deals.undercuts.length ? <div className="deal-group"><h4>Hosts undercutting the developer</h4>
        <ul>{deals.undercuts.map(d => <li key={d.offer.id}><strong>{d.model.name}</strong> at {providerName(d.offer.provider)}: {pair(d.offer)}
          <small>output {d.output_saving}% less than {d.model.developer} · precision {known(d.offer.precision)} · context {known(d.offer.limits.context_tokens)}</small></li>)}</ul>
        {deals.undercutsTotal > deals.undercuts.length ? <p className="deal-more">{deals.undercutsTotal - deals.undercuts.length} more flagged in the table.</p> : null}</div> : null}
      {deals.halfPrice.length ? <div className="deal-group"><h4>Half-price windows</h4>
        <ul>{deals.halfPrice.map(d => <li key={d.offer.id}><strong>{d.model.name}</strong> at {providerName(d.offer.provider)}: {pair(d.offer)}
          <small>{DEAL_KIND[d.kind]} · {d.output_saving}% below the standard {pair(d.reference)}</small></li>)}</ul>
        {deals.halfPriceTotal > deals.halfPrice.length ? <p className="deal-more">{deals.halfPriceTotal - deals.halfPrice.length} more in the table: filter the service tier to batch.</p> : null}</div> : null}
      {deals.ending.length ? <div className="deal-group"><h4>Prices with an announced end</h4>
        <ul>{deals.ending.map(d => <li key={d.offer.id}><strong>{d.model.name}</strong> at {providerName(d.offer.provider)}: {pair(d.offer)}
          <small>until {endDate(d.offer.effective_to)} · {d.days_left} days left</small></li>)}</ul></div> : null}
    </div>
    <p className="muted">Batch is asynchronous, off-peak follows the provider's clock, and a cheaper host may serve a different precision or context limit. Check the offer's terms before switching.</p>
  </section>;
}

function Sources({ids, sources}) {
  return <ul className="token-sources">{[...new Set(ids)].map(id => {
    const s = sources[id];
    return s ? <li key={id}><a href={s.url} target="_blank" rel="noreferrer">{s.publisher}: {s.title} ↗</a>
      <span> · checked {date(s.checked_at)}</span></li> : null;
  })}</ul>;
}

function OfferDetails({offer: o, model, sources, history, change, expanded = false}) {
  const records = [...history.map(s => ({at: s.observed_at, offer: s.offers.find(x => x.id === o.id)})),
    {at: o.observed_at, offer: o}];
  return <details className="token-details" open={expanded || undefined}><summary>Terms, sources & history</summary>
    <dl className="token-facts">
      <div><dt>API model ID</dt><dd>{o.api_model_id}</dd></div>
      <div><dt>Model revision</dt><dd>{known(model.revision)}</dd></div>
      <div><dt>Route</dt><dd>{o.channel}{o.upstream_provider ? ` via ${o.upstream_provider}` : ""}</dd></div>
      <div><dt>Precision</dt><dd>{known(o.precision)}</dd></div>
      <div><dt>Region</dt><dd>{known(o.region)}</dd></div>
      <div><dt>Input pricing band</dt><dd>{known(o.context_band.min)} – {known(o.context_band.max)} tokens</dd></div>
      <div><dt>Context / output limit</dt><dd>{known(o.limits.context_tokens)} / {known(o.limits.output_tokens)}</dd></div>
      <div><dt>Tools / structured output / vision</dt><dd>{known(o.capabilities.tools)} / {known(o.capabilities.structured_outputs)} / {known(o.capabilities.vision)}</dd></div>
      <div><dt>Cache TTL</dt><dd>{known(o.pricing.cache_ttl_seconds)}{o.pricing.cache_ttl_seconds != null ? " seconds" : ""}</dd></div>
      <div><dt>Reasoning billing</dt><dd>{o.pricing.reasoning_billing.replaceAll("_", " ")}</dd></div>
      <div><dt>Effective from / until</dt><dd>{date(o.effective_from)} / {date(o.effective_to)}</dd></div>
    </dl>
    <p>{o.pricing.conditions}</p><p>{o.notes}</p>
    {o.pricing.extra_charges.length ? <ul>{o.pricing.extra_charges.map((fee, i) => <li key={i}>
      {fee.name}: {money(fee.amount)} {fee.unit}. {fee.conditions}</li>)}</ul> : null}
    <p>Training use: {o.terms.training_use.replaceAll("_", " ")} · Retention: {known(o.terms.retention_days)} days · Zero retention: {known(o.terms.zero_retention)}. {o.terms.notes}</p>
    <Sources ids={[...model.source_ids, ...o.source_ids, ...o.pricing.source_ids, ...o.terms.source_ids]} sources={sources} />
    <h4>Observed price history</h4>
    {history.length === 0 ? <p>First observation. Price changes will appear after another verified edition.</p> : null}
    {change?.conditions_changed ? <p className="token-caution">Billing conditions also changed. These prices may not be directly comparable.</p> : null}
    <div className="table-scroll"><table><thead><tr><th>Checked</th>{FIELDS.map(f => <th key={f}>{LABELS[f]}</th>)}</tr></thead>
      <tbody>{records.map((r, i) => <tr key={i}><td>{date(r.offer?.observed_at || r.at)}</td>
        {r.offer ? FIELDS.map(f => <td key={f}>{money(r.offer.pricing[f])}</td>) : <td colSpan={4}>Not reverified in this edition</td>}</tr>)}</tbody></table></div>
    <p className="muted">USD per million tokens. Observations show when we checked; they do not establish when a provider changed its price.</p>
  </details>;
}

export default function TokenPrices() {
  const [data, setData] = useState(null), [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [expanded, setExpanded] = useState("");
  // Row density is a per-reader convenience; it never changes what is shown.
  const [compact, setCompact] = useState(() => { try { return localStorage.getItem("pg-density") === "compact"; } catch { return false; } });
  const toggleCompact = value => { setCompact(value); try { localStorage.setItem("pg-density", value ? "compact" : "comfortable"); } catch {} };
  const requestGeneration = useRef(0);
  const download = async () => {
    const generation = ++requestGeneration.current;
    setBusy(true); setNotice("");
    try {
      const value = await refreshTokenPrices();
      if (requestGeneration.current !== generation) return;
      setData(value); setError("");
      const result = value.refresh_result;
      setNotice(result === "pulled" ? "Hosted edition downloaded and validated. Provider verification dates are unchanged."
        : result === "skipped_recently" ? "Please wait 30 seconds between download checks."
        : result === "skipped_enterprise_configured" ? "Your organisation manages catalogs. No public catalog is configured."
        : result === "skipped_unconfigured" ? "No catalog source is configured."
        : "The hosted edition could not be downloaded or validated. Your last accepted edition is preserved.");
    } catch {setNotice("Download failed. Your last accepted edition is preserved. Try again.");}
    finally {setBusy(false);}
  };
  const [filters, setFilters] = useState({provider: "", open: false, tier: "", current: false, sort: "model"});
  useEffect(() => {
    let active = true;
    const load = async () => {
      const generation = requestGeneration.current;
      try { const value = await fetchTokenPrices(); if (active && requestGeneration.current === generation) { setData(value); setError(""); } }
      catch { if (active && requestGeneration.current === generation) setError("Prices could not be loaded. Your last edition remains available below."); }
    };
    load();
    const timer = setInterval(load, 60000);
    return () => { active = false; clearInterval(timer); };
  }, [retry]);
  const set = (key, value) => setFilters(f => ({...f, [key]: value}));
  const edition = data?.edition ? pricedEdition(data.edition) : null;
  const rows = edition ? modelRows(edition, data.offer_states, filters) : [];
  const shown = rows.reduce((n, r) => n + r.offers.length, 0);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const sources = Object.fromEntries((edition?.sources || []).map(s => [s.id, s]));
  const changes = Object.fromEntries((data?.changes || []).map(c => [c.offer_id, c]));
  return <section className="card token-prices" aria-label="Token prices">
    <header className="token-heading"><h2 className="section-title">API token prices</h2>
      <span className="token-unit">USD / 1M tokens</span></header>
    <p>What each model costs at the API, and who hosts it.</p>
    {error ? <p role="alert">{error} <button onClick={() => setRetry(r => r + 1)}>Retry</button></p> : null}
    <div className="download-status">
      <button disabled={busy} onClick={download}>{busy ? "Checking hosted edition…" : "Check for a newer edition"}</button>
      {data?.download?.feed?.checked_at ? <p>Last validated download: {date(data.download.feed.checked_at)}</p> : null}
      {data?.download?.last_attempt ? <p>Last manual attempt: {date(data.download.last_attempt)} · {data.download.result === "pulled" ? "validated" : "not updated"}</p> : null}
      {data?.download?.scheduled_attempt ? <details><summary>Automatic download checks</summary><p>Last attempt: {date(data.download.scheduled_attempt)}. Checks run about every 15 minutes while the agent is running.</p></details> : null}
      {notice ? <p role="status">{notice}</p> : null}
    </div>
    {!data && !error ? <p role="status">Loading token prices…</p> : null}
    {data && !edition ? <p>No verified price edition is available yet. The app will pick one up from its configured catalog when available.</p> : null}
    {edition ? <>
      <div className="token-stats"><span><strong>{edition.models.length}</strong> models</span><span><strong>{new Set(edition.offers.map(o => o.provider)).size}</strong> providers</span>
        <span><strong>{edition.offers.length}</strong> offers</span><span>Prices checked {date(edition.observed_at)}</span></div>
      {Object.values(data.offer_states).some(s => s === "stale") ? <p className="price-freshness">Some prices were last checked more than 48 hours ago. Comparisons remain available; confirm the provider rate before buying.</p> : null}
      <ProviderDirectory edition={edition} onSelect={provider => {setExpanded(""); setFilters(f => ({...f, provider, tier: "", open: false, current: false}));}} />
      <Deals edition={edition} states={data.offer_states} />
      <h3 id="token-offer-board">Prices by model</h3>
      <div className="token-filters">
        <label>Provider<select value={filters.provider} onChange={e => set("provider", e.target.value)}><option value="">All providers</option>
          {pricedProviders(edition).map(p => <option key={p} value={p}>{providerName(p)}</option>)}</select></label>
        <label>Service tier<select value={filters.tier} onChange={e => set("tier", e.target.value)}><option value="">All tiers</option>
          {[...new Set(edition.offers.map(o => o.service_tier))].sort().map(t => <option key={t} value={t}>{t}</option>)}</select></label>
        <label>Sort by<select value={filters.sort} onChange={e => set("sort", e.target.value)}><option value="model">Developer and model</option>
          <option value="input">Input price</option><option value="output">Output price</option><option value="lowest">Lowest output price</option></select></label>
        <label className="token-check"><input type="checkbox" checked={filters.open} onChange={e => set("open", e.target.checked)} />Open weights only</label>
        <label className="token-check"><input type="checkbox" checked={filters.current} onChange={e => set("current", e.target.checked)} />Recently verified, active offers only</label>
        <label className="token-check"><input type="checkbox" checked={compact} onChange={e => toggleCompact(e.target.checked)} />Compact rows</label>
      </div>
      <p className="token-caution">Matching model names do not guarantee the same precision, limits, speed, or service. Check the offer details before switching. Unknown prices are never treated as free.</p>
      <p className="muted">{plural(rows.length, "model")} · {plural(shown, "offer")} shown · Standard API rates are separate from your Usage cost estimates.</p>
      {rows.length === 0 ? <p>No models match these filters. Try another provider or tier.</p> : null}
      <div className="table-scroll"><table className={`token-table${compact ? " compact" : ""}`} aria-label="API prices by model"><caption>USD per million tokens. One row per model. The headline is the developer's own standard price, or the lowest direct host price marked "from".</caption>
        <thead><tr><th>Model</th>{FIELDS.map(f => <th key={f}>{LABELS[f]}</th>)}<th>Lowest</th><th>Hosts</th><th>Verification</th></tr></thead>
        <tbody>{rows.map(({model, offers, summary, cheaper, lowest}) => {
          const h = summary.headline, open = expanded === model.id;
          return <React.Fragment key={model.id}><tr id={`token-model-${model.id}`}>
          <td><div className="model-cell"><span className={`dev-badge tone-${developerBadge(model.developer).tone}`} aria-hidden="true">{developerBadge(model.developer).initials}</span>
            <div><strong>{model.name}</strong><small>{model.developer}{WEIGHTS[model.weights] ? ` · ${WEIGHTS[model.weights]}` : ""}</small><small>{priceNote(summary)}</small>
            {cheaper.length ? <small className="cheaper-line">cheaper at {providerName(cheaper[0].offer.provider)} · output {cheaper[0].output_saving}% less{cheaper.length > 1 ? ` · ${cheaper.length - 1} more` : ""}</small> : null}</div></div></td>
          {FIELDS.map(f => <td key={f}>{money(h.pricing[f])}</td>)}
          <td data-testid="lowest" className={lowest.offer && !lowest.same_as_developer && lowest.developer ? "lowest-cell better" : "lowest-cell"}>{lowest.offer ? <>
            <span className="lowest-pair">{money(lowest.offer.pricing.input)} / {money(lowest.offer.pricing.output)}</span>
            <small>at {providerName(lowest.offer.provider)}{lowest.developer ? (lowest.same_as_developer ? " · the developer's own" : ` · output ${lowest.output_saving}% less`) : ""}</small>
          </> : <small>no direct standard offer</small>}</td>
          <td><button className="link-button" aria-expanded={open} onClick={() => setExpanded(open ? "" : model.id)}>{summary.hosts.length === 1 ? `${providerName(summary.hosts[0])} only` : `${summary.hosts.length} hosts`}</button>
            <small>{plural(offers.length, "offer")}{summary.inputRange && Number(summary.inputRange[0]) !== Number(summary.inputRange[1]) ? ` · input ${money(summary.inputRange[0])}–${money(summary.inputRange[1])}` : ""}</small>
            {cheaper.length ? <span className="cheaper-chip">{cheaper.length === 1 ? "1 host cheaper" : `${cheaper.length} hosts cheaper`}</span> : null}</td>
          <td>{data.offer_states[h.id] === "stale" ? "Needs recheck" : data.offer_states[h.id]}<small>{h.observed_at.slice(0,10)}</small></td>
        </tr>{open ? <tr className="token-detail-row"><td colSpan={8}>
          <table className="token-offers" aria-label={`Offers for ${model.name}`}>
            <thead><tr><th>Host</th><th>Tier</th><th>Precision</th><th>Input band</th>{FIELDS.map(f => <th key={f}>{LABELS[f]}</th>)}<th>Verification</th></tr></thead>
            <tbody>{offers.map(o => { const flag = cheaper.find(c => c.offer.id === o.id); return <React.Fragment key={o.id}><tr className={flag ? "cheaper" : undefined}>
              <td><strong>{providerName(o.provider)}</strong><small>{o.upstream_provider ? `${o.upstream_provider} endpoint` : o.channel}{isFirstParty(model, o) ? " · developer" : ""}{flag ? ` · input ${flag.input_saving}% and output ${flag.output_saving}% below the developer` : ""}</small></td>
              <td>{o.service_tier}{ttl(o) ? <small>{ttl(o)}</small> : null}</td>
              <td>{known(o.precision)}</td>
              <td>{band(o) || "All bands"}</td>
              {FIELDS.map(f => <td key={f}>{money(o.pricing[f])}{changes[o.id]?.rates[f] ? <small>Previously {money(changes[o.id].rates[f].before)}</small> : null}</td>)}
              <td>{data.offer_states[o.id] === "stale" ? "Needs recheck" : data.offer_states[o.id]}<small>{o.observed_at.slice(0,10)}</small></td>
            </tr><tr className="token-evidence-row"><td colSpan={9}>
              <OfferDetails offer={o} model={model} sources={sources} history={edition.history} change={changes[o.id]} />
            </td></tr></React.Fragment>; })}</tbody>
          </table>
        </td></tr> : null}</React.Fragment>;})}</tbody>
      </table></div>
      <details className="token-coverage"><summary>Coverage & methodology</summary>
        <p>{edition.scope.model_selection}</p><p>{edition.scope.provider_selection}</p><p>{edition.scope.coverage_notes}</p>
        <p>Quotes and their pricing sources older than 48 hours carry a freshness notice, without blocking comparisons. The app checks the hosted edition periodically; loading this page does not reverify provider prices.</p>
        <ul>{edition.coverage.map(c => <li key={c.provider}><strong>{providerName(c.provider)}</strong> · {c.status.replaceAll("_", " ")}: {c.notes}</li>)}</ul>
        <p>Edition {edition.edition_version} · {edition.history.length} prior editions retained in this feed.</p>
      </details>
    </> : null}
  </section>;
}
