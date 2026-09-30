import { useCallback, useEffect, useRef, useState } from "react";
import { changeSetupImprovement, fetchSetupImprovements } from "./api.js";
import { Info } from "./Info.jsx";

const ERRORS = {
  invalid_project: "Enter an absolute folder path on this device.",
  finish_improvement_first: "Finish or dismiss the current improvement first.",
  finish_coach_first: "Finish your current coaching practice before starting a setup improvement.",
  missing_catalog: "No playbook edition is available yet. Your inspection is saved.",
  no_verification_gap: "No supported verification gap was found in this inspection. No improvement brief is needed.",
  stale_catalog: "This guidance needs editorial review. A current edition is needed for a new handoff.",
  no_compatible_playbook: "No current playbook matches this stack and harness version.",
  withdrawn: "This playbook was withdrawn or removed. The earlier record is preserved.",
  edition_changed: "This playbook changed. Dismiss this draft and inspect again to prepare the updated guidance.",
  context_changed: "The observed harness version changed. Inspect again before trying this improvement.",
  invalid_transition: "This improvement changed. Reload to see its current state.",
  mark_attempt_first: "Mark the improvement as tried before reviewing its usefulness.",
  record_conflict: "The backup conflicts with a record already here. Nothing was imported.",
  invalid_backup: "This is not a valid setup-improvements backup. Nothing was imported.",
  unconfirmed_practice: "Choose a confirmed practice session that is still in your history.",
  body_too_large: "The backup exceeds the 16 MB import limit.",
};

export function useSetupImprovements(enabled) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  const actionPending = useRef(false);
  const generation = useRef(0);
  const accept = useCallback(value => {
    if (active.current && value?.schema === "practicegraph.setup-improvements/1") setData(value);
  }, []);
  const reload = useCallback(async () => {
    const version = generation.current;
    try {
      const result = await fetchSetupImprovements();
      if (version === generation.current && !actionPending.current) {
        accept(result); if (active.current) setError("");
      }
    } catch { if (active.current) setError("Setup improvements could not be loaded. Try again."); }
  }, [accept]);
  const run = useCallback(async body => {
    if (actionPending.current) return null;
    actionPending.current = true; generation.current += 1; setBusy(true); setError("");
    try {
      const result = await changeSetupImprovement(body); accept(result); return result;
    } catch (e) {
      if (active.current) setError(ERRORS[e.message] || "That did not save. Try again.");
      return null;
    } finally {
      actionPending.current = false; generation.current += 1;
      if (active.current) setBusy(false);
    }
  }, [accept]);
  useEffect(() => {active.current = true; return () => {active.current = false;};}, []);
  useEffect(() => {
    if (!enabled) return;
    reload();
    const timer = setInterval(() => {if (!actionPending.current) reload();}, 30000);
    return () => clearInterval(timer);
  }, [enabled, reload]);
  return {data, busy, error, run, reload, setError};
}

