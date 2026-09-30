import React, {useEffect, useState} from "react";

const STORE_KEY = "pg-practice-brief-v1";
const validNotes = n => n && ["practice", "task", "check", "result"].every(k => typeof n[k] === "string") && typeof n.finished === "boolean";
const blank = () => ({practice: "", task: "", check: "", result: "", finished: false});

// A timer or a previously accepted choice takes precedence over a new suggestion.
export function currentPractice(view, time, learning) {
  if (time?.active) return {key: `timer:${time.active.id}`, panel: "hours",
    title: time.skills?.find(s => s.id === time.active.skill)?.label || "Your practice session",
    body: time.active.running ? "Your practice timer is running." : "Your practice timer is paused."};
  const coach = view.capability;
  const practice = coach?.practice_result || coach?.practice_progress;
  if (practice) return {key: `coach:${practice.practice_id || practice.title}`, panel: "coach",
    title: practice.title, body: practice.practice || practice.line};
  const course = learning?.records?.find(r => ["saved", "in_progress"].includes(r.state));
  if (course) return {key: `learning:${course.entry.id}`, panel: "learning",
    title: course.entry.title, body: course.availability === "current" ? "Your selected learning resource."
      : `This saved resource is ${course.availability}. Check its current status before continuing.`};
  return null;
}

function PracticeBrief({current, onContinue, suggestion, onSuggestion}) {
  const scope = current?.key || "personal";
  const [notes, setNotes] = useState(blank);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [history, setHistory] = useState([]);
  const [ready, setReady] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [queued, setQueued] = useState(null);
  useEffect(() => {
    try {
      const value = JSON.parse(localStorage.getItem(`${STORE_KEY}:${scope}`) || "null");
      if (value && (!validNotes(value.notes) || !Array.isArray(value.history)
        || !value.history.every(entry => validNotes(entry) && typeof entry.day === "string"))) throw new Error();
      let initial = value?.notes || blank();
      const incoming = sessionStorage.getItem("pg-build-practice");
      // Never overwrite an existing saved plan. Keep the incoming idea available separately.
      if (incoming) {
        let idea;
        try {idea = JSON.parse(incoming);} catch {idea = null;}
        if (typeof idea?.title === "string" && typeof idea?.task === "string") {
          setQueued(idea);
          if (!current && !initial.practice && !initial.task && !initial.check && !initial.result) {
          initial = {...blank(), practice: idea.title, task: idea.task};
          setPlanning(true);
          }
        }
      }
      setNotes(initial); setHistory(value?.history || []);
      if (initial.practice || initial.task || initial.result) setPlanning(true);
      setSaved(false); setError(""); setReady(true);
    } catch {setError("Practice notes could not be read. Reload before editing them."); setReady(false);}
  }, [scope]);
  const update = (key, value) => {setNotes(n => ({...n, [key]: value})); setSaved(false);};
  const persist = (next, entries = history) => {
    try {
      localStorage.setItem(`${STORE_KEY}:${scope}`, JSON.stringify({notes: next, history: entries}));
      setNotes(next); setHistory(entries); setSaved(true); setError("");
      if (!current && queued?.title === next.practice) {
        try {sessionStorage.removeItem("pg-build-practice"); setQueued(null);} catch { /* Saved notes remain valid. */ }
      }
      return true;
    } catch {setError("Practice notes could not be saved on this device. Keep this page open and try again."); return false;}
  };
  return <section className="card practice-brief">
    <div className="eyebrow">{current ? "Your current practice" : "Your practice plan"}</div>
    <h2>{current?.title || notes.practice || suggestion?.title || "Choose a small change to try"}</h2>
    <ol className="practice-sequence" aria-label="Practice steps"><li>Choose</li><li>Try on a task</li><li>Review the result</li></ol>
    {current ? <p>{current.body}</p> : <p>{notes.practice ? "Continue your saved plan, then record what passed your check." : suggestion?.body || "Choose a suggestion, a learning resource, or one change you want to test on your next task."}</p>}
    {!current && !notes.practice && suggestion ? <button className="primary" onClick={onSuggestion}>{suggestion.action}</button> : null}
    {!planning ? <button onClick={() => setPlanning(true)}>{current ? "Add practice notes" : "Plan my own practice"}</button> : null}
    {current ? <button className="primary" onClick={onContinue}>Continue current practice</button> : null}
    {error ? <p role="alert">{error}</p> : null}
    {queued && (current || (notes.practice && notes.practice !== queued.title)) ? <aside className="queued-practice">
      <strong>Build idea saved for your next practice: {queued.title}</strong>
      <p>Finish your current practice first. Its notes remain unchanged.</p><details><summary>Queued build steps</summary><p>{queued.task}</p></details>
    </aside> : null}
    {ready && planning ? <form onSubmit={event => {event.preventDefault(); persist(notes);}}>
      {!current ? <label className="practice-field">What will you try?
        <input required maxLength={500} disabled={notes.finished} value={notes.practice}
          placeholder="For example, agree on a test before the agent starts"
          onChange={e => update("practice", e.target.value)} /></label> : null}
      <div className="practice-brief-fields">
        <label className="practice-field">On which task?
          <textarea required maxLength={2000} disabled={notes.finished} value={notes.task}
            placeholder="Name a small piece of work" onChange={e => update("task", e.target.value)} /></label>
        <label className="practice-field">How will you check the result?
          <textarea required maxLength={2000} disabled={notes.finished} value={notes.check}
            placeholder="Describe a test, source check or review you can perform"
            onChange={e => update("check", e.target.value)} /></label>
      </div>
      <details className="stat-details" open={notes.finished || undefined}>
        <summary>Review the result</summary>
        <label className="practice-field">What happened when you tried it?
          <textarea maxLength={4000} disabled={notes.finished} value={notes.result}
            placeholder="What passed your check? What would you change next time?"
            onChange={e => update("result", e.target.value)} /></label>
        {!current && !notes.finished ? <button type="button"
          disabled={![notes.practice, notes.task, notes.check, notes.result].every(s => s.trim())}
          onClick={() => persist({...notes, finished: true})}>Finish this practice</button> : null}
      </details>
      <div className="practice-brief-actions">
        {!notes.finished ? <button type="submit">Save practice notes</button> : <>
          <span>Practice reviewed</span>
          <button type="button" onClick={() => persist(queued ? {...blank(), practice: queued.title, task: queued.task} : blank(), [...history, {...notes, day: new Date().toLocaleDateString()}])}>Plan another practice</button>
        </>}
        <span className="muted">Notes stay in this browser on this device.</span>
      </div>
      {saved ? <p role="status">Practice notes saved.</p> : null}
    </form> : null}
    {history.length ? <details className="stat-details"><summary>Previous practice notes</summary>
      {history.map((entry, index) => <details className="stat-details" key={index}>
        <summary>{entry.practice} · {entry.day}</summary><p>{entry.task}</p><p>{entry.check}</p><p>{entry.result}</p>
      </details>)}
    </details> : null}
  </section>;
}

