import { useCallback, useEffect, useRef, useState } from "react";
import { changeTraining, fetchTraining } from "./api.js";
import { ContentStatus } from "./ContentStatus.jsx";
import { Info } from "./Info.jsx";

const ERRORS = {
  training_edition_changed: "The training edition changed. Reload to review the current choices.",
  training_choice_unavailable: "That choice is no longer available. Finish the current item or review your preferences.",
  training_history_full: "Your learning record is full. Export it before clearing it.",
  training_state_invalid: "The local learning record could not be read. Your other practice records are unaffected.",
  invalid_training_progress: "This item changed in another window. Reload its current state.",
};
const credential = {none: "No credential", completion_badge: "Completion badge", completion_certificate: "Completion certificate",
  professional_certification: "Professional certification"};
const progress = {saved: "Saved", in_progress: "In progress", completed: "Completed · self-reported", dismissed: "Set aside"};
const emptyMessages = {
  catalog_unavailable: "A current training selection is not available. Your saved learning record is still below; check again after the next catalog update.",
  no_coverage: "This edition has no current resources for your chosen goal. Choose another goal or check again after a new edition.",
  filters: "No current resource matches these preferences. Try another tool or a broader cost or time limit.",
  already_recorded: "The matching resources are already in your learning record below. You can choose a set-aside item again or check back after a new edition.",
};

function safeLink(value) {
  try {const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password ? value : null;}
  catch {return null;}
}
function Link({url, children}) {
  const href = safeLink(url);
  return href ? <a href={href} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>;
}

export function LearningResource({entry, children}) {
  return <article className="learning-resource">
    <h3>{entry.title}</h3>
    <p className="muted">{entry.provider} · {credential[entry.credential]} · {entry.cost === "free" ? "Free" : entry.cost === "paid" ? "Paid" : "Cost unverified"}</p>
    <p>{entry.summary}</p>
    <p><strong>Why this fits your chosen goal:</strong> {entry.why}</p>
    <p>{entry.duration_minutes == null ? "Time commitment unverified" : `${entry.duration_minutes} minutes total · ${entry.duration_basis === "provider" ? "provider estimate" : "editorial estimate"}`}.</p>
    <p>{entry.cost_note}</p>
    <details className="stat-details"><summary>Steps, prerequisites and evidence</summary>
      <p>Prerequisites: {entry.prerequisites}</p>
      <ol>{entry.steps.map((step, i) => <li key={i}><Link url={step.url}>{step.title}</Link><p>{step.outcome}</p></li>)}</ol>
      <p>{entry.limitations}</p>
      <p>Reviewed {entry.reviewed_on}. Check current fees and requirements with the provider.</p>
      <ul>{entry.sources.map((url, i) => <li key={url}><Link url={url}>Source {i + 1}</Link></li>)}</ul>
    </details>
    <div className="capability-actions"><Link url={entry.url}>View at provider</Link>{children}</div>
  </article>;
}

export function useLearningPaths(enabled = true) {
  const [data, setData] = useState(null), [prefs, setPrefs] = useState(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const mounted = useRef(false), pending = useRef(false), generation = useRef(0);
  const accept = useCallback(result => {
    if (!mounted.current || result?.schema !== "practicegraph.learning/1") return;
    setData(result);
    setPrefs(current => current ?? result.prefs);
  }, []);
  const reload = useCallback(async () => {
    const revision = generation.current;
    try {
      const result = await fetchTraining();
      if (mounted.current && revision === generation.current && !pending.current) {accept(result); setError("");}
    } catch (e) {if (mounted.current && revision === generation.current) setError(ERRORS[e.message] || "Learning choices could not be loaded. Try again.");}
  }, [accept]);
  useEffect(() => {
    mounted.current = true;
    if (!enabled) return () => {mounted.current = false;};
    reload();
    const timer = setInterval(() => {if (!pending.current) reload();}, 60000);
    return () => {mounted.current = false; generation.current += 1; clearInterval(timer);};
  }, [reload, enabled]);
  const run = async body => {
    if (pending.current) return;
    pending.current = true; generation.current += 1; setBusy(true); setError("");
    try {
      const result = await changeTraining(body); accept(result);
      if (mounted.current && ["preferences", "clear"].includes(body.action)) setPrefs(result.prefs);
    } catch (e) {if (mounted.current) setError(ERRORS[e.message] || "That did not save. Try again.");}
    finally {pending.current = false; generation.current += 1; if (mounted.current) setBusy(false);}
  };
  const download = async () => {
    try {
      const backup = await fetchTraining(true);
      const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], {type: "application/json"}));
      const link = document.createElement("a"); link.href = url; link.download = "practicegraph-learning.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {setError("The learning record could not be exported. Try again.");}
  };
  return {data, prefs, setPrefs, busy, error, run, reload, download};
}

