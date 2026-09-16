import { useCallback, useEffect, useRef, useState } from "react";
import { changePracticeTime, fetchPracticeTime } from "./api.js";
import { Info } from "./Info.jsx";

const FEEDBACK = [["", "No reflection"], ["learned", "Learned something"],
  ["needs_practice", "Needs more practice"], ["unsure", "Unsure yet"]];
const ERRORS = {
  finish_active_first: "Finish the current session first.",
  session_changed: "This session changed in another window. Reload the history.",
  invalid_duration: "Choose a positive duration no longer than the recorded time.",
  invalid_backup: "This file is not a valid PracticeGraph practice-history backup.",
  backup_conflict: "This backup differs from a session already here. Nothing was imported.",
  overlapping_time: "These sessions overlap. Nothing was added twice.",
  session_limit: "This session reached 12 hours. Finish and review its time before starting another.",
  invalid_goal: "Choose a whole-hour goal between 1 and 100,000, or leave it empty.",
  history_disabled: "Enable practice history to change sessions or goals.",
  body_too_large: "This backup exceeds the 16 MB import limit.",
};

export function practiceDuration(seconds) {
  if (seconds > 0 && seconds < 60) return "less than 1m";
  const minutes = Math.floor(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
}

// Mounted with the dashboard, so navigating to another section keeps a session alive.
// No catch-up on reconnect: the engine pauses at the last acknowledged heartbeat.
export function usePracticeTime() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = useRef(null);
  const locked = useRef(false);
  const mounted = useRef(true);
  const accept = useCallback(value => {
    if (mounted.current && Array.isArray(value?.skills)) {
      current.current = value; setData(value);
    }
  }, []);
  const reload = useCallback(async () => {
    try { accept(await fetchPracticeTime()); if (mounted.current) setError(""); }
    catch { if (mounted.current) setError("Practice history could not be loaded. Try again."); }
  }, [accept]);
  const run = useCallback(async body => {
    if (locked.current) return false;
    locked.current = true; setBusy(true); setError("");
    try { accept(await changePracticeTime(body)); return true; }
    catch (e) {
      setError(e.message === "invalid_duration" && body.action === "manual"
        ? "Choose a completed session with an end after its start, lasting no more than 12 hours."
        : ERRORS[e.message] || "That did not save. Try again.");
      return false;
    }
    finally { locked.current = false; if (mounted.current) setBusy(false); }
  }, [accept]);
  useEffect(() => {
    mounted.current = true; reload();
    const poll = setInterval(async () => {
      if (locked.current) return;
      locked.current = true;
      try {
        const active = current.current?.active;
        accept(active?.running
          ? await changePracticeTime({action: "heartbeat", id: active.id})
          : await fetchPracticeTime());
      } catch {
        try { accept(await fetchPracticeTime()); }
        catch { if (mounted.current) setError("Connection lost. Unacknowledged time will not be added. Reload to check the session."); }
      } finally { locked.current = false; }
    }, 30000);
    return () => { mounted.current = false; clearInterval(poll); };
  }, [reload, accept]);
  return {data, error, busy, run, reload, setError};
}

function SkillSelect({skills, value, onChange, disabled, label = "Skill"}) {
  return <label className="practice-field">{label}<select value={value} disabled={disabled}
    onChange={event => onChange(event.target.value)}>
    {skills.map(skill => <option key={skill.id} value={skill.id}>{skill.label}</option>)}
  </select></label>;
}

function ReviewSession({entry, skills, run, busy, estimated = false, editing = false}) {
  const [skill, setSkill] = useState(entry.skill || skills[0].id);
  const [minutes, setMinutes] = useState(String(Math.round(entry.seconds / 6) / 10));
  const [reflection, setReflection] = useState(entry.reflection || "");
  // Preserve seconds when the displayed rounded minutes have not been edited.
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    if (!changed) setMinutes(String(Math.round(entry.seconds / 6) / 10));
  }, [entry.seconds, changed]);
  const amount = changed ? Math.round(Number(minutes) * 60) : entry.seconds;
  return <div className="practice-review">
    <p><strong>{entry.day}</strong> · {practiceDuration(entry.seconds)}
      {estimated ? " estimated human activity" : " recorded session"}</p>
    <div className="practice-fields">
      <SkillSelect skills={skills} value={skill} onChange={setSkill} disabled={busy} />
      <label className="practice-field">Minutes to count<input type="number" min="0.1" step="0.1"
        max={Math.ceil(entry.seconds / 6) / 10} value={minutes} disabled={busy}
        onChange={event => {setMinutes(event.target.value); setChanged(true);}} /></label>
      <label className="practice-field">What did you take away?<select disabled={busy} value={reflection}
        onChange={event => setReflection(event.target.value)}>
        {FEEDBACK.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select></label>
    </div>
    <div className="capability-actions">
      <button disabled={busy || !Number.isFinite(amount) || amount <= 0 || amount > entry.seconds}
        onClick={() => run({action: "confirm", id: entry.id, skill, seconds: amount, reflection})}>
        {editing ? "Save correction" : "Count as practice"}</button>
      <button disabled={busy} onClick={() => {
        if (!editing || window.confirm("Remove this session from your practice total?")) {
          run({action: "dismiss", id: entry.id});
        }
      }}>{editing ? "Remove session" : "Skip"}</button>
    </div>
  </div>;
}

