import React from "react";
import { Info } from "./Info.jsx";

// Existing choices take precedence over another suggestion. This view routes
// to their original controls; it never starts, completes, or credits work.
export function readingStep(view, {practiceTime, learning, recordsReady = true} = {}) {
  if (practiceTime?.active) return {href: "#practice", label: "Return to your session",
    title: practiceTime.active.running ? "Your practice session is recording" : "Your practice session is paused",
    body: "Finish and review the time before it enters your confirmed practice record."};
  const c = view.capability;
  if (c?.practice_result || c?.practice_progress) {
    const p = c.practice_result || c.practice_progress;
    return {href: "#practice", label: c.practice_result ? "Review your practice" : "Continue your practice",
      title: p.title, body: c.practice_result ? p.line : p.practice};
  }
  const course = learning?.records?.find(r => ["saved", "in_progress"].includes(r.state));
  if (course) return {href: "#practice", label: "Continue your learning step", title: course.entry.title,
    body: course.availability === "current" ? "Your chosen learning step. Progress stays on this device."
      : `Your saved guidance is ${course.availability}. Review its status before continuing.`};
  if (!recordsReady) return null;
  if (c?.pending_outcome) return {href: "#practice", label: "Review your recent work",
    title: "A question about your recent work", body: c.pending_outcome.question};
  if (c?.paths_confirmed && c?.opportunity) return {href: "#practice", label: "Review this suggested practice",
    title: c.opportunity.title, body: c.opportunity.why};
  if (view.playbook?.length) return {href: "#practice", label: "Review the suggested action",
    title: view.playbook[0].title, body: view.playbook[0].why};
  return null;
}

export function Reading({view, practiceTime, learning, recordsReady = true, recordsError = false, editions = null, editorialOnly = false}) {
  const observation = view.observation;
  const qualified = observation?.title && observation.body && observation.period && observation.confidence && observation.caveat;
  const step = readingStep(view, {practiceTime, learning, recordsReady});
  return <div className={`layout col reading-page${step ? "" : " reading-no-step"}${editorialOnly ? " editorial-updates" : ""}`}>
    <header className="reading-intro">
      <h1>News</h1>
      <p className="section-intro">The latest changes to AI tools and services.</p>
    </header>
    {!editorialOnly ? <><section className="card reading-personal" aria-labelledby="reading-your-work">
      <div className="eyebrow">Your work</div>
      <h2 id="reading-your-work">{qualified ? observation.title : "Not enough recorded activity for a pattern yet"}</h2>
      <p>{qualified ? observation.body : "There is not enough supported evidence for a work-pattern observation yet. You can still explore the curated editions and choose a practice."}</p>
      {qualified ? <>
        <p className="reading-evidence"><span>{observation.period} · {observation.confidence}</span>
          <Info text={observation.caveat} /></p>
      </> : null}
      <a href="#spend">See your usage <span aria-hidden="true">→</span></a>
      {!step ? <p className="reading-quiet" role={!recordsReady ? "status" : undefined}>
        {!recordsReady ? recordsError ? "Some private records could not be loaded. Open the Practice page to retry."
          : "Checking your private records for a step you already chose…"
          : <>No next step selected. <a href="#practice">Choose a practice</a> when you have something you want to improve.</>}
      </p> : null}
    </section>
    {step ? <section className="card reading-next" aria-labelledby="reading-next-title">
      <div className="eyebrow">Your next step</div>
      <h2 id="reading-next-title">{step.title}</h2><p>{step.body}</p>
      <a className="reading-action" href={step.href}>{step.label} <span aria-hidden="true">→</span></a>
    </section> : null}</> : null}
    {editions}

  </div>;
}