export function PracticeWorkspace({current, suggestion, ready = true, error = false, coach, hours, learning, prompts, patterns}) {
  const [selected, setSelected] = useState(null);
  const panel = selected ?? current?.panel ?? "";
  const panels = [["coach", "Practice suggestions and feedback", coach], ["hours", "Practice time", hours],
    ["learning", "Learning resources", learning], ["prompts", "Prompts to try", prompts],
    ["patterns", "Recorded work patterns", patterns]];
  return <>
    {ready ? <PracticeBrief key={current?.key || "personal"} current={current} suggestion={suggestion} onSuggestion={() => {setSelected("coach"); document.getElementById("practice-panel-coach")?.scrollIntoView?.({block: "start"});}} onContinue={() => {
      setSelected(current.panel);
      document.getElementById(`practice-panel-${current.panel}`)?.scrollIntoView?.({block: "start"});
    }} /> : <section className="card"><h1>Your current practice</h1>
      <p role="status">{error ? "Some practice records could not be loaded. Open Practice time or Learning resources below to retry."
        : "Checking for a practice you already started…"}</p></section>}
    <div className="practice-support">{panels.filter(([, , content]) => content).map(([id, title, content]) =>
      <details key={id} id={`practice-panel-${id}`} className="practice-panel" open={panel === id}
        onToggle={event => {const open = event.currentTarget.open; if (open && panel !== id) setSelected(id); else if (!open && panel === id) setSelected("");}}>
        <summary>{title}{current?.panel === id ? " · current" : ""}</summary>{content}
      </details>)}
    </div>
    <a className="practice-checklist-link" href="#baseline">AI working checklist</a>
  </>;
}