function downloadText(text, name, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function Comparison({value}) {
  const messages = {
    not_attempted: "The follow-up begins when you mark the improvement as tried.",
    context_unknown: "There is not enough project, model, or harness-version evidence for a comparison.",
    context_changed: "The observed harness version changed. The earlier samples are preserved; new comparisons are paused.",
    waiting: "Waiting for three later comparable changes in this folder, branch, and recorded tool/model context.",
    observed: "Three comparable changes are available. This is an observation, not evidence that the improvement caused a change.",
  };
  return <div className="setup-comparison">
    <h3>What happened afterwards?<Info text={"Edited work episodes from supported logs; an attempted check is not a "
      + "passing test. Models are matched on the daily session records; missing or different context is excluded."} /></h3>
    <p>{messages[value.state]}</p>
    {value.before_count ? <p>Before: verification attempts visible in {value.before_verified} of {value.before_count} changes.</p>
      : <p>No matching earlier changes were available.</p>}
    {value.after_count > 0 ? <p>After: verification attempts visible in {value.after_verified} of {value.after_count} changes.</p> : null}
  </div>;
}

function Evidence({record}) {
  const i = record.inspection;
  return <details className="stat-details"><summary>Inspection evidence and limits</summary>
    <p>Detected stack: {i.stacks.join(", ") || "unknown"}.</p>
    <p>Declared package scripts: {i.declared_checks.join(", ") || "none found"}.
      Pytest configuration: {i.pytest_config ? "found" : "not found"}.</p>
    <p>{i.verification_mentioned ? "A recognized test command appears in the inspected guidance."
      : "No recognized test command was found in the inspected guidance."}</p>
    <p className="muted">File presence and declarations do not establish a working command or complete documentation.
      Nested packages, other testing tools, and unrecorded checks may be outside this inspection.</p>
    <ul>{i.files.map(file => <li key={file.name}>{file.name}: {file.state.replaceAll("_", " ")}</li>)}</ul>
    {record.entry ? <>
      <p>{record.entry.limitations}</p>
      <p>Playbook revision {record.entry.revision} · reviewed {record.entry.reviewed_on}.</p>
      <ul>{record.entry.sources.map((source, index) => <li key={source}>
        <a href={source} target="_blank" rel="noreferrer">Source {index + 1}</a></li>)}</ul>
    </> : null}
  </details>;
}

function Improvement({record, run, busy, history = false, practiceHistory = [], notify, setError}) {
  const ready = record.availability === "ready";
  const canHandoff = ready && ["prepared", "attempted", "reviewed"].includes(record.state);
  const handoff = async exporting => {
    const result = await run({action: "handoff", id: record.id});
    if (!result?.brief) return;
    try {
      if (exporting) downloadText(result.brief, "practicegraph-verification-brief.txt", "text/plain");
      else await navigator.clipboard.writeText(result.brief);
      notify(exporting ? "Brief exported. Mark it tried after you use it." : "Task copied. Copying does not mark it tried.");
    } catch { setError("The brief is still available below. Select and copy its text, or export it."); }
  };
  return <div className="setup-improvement">
    <h3>{record.label}</h3>
    <p className="muted">{record.tool === "codex" ? "Codex" : "Claude Code"} · {new Date(record.created_at * 1000).toLocaleDateString()}
      {record.feedback ? ` · ${record.feedback.replaceAll("_", " ")}` : ""}</p>
    {!ready ? <p role="status">{ERRORS[record.availability] || "Guidance is unavailable for this inspection."}</p> : null}
    {record.state === "inspected" ? <>
      {record.baseline.length ? <p>In {record.baseline.length} recent matching changes, verification attempts were visible in {record.baseline.filter(f => f.tests > 0).length}.</p>
        : <p>No matching edited work was available. You can still request a verification setup brief.</p>}
      <button disabled={busy || !ready} onClick={() => run({action: "prepare", id: record.id})}>Prepare verification brief</button>
    </> : null}
    {record.brief ? <>
      <details className="stat-details" open={!history}><summary>Review the exact verification brief</summary>
        <pre className="setup-brief">{record.brief}</pre>
      </details>
      <div className="capability-actions">
        <button disabled={busy || !canHandoff} onClick={() => handoff(false)}>Copy task</button>
        <button disabled={busy || !canHandoff} onClick={() => handoff(true)}>Export brief</button>
        {record.state === "prepared" ? <button disabled={busy || !ready}
          onClick={() => run({action: "attempt", id: record.id})}>I tried this improvement</button> : null}
      </div>
      <Comparison value={record.comparison} />
      {(record.attempted_at || record.state === "prepared") && record.state !== "dismissed" ? <>
        <p>{history ? "Correct your assessment:" : "Your assessment:"}</p>
        <div className="capability-actions">
          {(record.attempted_at ? [["helpful", "Helpful"], ["not_helpful", "Not helpful"], ["unsure", "Unsure yet"]]
            : [["not_tried", "I did not try it"]]).map(([feedback, label]) => <button key={feedback} disabled={busy}
              onClick={() => run({action: "review", id: record.id, feedback})}>{label}</button>)}
        </div>
      </> : null}
    </> : null}
    <Evidence record={record} />
    {record.attempted_at ? <details className="stat-details"><summary>Link a confirmed practice session</summary>
      <p className="muted">This adds context to the improvement. It does not add or change practice hours.</p>
      <label className="practice-field">Practice session<select value={record.practice_id || ""} disabled={busy}
        onChange={event => run({action: "link_practice", id: record.id, practice_id: event.target.value || null})}>
        <option value="">No session linked</option>
        {record.practice_id && !practiceHistory.some(p => p.id === record.practice_id)
          ? <option value={record.practice_id}>Linked session not in the recent history</option> : null}
        {practiceHistory.map(p => <option value={p.id} key={p.id}>{p.day} · {Math.floor(p.seconds / 60)} minutes</option>)}
      </select></label>
    </details> : null}
    {!history ? <button className="skcopy" disabled={busy}
      onClick={() => run({action: "dismiss", id: record.id})}>Dismiss this improvement</button> : null}
  </div>;
}

export function SetupImprovements({controller, practiceHistory = []}) {
  const {data, error, busy, run, reload, setError} = controller;
  const [path, setPath] = useState("");
  const [label, setLabel] = useState("");
  const [tool, setTool] = useState("codex");
  const [message, setMessage] = useState("");
  const [backup, setBackup] = useState(null);
  const request = useRef(null);
  if (!data) return <section className="card setup-improvements"><h2>Improve my setup</h2>
    <p>{error || "Opening your private setup record…"}</p>{error ? <button onClick={reload}>Try again</button> : null}</section>;
  const inspect = async () => {
    request.current ||= crypto.randomUUID().replaceAll("-", "");
    const result = await run({action: "inspect", id: request.current, path, label, tool});
    if (result) {setPath(""); setLabel(""); request.current = null; setMessage("");}
  };
  const exportHistory = async () => {
    try {
      const value = await fetchSetupImprovements(true);
      downloadText(JSON.stringify(value, null, 2), "practicegraph-setup-improvements.json", "application/json");
      setMessage("History exported. Keep this backup private.");
    } catch {setError("The history could not be exported. Try again.");}
  };
  const loadBackup = async event => {
    const file = event.target.files?.[0]; setBackup(null); setError("");
    if (!file) return;
    try {
      if (file.size > 16 * 1024 * 1024) throw new Error();
      const value = JSON.parse(await file.text());
      if (value.schema !== "practicegraph.setup-improvements/1" || !Array.isArray(value.records)) throw new Error();
      setBackup(value);
    } catch {setError("Choose a setup-improvements JSON backup under 16 MB.");}
    event.target.value = "";
  };
  return <section className="card setup-improvements">
    <h2>Improve my setup</h2>
    <p>Check one project's verification setup, try the fix with your coding harness, and keep a record of what happened.</p>
    <p className="content-status" title={data.catalog.source_label || undefined}>
      {data.catalog.edition_date ? `Playbook edition ${data.catalog.edition_date}` : "No playbook edition yet"}
      {data.catalog.state && data.catalog.state !== "current" ? ` · ${data.catalog.state.replaceAll("_", " ")}` : ""}</p>
    {data.active ? <Improvement record={data.active} run={run} busy={busy} practiceHistory={practiceHistory}
      notify={setMessage} setError={setError} /> : <>
      {data.verification_gaps > 0 ? <p>Some of this week's edits show no test run in the logs. Pick a project folder to check.</p> : null}
      {data.coach_active ? <p>Finish your current coaching practice before starting a setup improvement.</p> : null}
      <details className="stat-details"><summary>Inspect a project</summary>
        <p>Reads the project's config and docs; runs nothing.<Info text={"Read-only: package.json, pyproject.toml, "
          + "pytest.ini, tox.ini, AGENTS.md, CLAUDE.md, README.md, CONTRIBUTING.md, docs/TESTING.md and root lockfile "
          + "presence, up to 128 KB per file. Scripts are not executed. Only findings, file fingerprints and the name "
          + "you enter are kept; contents and the path are not."} /></p>
        <label className="practice-field">Name this check<input value={label} maxLength={80} disabled={busy}
          placeholder="For example, website verification" onChange={e => {setLabel(e.target.value); request.current = null;}} /></label>
        <label className="practice-field">Project folder<input value={path} disabled={busy} autoComplete="off" spellCheck={false}
          placeholder="Absolute folder path on this device" onChange={e => {setPath(e.target.value); request.current = null;}} /></label>
        <label className="practice-field">Coding harness<select value={tool} disabled={busy}
          onChange={e => {setTool(e.target.value); request.current = null;}}>
          <option value="codex">Codex</option><option value="claude_code">Claude Code</option></select></label>
        <button disabled={busy || !path.trim() || !label.trim() || data.coach_active} onClick={inspect}>Inspect selected project</button>
      </details>
    </>}
    <details className="stat-details"><summary>Your setup playbook</summary>
      {data.history.length ? data.history.map(record => <details key={record.id} className="practice-entry">
        <summary>{record.label} · {record.state}{record.feedback ? ` · ${record.feedback.replaceAll("_", " ")}` : ""}</summary>
        <Improvement record={record} run={run} busy={busy} history practiceHistory={practiceHistory}
          notify={setMessage} setError={setError} /></details>) : <p>Completed and dismissed improvements will appear here.</p>}
    </details>
    <details className="stat-details"><summary>Back up and manage setup records</summary>
      <p className="muted">Kept on this device. Export before reinstalling or moving devices.</p>
      <button disabled={busy} onClick={exportHistory}>Export setup history</button>
      <label className="practice-field">Restore setup backup<input type="file" accept="application/json,.json"
        disabled={busy} onChange={loadBackup} /></label>
      {backup ? <><p>{backup.records.length} improvement records ready for validation.</p>
        <button disabled={busy} onClick={async () => {
          if (await run({action: "restore", backup})) {setBackup(null); setMessage("Setup history restored.");}
        }}>Restore setup history</button></> : null}
      <button className="skcopy" disabled={busy} onClick={async () => {
        if (window.confirm("Clear all setup-improvement records from this device? Export a backup first to keep them. Practice hours and coach feedback are separate.")) {
          if (await run({action: "clear", confirm: true})) setMessage("Setup records cleared.");
        }
      }}>Clear setup records</button>
    </details>
    {message ? <p role="status">{message}</p> : null}
    {error ? <div role="alert"><p>{error}</p><button onClick={reload}>Reload setup history</button></div> : null}
  </section>;
}
