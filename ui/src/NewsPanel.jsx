import React, {useEffect, useState} from "react";
import {changeNews, fetchNews} from "./api.js";

export function NewsUrgency({attention}) {
  if (!attention || !["important", "urgent"].includes(attention.urgency)) return null;
  const now = Date.now();
  if (!(Date.parse(attention.starts_at) <= now && now < Date.parse(attention.expires_at))) return null;
  return <span className={`news-urgency news-urgency-${attention.urgency}`} title={attention.reason}>
    {attention.urgency === "urgent" ? "Urgent" : "Important"}
  </span>;
}

function useNews() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    try { setData(await fetchNews()); setError(""); }
    catch { setError("News could not be loaded. Try again."); }
  };
  useEffect(() => {
    let current = true;
    const poll = async () => {
      try { const next = await fetchNews(); if (current) { setData(next); setError(""); } }
      catch { if (current) setError("News could not be loaded. Try again."); }
    };
    poll();
    const timer = setInterval(poll, 30000);
    return () => { current = false; clearInterval(timer); };
  }, []);
  const run = async body => {
    setBusy(true);
    try { setData(await changeNews(body)); setError(""); }
    catch { setError("Your choice could not be saved. Try again."); }
    finally { setBusy(false); }
  };
  return {data, error, busy, refresh, run};
}

function Settings({data, busy, run}) {
  if (!data?.settings) return <p role="status">Loading notification settings…</p>;
  const s = data.settings;
  return <div className="news-settings">
    <label>Notify me about
      <select disabled={busy} value={s.mode} onChange={e => run({action: "settings", mode: e.target.value, urgent_popup: s.urgent_popup})}>
        <option value="important">Important and urgent news</option>
        <option value="urgent">Urgent news only</option>
        <option value="off">No news notifications</option>
      </select>
    </label>
    <label className="news-popup-choice"><input type="checkbox" checked={s.urgent_popup} disabled={busy}
      onChange={e => run({action: "settings", mode: s.mode, urgent_popup: e.target.checked})} />
      Open a small window for urgent news</label>
    <p>Quiet hours apply. At most three alerts in 24 hours, at least an hour apart. Automatic windows keep your keyboard focus where it is.</p>
    {s.snoozed_until * 1000 > Date.now() ? <p role="status">News alerts are snoozed.
      <button disabled={busy} onClick={() => run({action: "snooze", minutes: 0})}>Resume alerts</button></p>
      : <div className="news-actions"><button disabled={busy} onClick={() => run({action: "snooze", minutes: 60})}>Snooze 1 hour</button>
        <button disabled={busy} onClick={() => run({action: "snooze", minutes: 1440})}>Snooze 24 hours</button></div>}
  </div>;
}

function SettingsLoader() {
  const news = useNews();
  return <>{news.error ? <p role="alert">{news.error} <button onClick={news.refresh}>Retry</button></p> : null}
    <Settings {...news} /></>;
}

export function NewsPreferences() {
  const [open, setOpen] = useState(false);
  return <details className="news-preferences" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>News notifications</summary>{open ? <SettingsLoader /> : null}
  </details>;
}

export default function NewsPanel() {
  const news = useNews();
  const {data, error, busy, run, refresh} = news;
  const [copyStatus, setCopyStatus] = useState("");
  const copy = async url => {
    try { await navigator.clipboard.writeText(url); setCopyStatus("Story link copied."); }
    catch { setCopyStatus("Could not copy. Open the source to copy its address."); }
  };
  return <main className="news-panel" aria-label="Latest news">
    <header><div className="eyebrow">PracticeGraph</div><h1>Latest news</h1>
      <p>{data ? `${data.unread_count} unread` : "Opening the latest edition…"}</p></header>
    {error ? <p role="alert">{error} <button onClick={refresh}>Retry</button></p> : null}
    {data?.quiet_hours ? <p className="news-quiet">Quiet hours · alerts will wait.</p> : null}
    {data?.items?.length === 0 ? <p>No news edition is available yet.</p> : null}
    <div className="news-panel-stories">
      {data?.items?.map(item => {
        const url = /^https:\/\//i.test(item.url || "") ? item.url : null;
        return <article key={item.id} className={`news-panel-story${item.unread ? " is-unread" : ""}`}>
          <div className="news-panel-meta"><NewsUrgency attention={item.attention} />
            <span>{item.source}</span>{item.unread ? <span className="news-unread">Unread</span> : null}</div>
          <h2>{item.title}</h2><p>{item.hook}</p>
          {item.attention_active ? <p className="news-attention-reason">{item.attention.reason}</p> : null}
          <details><summary>Read the summary</summary><p>{item.summary}</p><p>{item.why}</p></details>
          <div className="news-actions">
            {url ? <a href={url} target="_blank" rel="noreferrer" onClick={() => run({action: "read", id: item.id})}>Open source ↗</a> : null}
            {item.unread ? <button disabled={busy} onClick={() => run({action: "read", id: item.id})}>Mark read</button> : null}
            {url ? <button onClick={() => copy(url)}>Copy link</button> : null}
          </div>
        </article>;
      })}
    </div>
    {copyStatus ? <p role="status">{copyStatus}</p> : null}
    <details className="news-preferences"><summary>Notification settings</summary><Settings {...news} /></details>
  </main>;
}