export function PracticeHistory({controller}) {
  const {data, error, busy, run, reload, setError} = controller;
  const [selected, setSelected] = useState("ai-development");
  const [goal, setGoal] = useState("");
  const [backup, setBackup] = useState(null);
  const [manualStart, setManualStart] = useState("");
  const [manualEnd, setManualEnd] = useState("");
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(Date.now());
  const savedGoal = data?.skills.find(skill => skill.id === selected)?.goal_hours;
  useEffect(() => {
    if (!data?.active?.running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [data?.active?.running]);
  useEffect(() => {
    setGoal(String(savedGoal || ""));
  }, [selected, savedGoal]);
  if (!data) return <section className="card practice-history">
    <h2>Your practice history</h2><p className="muted">{error || "Opening your private record…"}</p>
    {error ? <button onClick={reload}>Try again</button> : null}
  </section>;
  const skill = data.skills.find(item => item.id === selected) || data.skills[0];
  const active = data.active;
  const hasRecord = data.history.length || data.pending.length || active;
  const disconnected = active?.running && Math.floor(now / 1000) - active.last_tick > 90;
  const extrapolated = active?.running && !disconnected
    ? Math.max(0, Math.min(90, Math.floor(now / 1000) - active.last_tick)) : 0;
  const download = async () => {
    try {
      const content = await fetchPracticeTime(true);
      const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2)], {type: "application/json"}));
      const link = document.createElement("a"); link.href = url;
      link.download = "practicegraph-practice-history.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("Backup exported. Keep it somewhere private to restore after reinstalling.");
    } catch { setError("The backup could not be exported. Try again."); }
  };
  const loadBackup = async event => {
    const file = event.target.files?.[0]; setBackup(null); setMessage(""); setError("");
    if (!file) return;
    try {
      if (file.size > 16 * 1024 * 1024) throw new Error();
      const value = JSON.parse(await file.text());
      if (value.schema !== "practicegraph.practice-time/1" || !Array.isArray(value.entries)) throw new Error();
      setBackup(value);
    } catch { setError("Choose a valid practice-history JSON backup under 16 MB."); }
    event.target.value = "";
  };
  return <section className="card practice-history">
    <h2>Your practice history<Info text={"Time you chose to count, reviewed session by session - a record "
      + `of investment, not a level of mastery. Week beginning ${data.week_start} · ${data.timezone}.`} /></h2>
    {!data.enabled && !hasRecord ? <>
      <p>Keep a record of the time you invest in a skill. Choose sessions to count and return to what you learned.</p>
      <button disabled={busy} onClick={() => run({action: "enable", enabled: true})}>Enable practice history</button>
    </> : <>
      {!data.enabled ? <div><p>Tracking is paused. Your record is preserved.</p>
        <button disabled={busy} onClick={() => run({action: "enable", enabled: true})}>Enable practice history</button></div> : null}
      <SkillSelect skills={data.skills} value={selected} onChange={setSelected} disabled={busy} />
      <p className="practice-total">{skill.confirmed_seconds ? <>{practiceDuration(skill.confirmed_seconds)} <span>confirmed practice</span></> : "No confirmed practice time yet"}</p>
      <p>{practiceDuration(skill.week_seconds)} this week</p>
      {skill.goal_hours ? <p className="practice-goal">Your goal: {skill.goal_hours.toLocaleString()} hours
        {skill.confirmed_seconds >= skill.goal_hours * 3600 ? " · reached" : ""}.</p> : null}
      {active ? <div className="practice-active">
        <strong>{data.skills.find(item => item.id === active.skill)?.label}</strong>
        <p role="timer" aria-label="Current practice session">{practiceDuration(active.seconds + extrapolated)}
          {disconnected ? " · connection paused" : active.running ? " · recording" : " · paused"}</p>
        <div className="capability-actions">
          <button disabled={busy || !data.enabled} onClick={() => run({action: active.running && !disconnected ? "pause" : "resume", id: active.id})}>
            {active.running && !disconnected ? "Pause practice" : "Resume practice"}</button>
          <button disabled={busy || !data.enabled} onClick={() => run({action: "finish", id: active.id})}>Finish and review</button>
        </div>
        <p className="muted">Saved time: {practiceDuration(active.seconds)}.<Info text={"Time saves every 30 seconds; "
          + "after 90 seconds without contact it pauses at the last save. Resume explicitly and remove idle time when reviewing."} /></p>
      </div> : <button disabled={busy || !data.enabled} onClick={() => run({action: "start", skill: selected})}>Start practice session</button>}
      {data.pending.length > 0 ? <div className="practice-pending"><h3>Ready for your review</h3>
        {data.pending.map(entry => <ReviewSession key={`${entry.id}-${entry.seconds}`} entry={entry}
          skills={data.skills} busy={busy} run={run} />)}</div> : null}
      <details className="stat-details"><summary>Review recent activity estimates</summary>
        <p className="muted">Stretches of your own activity in supported tools, five minutes or closer together.
          Count only the time you actually practised.<Info text={"Overlapping activity counts once; recorded breaks and "
          + "existing sessions are excluded. An estimate is not practice until you count it."} /></p>
        {data.suggestions.length ? data.suggestions.map(entry => <ReviewSession key={entry.id} entry={entry}
          skills={data.skills} busy={busy} run={run} estimated />)
          : <p>No unreviewed activity estimates in the last seven local days.</p>}
      </details>
      <details className="stat-details"><summary>Confirmed sessions</summary>
        {data.history.length >= 100 ? <p className="muted">Latest 100 shown; exports carry the full record.</p> : null}
        {data.history.length ? data.history.map(entry => <details key={entry.id} className="practice-entry">
          <summary>{entry.day} · {data.skills.find(s => s.id === entry.skill)?.label} · {practiceDuration(entry.seconds)}</summary>
          <ReviewSession entry={entry} skills={data.skills} busy={busy} run={run} editing />
        </details>) : <p>Your first confirmed session will appear here.</p>}
      </details>
      <details className="stat-details"><summary>Add a missed practice session</summary>
        <p>Practice done away from the timer, for {skill.label} - local time, up to 12 hours per entry.</p>
        <div className="practice-fields">
          <label className="practice-field">Started<input type="datetime-local" value={manualStart}
            onChange={event => setManualStart(event.target.value)} /></label>
          <label className="practice-field">Finished<input type="datetime-local" value={manualEnd}
            onChange={event => setManualEnd(event.target.value)} /></label>
        </div>
        <button disabled={busy || !data.enabled || !manualStart || !manualEnd} onClick={async () => {
          if (await run({action: "manual", skill: selected, start: Math.floor(Date.parse(manualStart) / 1000),
            end: Math.floor(Date.parse(manualEnd) / 1000), reflection: ""})) {
            setManualStart(""); setManualEnd(""); setMessage("Practice session added.");
          }
        }}>Confirm completed practice</button>
      </details>
      <details className="stat-details"><summary>Set a personal hours goal</summary>
        <label className="practice-field">Hours for {skill.label}<input type="number" min="1" max="100000"
          step="1" value={goal} onChange={event => setGoal(event.target.value)} /></label>
        <div className="capability-actions"><button disabled={busy}
          onClick={() => run({action: "goal", skill: selected, hours: goal === "" ? null : Number(goal)})}>Save goal</button></div>
      </details>
    </>}
    <details className="stat-details"><summary>Back up and manage this record</summary>
      <p className="muted">Kept on this device. Export before reinstalling or moving devices.</p>
      <div className="capability-actions"><button disabled={busy} onClick={download}>Export history</button>
        {data.enabled ? <button disabled={busy} onClick={() => run({action: "enable", enabled: false})}>Pause history tracking</button> : null}</div>
      <label className="practice-field">Restore a backup<input type="file" accept="application/json,.json" disabled={busy}
        onChange={loadBackup} /></label>
      {backup ? <div><p>{backup.entries.length} session records ready for validation.</p>
        <button disabled={busy} onClick={async () => {
          if (await run({action: "restore", backup})) {setBackup(null); setMessage("History restored.");}
        }}>Restore history</button></div> : null}
      <button className="skcopy" disabled={busy} onClick={() => {
        if (window.confirm("Permanently clear all practice-time sessions and goals from this device? Export a backup first if you want to keep them.")) {
          run({action: "clear", confirm: true});
        }
      }}>Clear practice-time history</button>
    </details>
    {message ? <p role="status">{message}</p> : null}
    {error ? <div role="alert"><p>{error}</p><button onClick={reload}>Reload history</button></div> : null}
  </section>;
}