export function LearningPaths({controller, standalone = false}) {
  const local = useLearningPaths(!controller);
  const {data, prefs, setPrefs, busy, error, run, reload, download} = controller || local;
  const active = data?.records.find(r => ["saved", "in_progress"].includes(r.state));
  const history = data?.records.filter(r => ["completed", "dismissed"].includes(r.state)) || [];
  const Container = standalone ? "section" : "details";
  return <Container className="card learning-paths">
    {!standalone ? <summary>Learning paths{active ? ` · ${progress[active.state]}` : " · optional"}</summary> : null}
    <p>Choose something you want to learn.<Info text={"Suggestions follow your choices; activity logs are never read as "
      + "a skill gap. Progress is your own report, kept on this device; finishing a resource adds no practice hours "
      + "and certifies nothing."} /></p>
    {data && prefs ? <>
      <ContentStatus status={data.feed} label="Training selection" />
      <form className="learning-preferences" onSubmit={e => {e.preventDefault(); run({action: "preferences", ...prefs});}}>
        <label className="practice-field">Learning goal<select value={prefs.goal} disabled={busy}
          onChange={e => setPrefs({...prefs, goal: e.target.value})}>
          <option value="">Choose a goal</option>{Object.entries(data.goals).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select></label>
        <label className="practice-field">Coding tool<select value={prefs.tool} disabled={busy}
          onChange={e => setPrefs({...prefs, tool: e.target.value})}>
          {Object.entries(data.tools).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select></label>
        <label className="practice-field">Total time commitment<select value={prefs.max_minutes ?? ""} disabled={busy}
          onChange={e => setPrefs({...prefs, max_minutes: e.target.value ? Number(e.target.value) : null})}>
          <option value="">Any duration</option><option value="60">Up to 1 hour</option><option value="240">Up to 4 hours</option><option value="1200">Up to 20 hours</option>
        </select></label>
        <label><input type="checkbox" checked={prefs.free_only} disabled={busy}
          onChange={e => setPrefs({...prefs, free_only: e.target.checked})} /> Free resources only</label>
        <button type="submit" disabled={busy}>Save learning preferences</button>
      </form>
      {active ? <>
        <p role="status">Your selected next step · {progress[active.state]}</p>
        {active.availability !== "current" ? <p role="status">This saved guidance is {active.availability}. Check the provider before continuing; your earlier record is preserved.</p> : null}
        <LearningResource entry={active.entry}>
          <button disabled={busy} onClick={() => run({action: "progress", id: active.entry.id, state: active.state === "saved" ? "in_progress" : "completed"})}>
            {active.state === "saved" ? "I started this" : "I completed this"}</button>
          <button disabled={busy} onClick={() => run({action: "progress", id: active.entry.id, state: "dismissed"})}>Set aside</button>
        </LearningResource>
      </> : data.suggestion ? <>
        <LearningResource entry={data.suggestion}><button disabled={busy}
          onClick={() => run({action: "save", id: data.suggestion.id, edition: data.feed.version})}>Choose this next step</button></LearningResource>
        {data.alternatives.length ? <details className="stat-details"><summary>Other matching options</summary>{data.alternatives.map(entry =>
          <LearningResource key={entry.id} entry={entry}><button disabled={busy}
            onClick={() => run({action: "save", id: entry.id, edition: data.feed.version})}>Choose this next step</button></LearningResource>)}</details> : null}
      </> : data.prefs.goal ? <div role="status"><p>{emptyMessages[data.empty_reason] || emptyMessages.filters}</p>
        {data.empty_reason === "filters" ? <><button disabled={busy} onClick={() => setPrefs({...prefs, tool: "any", max_minutes: null, free_only: false})}>Broaden tool, time and cost filters</button>
          <p className="muted">Review the fields above, then save to apply. Your saved preferences remain unchanged until then.</p></> : null}
      </div> : null}
      <details className="stat-details"><summary>Your learning record</summary>
        {history.length ? history.map(r => <details className="stat-details" key={r.entry.id}>
          <summary>{r.entry.title} · {progress[r.state]} · {r.updated_on}</summary>
          {r.availability !== "current" ? <p>This saved guidance is {r.availability}. Check the provider before continuing; your earlier record is preserved.</p> : null}
          <LearningResource entry={r.entry}>
            {r.state === "dismissed" ? <button disabled={busy || !!active}
              onClick={() => run({action: "resume", id: r.entry.id})}>Choose again</button> : null}
          </LearningResource>
          {r.state === "dismissed" && active ? <p>Finish or set aside your selected step before choosing another.</p> : null}
        </details>) : <p>No completed or set-aside learning items yet.</p>}
        <p>Export is a readable copy; this version does not restore it.</p>
        <div className="capability-actions"><button disabled={busy} onClick={download}>Export learning record</button>
          <button disabled={busy} onClick={() => {if (window.confirm("Clear learning preferences and progress from this device? Practice hours are kept separately.")) run({action: "clear", confirm: true});}}>Clear learning record</button></div>
      </details>
    </> : !error ? <p role="status">Loading learning choices…</p> : null}
    {error ? <div role="alert"><p>{error}</p><button disabled={busy} onClick={reload}>Reload learning choices</button></div> : null}
  </Container>;
}
