import { useState } from "react";

import {
  acceptCapabilityPractice,
  clearCapabilityHistory,
  recordCapabilityOutcome,
  reviewCapabilityPractice,
  saveCapabilityPaths,
} from "./api.js";
import { safeCodexUrl } from "./urls.js";
import { Info } from "./Info.jsx";

const PATHS = [
  { id: "knowledge", label: "Knowledge work" },
  { id: "software", label: "Software work" },
];
const FEEDBACK = [
  { id: "helpful", label: "Tried it — helpful" },
  { id: "not_helpful", label: "Tried it — not helpful" },
  { id: "uncertain", label: "Tried it — unsure" },
  { id: "not_tried", label: "Didn't try it" },
];

export function CapabilityCoach({
  capability,
  refresh,
  savePaths = saveCapabilityPaths,
  recordOutcome = recordCapabilityOutcome,
  acceptPractice = acceptCapabilityPractice,
  reviewPractice = reviewCapabilityPractice,
  showRecentWork = false,
}) {
  const [paths, setPaths] = useState(capability.paths);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [editingPaths, setEditingPaths] = useState(false);

  const runAction = async (action) => {
    setBusy(true);
    setError(false);
    try {
      await action();
      await refresh();
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const togglePath = (path) => {
    setPaths((current) =>
      current.includes(path)
        ? current.length === 1
          ? current
          : current.filter((value) => value !== path)
        : [...current, path],
    );
  };

  let content;
  if (!capability.paths_confirmed || editingPaths) {
    content = (
      <>
        <p className="muted">Choose the kinds of work you want this coach to notice.</p>
        <div className="capability-paths">
          {PATHS.map((path) => (
            <label key={path.id}>
              <input
                type="checkbox"
                checked={paths.includes(path.id)}
                disabled={busy}
                onChange={() => togglePath(path.id)}
              />
              <span>{path.label}</span>
            </label>
          ))}
        </div>
        <div className="capability-actions">
          <button
            type="button"
            disabled={busy || paths.length === 0}
            onClick={() => runAction(async () => {
              await savePaths(paths);
              setEditingPaths(false);
            })}
          >
            Save my focus
          </button>
        </div>
      </>
    );
  } else if (showRecentWork && capability.pending_outcome) {
    content = (
      <>
        {capability.pending_outcome.subject ? <p className="review-subject">{capability.pending_outcome.subject}</p> : null}
        <p>{capability.pending_outcome.question}</p>
        <div className="capability-actions">
          {capability.pending_outcome.options.map((option) => (
            <button
              type="button"
              key={option.id}
              disabled={busy}
              onClick={() => runAction(() => recordOutcome(option.id))}
            >
              {option.label}
            </button>
          ))}
        </div>
      </>
    );
  } else if (capability.practice_result) {
    const result = capability.practice_result;
    content = (
      <>
        <h2>{result.title}</h2>
        <p>{result.line}</p>
        <p className="muted">
          This local check used {result.comparable_units} comparable pieces of work.
          <Info text="These log signals do not establish whether the practice caused a change." />
        </p>
      </>
    );
  } else if (capability.practice_progress) {
    const progress = capability.practice_progress;
    const codexUrl = safeCodexUrl(progress.codex_url);
    content = (
      <>
        <h2>{progress.title}</h2>
        <p>{progress.practice}</p>
        <p className="muted">{progress.context}</p>
        <div className="capability-actions">
          {codexUrl ? (
            <a
              className="skcopy"
              href={codexUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open in Codex
            </a>
          ) : null}
        </div>
      </>
    );
  } else if (capability.opportunity) {
    const opportunity = capability.opportunity;
    const codexUrl = safeCodexUrl(opportunity.codex_url);
    content = (
      <>
        <h2>{opportunity.title}</h2>
        <p>{opportunity.observation}</p>
        <p>{opportunity.why}</p>
        <p>{opportunity.practice}</p>
        <div className="capability-actions">
          {codexUrl ? (
            <a
              className="skcopy"
              href={codexUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open in Codex
            </a>
          ) : null}
          <button
            type="button"
            disabled={busy}
            onClick={() => runAction(() => acceptPractice(opportunity.practice_id))}
          >
            Try this practice
          </button>
        </div>
      </>
    );
  } else {
    content = <p>There is not enough recorded activity for a personal suggestion yet. You can plan a practice above or use the AI working checklist.</p>;
  }

  return (
    <section className="card capability-coach">
      <h2>Practice suggestions and feedback</h2>
      <div className="capability-content">{content}</div>
      {(capability.practice_result || capability.practice_progress) && !editingPaths ? (
        <div className="capability-review">
          <p>Finish this practice with your own assessment.</p>
          <div className="capability-actions">
            {FEEDBACK.map((item) => (
              <button type="button" key={item.id} disabled={busy}
                onClick={() => runAction(() => reviewPractice(item.id))}>
                {item.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {capability.paths_confirmed && !editingPaths ? (
        <button type="button" className="skcopy" disabled={busy}
          onClick={() => setEditingPaths(true)}>Change my focus</button>
      ) : null}
      {capability.history?.length ? (
        <details className="capability-history">
          <summary>Your practice feedback</summary>
          <p className="muted">Your own reports, separate from the observed activity above.</p>
          <ul>{capability.history.map((item, index) => (
            <li key={`${item.finished_day}-${index}`}>
              {item.finished_day} · {item.title} · {FEEDBACK.find((f) => f.id === item.feedback)?.label}
            </li>
          ))}</ul>
        </details>
      ) : null}
      {capability.paths_confirmed ? (
        <details className="capability-history">
          <summary>Manage private coach records</summary>
          <p>Clears your answers, the active practice and your feedback; practice hours and activity history stay.</p>
          <button type="button" disabled={busy} onClick={() => {
            if (window.confirm("Permanently clear your private coach answers and practice feedback? Your practice-time record is kept.")) {
              runAction(clearCapabilityHistory);
            }
          }}>Clear coach history</button>
        </details>
      ) : null}
      {error ? <p className="capability-error" role="alert">That did not save. Try once more.</p> : null}
    </section>
  );
}
