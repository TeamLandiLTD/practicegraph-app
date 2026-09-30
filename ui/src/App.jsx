import TokenPrices from "./TokenPrices.jsx";
import { ContentStatus } from "./ContentStatus.jsx";
import React, { useEffect, useRef, useState } from "react";
import {
  changeNews, checkin, closeDay, coalesce, count, fetchFeedDay,
  dismissSuggestion, durationHm, fetchPresence, fetchView, pinModel,
  recordBlock,
  recordDrain, recordEstimate, recordSkillCopy, saveSchedule,
  setCurrency, usd,
} from "./api.js";
import { UsageBasis, moneyLabel } from "./Usage.jsx";
import { Info } from "./Info.jsx";
import { UsageExplorer } from "./UsageExplorer.jsx";
export { EconomyCard } from "./Usage.jsx";
import { activeNews, toggleNewsId } from "./news.js";
import { NewsPreferences, NewsUrgency } from "./NewsPanel.jsx";
import { PrivacyStatus } from "./PrivacyStatus.jsx";
import { CapabilityCoach } from "./CapabilityCoach.jsx";
import { PracticeHistory, usePracticeTime } from "./PracticeHistory.jsx";
import { LearningPaths, useLearningPaths } from "./LearningPaths.jsx";
import { Reading } from "./Reading.jsx";
import { PracticeWorkspace, currentPractice } from "./PracticeWorkspace.jsx";
import { SURFACES, resolveSurface, ICONS} from "./navigation.js";
import { Community } from "./Community.jsx";

// Content-supplied links (news, model, skill) are validated to https server-
// side; re-check on render as defense in depth so a stray non-https or
// `javascript:` URL can never land in an href. Returns the url or null.
const httpsUrl = (url) =>
  typeof url === "string" && url.startsWith("https://") ? url : null;

// The one non-https scheme this page will ever link: the Codex composer,
// prefilled with a playbook prompt. Exact-prefix check, server-minted only —
// anything else renders as no link at all, and the copy button still works.
const codexUrl = (url) =>
  typeof url === "string" && url.startsWith("codex://new?prompt=") ? url : null;

const PRESENCE_POLL_MS = 60_000;
const VIEW_REFRESH_MS = 5 * 60_000;
// While the engine reports a refresh in progress (a first scan of a long
// history runs for minutes), the view is asked again every few seconds so the
// numbers arrive as they are read. Field report 2026-09-17: a first run showed
// an empty page for five minutes, which reads as "does not load the data".
const SCAN_REFRESH_MS = 5_000;
const SCAN_NOTICE_AFTER_S = 5;
// The version this page was built as. The marker string survives minification
// so a release check can prove the committed bundle matches the release.
export const BUNDLE_VERSION = typeof __APP_VERSION__ === "string" ? __APP_VERSION__ : "";
const BUNDLE_MARK = typeof __APP_VERSION__ === "string" ? "pg-bundle-version:" + __APP_VERSION__ : "";
// Consecutive failed view refreshes before the page reloads to recover a dead
// origin. The native window's watchdog usually re-points us first; this is the
// backstop (and the only recovery for a plain browser tab). At the 5-min
// refresh cadence this is patient by design — a reload is the last resort, not
// a reaction to one blip; presence polling (every 60s) fails quietly meanwhile.
const REFRESH_MISS_LIMIT = 2;



// One drawing of the GitHub mark, for the header link and the repository shelf.
function GitHubMark({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M7 17.5v-2.2c-3 .7-3.7-1.4-3.7-1.4-.5-1.2-1.2-1.5-1.2-1.5-1-.7.1-.7.1-.7 1.1.1 1.7 1.1 1.7 1.1 1 1.7 2.6 1.2 3.2.9.1-.7.4-1.2.7-1.5-2.4-.3-5-1.2-5-5.4 0-1.2.4-2.2 1.1-2.9-.1-.3-.5-1.4.1-2.9 0 0 .9-.3 3 1.1a10 10 0 0 1 5.4 0c2.1-1.4 3-1.1 3-1.1.6 1.5.2 2.6.1 2.9.7.7 1.1 1.7 1.1 2.9 0 4.2-2.6 5.1-5 5.4.4.3.8 1 .8 2v3.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
  );
}

function Card({ children, delay = 0, className = "" }) {
  return (
    <section className={`card ${className}`} style={{ animationDelay: `${delay}ms` }}>
      {children}
    </section>
  );
}

// The display currency. The choice shows at once and saves run one at a time
// (the billing dropdowns' lesson: a control that waits for the whole view to
// rebuild looks broken). A currency whose rates are not downloaded yet stays
// in US dollars until they arrive, and the note says which.
export function CurrencySetting({ currency, fetching, onSaved, save = setCurrency }) {
  const [chosen, setChosen] = useState(null);
  const [error, setError] = useState(false);
  const queue = useRef(Promise.resolve());
  useEffect(() => {
    // Drop the choice once the view reports it, so a later outside change shows.
    setChosen(current => (current === currency.requested ? null : current));
  }, [currency.requested]);
  const change = (code) => {
    setChosen(code); setError(false);
    queue.current = queue.current.then(async () => {
      let saved = false;
      try { saved = await save(code); } catch { saved = false; }
      if (!saved) {
        setError(true);
        setChosen(current => (current === code ? null : current));
        return;
      }
      onSaved?.();
    });
  };
  const waiting = currency.missing && chosen == null;
  const note = error ? "Could not save the currency."
    : waiting && fetching ? "Getting exchange rates…"
      : waiting ? "Exchange rates unavailable; showing US dollars." : "";
  return (
    <span className="currency-setting">
      <select aria-label="Display currency" value={chosen ?? currency.requested}
        title={currency.code === "USD" ? "Show amounts in another currency"
          : `Converted from US dollars at the ${currency.source} rate of ${currency.date}`}
        onChange={event => change(event.target.value)}>
        {currency.available.map(({ code, name }) => (
          <option key={code} value={code}>{code} · {name}</option>
        ))}
      </select>
      {note ? <small role="status">{note}</small> : null}
    </span>
  );
}

const TIMEZONES = [
  "UTC", "Europe/Sofia", "Europe/London", "America/New_York",
  "America/Los_Angeles", "Asia/Tokyo",
];
const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function ScheduleSettings({ schedule, onSaved, save = saveSchedule }) {
  const suggested = schedule.timezone_name || "UTC";
  const [timezone, setTimezone] = useState(
    TIMEZONES.includes(suggested) ? suggested : "__other__",
  );
  const [otherTimezone, setOtherTimezone] = useState(
    TIMEZONES.includes(suggested) ? "" : suggested,
  );
  const [workingDays, setWorkingDays] = useState(schedule.working_days || []);
  const [workStart, setWorkStart] = useState(schedule.work_start || "09:00");
  const [workEnd, setWorkEnd] = useState(schedule.work_end || "18:00");
  const [quietStart, setQuietStart] = useState(schedule.quiet_start || "22:00");
  const [quietEnd, setQuietEnd] = useState(schedule.quiet_end || "07:00");
  const [weekendMode, setWeekendMode] = useState(
    schedule.weekend_mode || "exceptional",
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);

  const toggleDay = (day) => {
    setWorkingDays((days) => days.includes(day)
      ? days.filter((item) => item !== day)
      : [...days, day].sort((a, b) => a - b));
  };
  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError(false);
    try {
      const result = await save({
        timezone_name: otherTimezone.trim()
          || (timezone === "__other__" ? suggested : timezone),
        working_days: workingDays,
        work_start: workStart,
        work_end: workEnd,
        quiet_start: quietStart,
        quiet_end: quietEnd,
        weekend_mode: weekendMode,
      });
      onSaved && onSaved(result);
    } catch (_error) {
      setError(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="schedule-settings">
      <summary>{schedule.confirmed ? "Working hours" : "Confirm your working hours"}</summary>
      <form onSubmit={submit}>
        <p className="schedule-intro">
          This lets PracticeGraph describe activity in your local day.
        </p>
        <div className="schedule-grid">
          <label>Timezone
            <select value={timezone} onChange={(event) => {
              const selected = event.target.value;
              setTimezone(selected);
              if (selected !== "__other__") setOtherTimezone("");
            }}>
              {TIMEZONES.map((name) => <option key={name}>{name}</option>)}
              <option value="__other__">Other IANA timezone</option>
            </select>
          </label>
          <label>Other IANA timezone
            <input value={otherTimezone}
                   placeholder="For example, Australia/Sydney"
                   onChange={(event) => setOtherTimezone(event.target.value)} />
          </label>
          <label>Work starts
            <input type="time" value={workStart}
                   onChange={(event) => setWorkStart(event.target.value)} />
          </label>
          <label>Work ends
            <input type="time" value={workEnd}
                   onChange={(event) => setWorkEnd(event.target.value)} />
          </label>
          <label>Quiet hours start
            <input type="time" value={quietStart}
                   onChange={(event) => setQuietStart(event.target.value)} />
          </label>
          <label>Quiet hours end
            <input type="time" value={quietEnd}
                   onChange={(event) => setQuietEnd(event.target.value)} />
          </label>
          <label>Weekend activity
            <select value={weekendMode}
                    onChange={(event) => setWeekendMode(event.target.value)}>
              <option value="exceptional">Usually an exception</option>
              <option value="normal">Part of my normal schedule</option>
            </select>
          </label>
        </div>
        <fieldset className="working-days">
          <legend>Working days</legend>
          {DAY_LABELS.map((label, day) => (
            <label key={label}>
              <input type="checkbox" checked={workingDays.includes(day)}
                     onChange={() => toggleDay(day)} /> {label}
            </label>
          ))}
        </fieldset>
        {error ? <p className="schedule-error" role="alert">Could not save that schedule</p> : null}
        <button className="primary" type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save schedule"}
        </button>
      </form>
    </details>
  );
}

// A hover/focus tooltip: shows `text` on hover or keyboard focus, nothing until
// asked. Used for the "what this measures" hints so the calm surface stays calm
// and the explanation is one gesture away.
function Head({ title, meta, hint }) {
  return (
    <div className="head">
      <div className="section-heading">
        <h2 className="section-title">{title}</h2>
        {hint ? <Info text={hint} /> : null}
      </div>
      {meta ? <span className="meta">{meta}</span> : null}
    </div>
  );
}

const BASELINE_DRAFT_KEY = "pg-baseline-draft-v1";
const BASELINE_SEED = [
  "Explain how a coding agent gathers context, acts through tools, and verifies its work — and where that loop can fail.",
  "Ask the agent to inspect and explain the relevant code before making a non-trivial change.",
  "Frame a task with an outcome, context, constraints, non-goals, and acceptance criteria.",
  "Provide concrete context such as files, errors, logs, screenshots, examples, or reproduction steps.",
  "Choose between direct implementation and a plan based on the size, risk, and familiarity of the work.",
  "Check the working state and deliberately control permissions, credentials, network access, and external actions.",
  "Give the agent a concrete verification loop using tests, builds, checks, reproduction steps, or visual review.",
  "Inspect the diff, challenge assumptions and edge cases, and explain why the result should work.",
  "Stop drift early, give specific corrective feedback, and safely undo or restart failed work.",
  "Keep repository instructions concise, preserve important plans, and separate unrelated workstreams.",
];

function readBaselineDraft() {
  try {
    const saved = JSON.parse(localStorage.getItem(BASELINE_DRAFT_KEY));
    if (Array.isArray(saved) && saved.every((item) => typeof item === "string")) {
      return saved.length ? saved : [""];
    }
  } catch (_error) {
    // A malformed local draft should never make the rest of the dashboard fail.
  }
  return BASELINE_SEED;
}

export function BaselineBuilder() {
  const [items, setItems] = useState(readBaselineDraft);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(BASELINE_DRAFT_KEY, JSON.stringify(items));
    } catch (_error) {
      // Keep the editor usable when the browser declines persistent storage.
    }
  }, [items]);

  const revise = (index, value) => {
    setItems((current) => current.map((item, i) => i === index ? value : item));
  };
  const remove = (index) => {
    setItems((current) => {
      const next = current.filter((_item, i) => i !== index);
      return next.length ? next : [""];
    });
  };

  return (
    <Card className="baselinebuilder">
      <Head title="AI working checklist" />
      <p className="baselineintro">
        Use these practices to review how you approach a task with AI. Adapt them
        to your work; this list does not assess or certify your ability.
      </p>
      <details className="baselinepractice">
        <summary>Try one practice on a small piece of work</summary>
        <p>Choose a task you can check yourself. Write the result you want, give the
          relevant context, and agree on a check before the agent starts. Afterward,
          inspect the output and name one thing you would change next time.</p>
        <p>For a document, check the audience, facts and requested format. For code,
          check the diff and run a relevant test. A checked box or a tool call alone
          cannot establish that you understand the result.</p>
        <p>References: <a href="https://learn.chatgpt.com/guides/best-practices"
          target="_blank" rel="noreferrer">Codex best practices</a>{" · "}
          <a href="https://code.claude.com/docs/en/best-practices"
            target="_blank" rel="noreferrer">Claude Code best practices</a>{" · "}
          <a href="https://code.claude.com/docs/en/permissions"
            target="_blank" rel="noreferrer">Permissions</a></p>
      </details>
      <button type="button" className="skcopy" onClick={() => setEditing(!editing)}>
        {editing ? "Done editing" : "Edit checklist"}
      </button>
      <ol className="baselinelist">
        {items.map((item, index) => (
          <li key={index}>
            {editing ? <><textarea value={item} rows={3}
                      aria-label={`Baseline practice ${index + 1}`}
                      placeholder="I can demonstrate…"
                      onChange={(event) => revise(index, event.target.value)} />
            <button type="button" className="baselineremove"
                    aria-label={`Remove baseline practice ${index + 1}`}
                    onClick={() => remove(index)}>Remove</button></> : <p>{item || "No practice written yet."}</p>}
          </li>
        ))}
      </ol>
      {editing ? <div className="baselineacts">
        <button type="button" onClick={() => setItems((current) => [...current, ""])}>
          Add practice
        </button>
        <span>Saved on this machine as you write · no score or certification</span>
      </div> : null}
    </Card>
  );
}



// Numbers pop in the same mono green the fact band uses — the sentence
// stays serif, the measurements stay measurements. Decimals stay whole
// ($0.24 is one number, not three fragments).
function reflectionRich(text) {
  const parts = text.split(/(\d[\d,]*(?:\.\d+)?(?::\d{2})?(?:h \d{2}m)?%?)/g);
  return parts.map((part, index) =>
    index % 2 ? <b className="n" key={index}>{part}</b> : part
  );
}

// The reading and detailed practice view retain the observation's own
// period, confidence, and caveat. Missing evidence is explained in Reading.
export function Observation({ view }) {
  const o = view.observation;
  if (!o?.title || !o.body || !o.period || !o.confidence || !o.caveat) return null;
  return (
    <section className={`observation obs-${o.confidence.replace(/\s+/g, "-")}`}>
      <div className="eyebrow">A pattern worth knowing</div>
      <h2 className="obstitle">{o.title}</h2>
      <p className="obsbody">{o.body}</p>
      <p className="obsqual">
        <span className="obsperiod">{o.period}</span>
        <span className="obsdot">·</span>
        <span className="obsconf">{o.confidence}</span>
      </p>
      <p className="obscaveat">{o.caveat}</p>
    </section>
  );
}



// The one composed summary left: the advisor's standing audit line. The
// tools and build summaries repeated what their cards already say (2026-09-10).
function surfaceSummary(view, surface) {
  if (surface === "advice") return view.advisor?.audit || null;
  return null;
}

// The insight slot every category grid opens with: the qualified
// observation when this is its home surface, then the light-green summary
// box — this surface's share of the reading plus its composed summary
// sentence — compact, static, no rotation, no band title. On a young
// install with no reading yet, practice keeps the plain noticed facts so
// the page never goes silent about behavior.
function SurfaceInsights({ view, surface }) {
  const summary = surfaceSummary(view, surface);
  // The practice loop owns a selected practice. Legacy timing/fitness
  // reflections must not introduce a competing interpretation above it.
  const o = view.observation;
  const observation = surface === "practice" && o?.id === "refire-after-failure"
    && !view.capability?.opportunity && !view.capability?.practice_progress
    && !view.capability?.practice_result;
  return <>
    {summary ? <div className="noticed inset"><p className="fact">{reflectionRich(summary)}</p></div> : null}
    {observation ? <Observation view={view} /> : null}
  </>;
}

function RateRow({ onDone }) {
  const [picked, setPicked] = useState(null);
  const [thanks, setThanks] = useState(false);
  const rate = async (n) => {
    setPicked(n);
    if (await checkin(n)) {
      setThanks(true);
      onDone?.();
    }
  };
  return (
    <div className="rate">
      <span className="q">How did today feel?</span>
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} className={picked === n ? "picked" : ""}
                onClick={() => rate(n)}>{n}</button>
      ))}
      {thanks
        ? <span className="thanks">noted</span>
        : <span className="muted mono" style={{ fontSize: 11 }}>
            1 = rough · 5 = great
          </span>}
    </div>
  );
}

// News: the central feed (a briefings catalog artifact) shown where the daily
// brief used to sit. Two kinds share the feed - editorial field notes
// (paper/announcement) and operational pushes (admin/release). A source pill
// says where it came from (this org, or the public registry). Display-only:
// a briefing url opens on the person's click; the endpoint never fetches it,
// and which items were seen/dismissed stays on this machine.
// News is about the world AROUND these tools - model/tool releases, updates,
// papers, posts - sourced from RSS feeds (parsed server-side into clean text).
function NewsMeta({ source, attention }) {
  return (
    <div className="newsmeta">
      <NewsUrgency attention={attention} />
      {source ? <span className="newssrc">{source}</span> : null}
    </div>
  );
}

function NewsDisclosure({ id, title, url, teaser, meta, kind, revealed, onPreview,
                          onPreviewEnd, onToggle, children }) {
  const panelId = `news-details-${id}`;
  return (
    <article className={`newsentry k-${kind}${revealed ? " is-revealed" : ""}`}

             onBlurCapture={(event) => {
               if (!event.currentTarget.contains(event.relatedTarget)) onPreviewEnd();
             }}>
      <a className="newstitlelink" href={httpsUrl(url)} target="_blank" rel="noreferrer">{title}</a>
      <span className="newsteaser">{teaser}</span>
      <button className="newstitlebutton" type="button"
              aria-label={title} aria-expanded={revealed} aria-controls={panelId}
              onClick={() => onToggle(id)}>
        {revealed ? "Hide summary" : "Read summary"}
      </button>
      {meta}
      <div className="newsreveal" id={panelId} aria-hidden={!revealed}
           inert={revealed ? undefined : "true"}>
        <div className="newsrevealinner">{children}</div>
      </div>
    </article>
  );
}

// The dated shelf behind an editorial feed: today plus the archived days.
// Picking a day fetches that day's edition through /api/feed-day; "today"
// returns to the live feed. Days the server never archived simply are not
// offered — an absent day is absent, never guessed at.
function FeedDays({ days, active, onPick }) {
  if (!days || days.length === 0) return null;
  return (
    <div className="feeddays" role="group" aria-label="Edition archive">
      <button className={active == null ? "feedday on" : "feedday"}
              aria-pressed={active == null} onClick={() => onPick(null)}>Latest edition</button>
      {days.map((day) => (
        <button key={day}
                aria-pressed={active === day} className={active === day ? "feedday on" : "feedday"}
                onClick={() => onPick(day)}>{day}</button>
      ))}
    </div>
  );
}

// Fetch one archived day when picked; null day = the live feed. Errors show
// as an honest empty day, never as stale content presented as that day's.
function useFeedDay(channel) {
  const [day, setDay] = useState(null);
  const [items, setItems] = useState(null);
  const [repos, setRepos] = useState([]);
  const request = useRef(0);
  const pick = (chosen) => {
    const revision = ++request.current;
    setDay(chosen);
    if (chosen == null) { setItems(null); setRepos([]); return; }
    setItems(null);
    setRepos([]);
    fetchFeedDay(channel, chosen)
      .then((r) => { if (revision === request.current) {setItems(r.items); setRepos(r.repos || []);} })
      .catch(() => {if (revision === request.current) setItems([]);});
  };
  return { day, items, repos, pick };
}

function News({ view }) {
  // One feed, the curated one. The briefings fallback rendered a second
  // stream of external content in the same slot — cut in the page diet
  // (2026-08-13): a product about your own practice gets one news surface.
  const liveItems = activeNews(view);
  // Previous days: the local shelf, today's own archive entry left out.
  const shelfDays = view.news_days || [];
  const shelf = useFeedDay("news");
  const richItems = shelf.day == null ? liveItems : (shelf.items || []);
  const [expandedId, setExpandedId] = useState(null);
  const [previewId, setPreviewId] = useState(null);
  const revealedId = previewId ?? expandedId;
  const endPreview = () => setPreviewId(null);
  const toggleItem = (id) => {
    setPreviewId(null);
    setExpandedId((current) => toggleNewsId(current, id));
    if (shelf.day == null) changeNews({action: "read", id}).catch(() => {});
  };
  return (
    <Card className="span2">
      <Head title="Tool news"
            hint={"News about the tools you use. Source pages open when you choose them. "
              + "Previous days stay readable for two weeks."} />
      {shelf.day == null ? <ContentStatus status={view.feed_status?.news} label="News" /> : null}
      <NewsPreferences />
      <FeedDays days={shelfDays} active={shelf.day} onPick={shelf.pick} />
      {shelf.day != null && richItems.length === 0 ? (
        <p className="mdefault">
          {shelf.items == null ? "Opening that day…"
            : "No items for this date."}
        </p>
      ) : null}
      <div className="newsfeed">
        {richItems.map((item) => {
          return (
            <NewsDisclosure key={item.id} id={item.id} title={item.title} url={item.url}
                            teaser={item.hook}
                            kind={item.kind} revealed={revealedId === item.id}
                            onPreview={setPreviewId} onPreviewEnd={endPreview}
                            onToggle={toggleItem}
                            meta={<NewsMeta kind={item.kind} source={item.source}
                                            url={item.url} attention={item.attention} />}>
              <p className="richnewssummary">{item.summary}</p>
              <p className="richnewswhy"><span>Why this is here</span>{item.why}</p>
            </NewsDisclosure>
          );
        })}
      </div>
    </Card>
  );
}

// Let's build: one buildable idea per card from the APIs behind the
// harnesses (served editions, cached for offline reading). The hook is the face; the
// recipe and the official-docs link sit behind a fold. Grouped by vendor so
// a person can read their own side first.
function BuildIdeas({ view }) {
  const [draftError, setDraftError] = useState("");
  const useIdea = idea => {
    try {sessionStorage.setItem("pg-build-practice", JSON.stringify({title: idea.title, task: `${idea.hook}\n\nFirst version:\n${idea.steps.join("\n")}`})); window.location.hash = "practice";}
    catch {setDraftError("The draft could not be stored. Copy the build steps into your practice plan.");}
  };
  const liveItems = view.build_ideas || [];
  const shelfDays = view.build_ideas_days || [];
  const shelf = useFeedDay("build-ideas");
  const items = shelf.day == null ? liveItems : (shelf.items || []);
  const repos = shelf.day == null
    ? (view.build_repos || [])
    : (shelf.repos || []);
  return (
    <>
    <Card className="span2">
      <Head title="Projects to try"
            hint={"One buildable idea per card from the APIs behind your "
              + "tools - what it is, why it is worth an afternoon, and the "
              + "first-version steps. Links are the official docs; nothing "
              + "opens unless you click it."} />
      {shelf.day == null ? <ContentStatus status={view.feed_status?.["build-ideas"]} label="Build ideas" /> : null}
      <p>Build one small result, then check it on a real example.</p>
      {draftError ? <p role="alert">{draftError}</p> : null}
      <FeedDays days={shelfDays} active={shelf.day} onPick={shelf.pick} />
      {shelf.day != null && items.length === 0 ? (
        <p className="mdefault">
          {shelf.items == null ? "Opening that day…"
            : "No items for this date."}
        </p>
      ) : null}
      <div className="build-projects">
          {items.map((idea) => (
            <details className="buildidea" key={idea.id}>
              <summary>
                <span className="buildtitle">{idea.title}</span>
                <span className="buildhook">{idea.hook}</span><span className="build-action-label">View build steps</span>
                <span className="buildfeature">{idea.feature} · {idea.api_label || idea.api}</span>
              </summary>
              <div className="buildbody">
                <button onClick={() => useIdea(idea)}>Use as a practice</button>
                <p>{idea.summary}</p>
                <p className="buildstepslabel">A first version</p>
                <ol className="buildsteps">
                  {idea.steps.map((step) => <li key={step}>{step}</li>)}
                </ol>
                <p className="buildwhy">
                  <span>Why this one</span>{idea.why}
                </p>
                <p className="buildlink">
                  <a href={idea.url} target="_blank" rel="noreferrer">
                    {idea.source} ↗
                  </a>
                  {idea.repo ? (
                    <a className="buildreceipt" href={idea.repo}
                       target="_blank" rel="noreferrer">
                      see it real: {repoLeaf(idea.repo)} ↗
                    </a>
                  ) : null}
                </p>
              </div>
            </details>
          ))}
        </div>
    </Card>
    {/* The repository shelf is its own card of tiles (design decision 2026-09-17):
        inside the ideas card, in small mono type, it read as a footnote. The
        names now carry the same display type as the idea titles. */}
    {repos.length > 0 ? (
      <Card className="span2 buildrepos">
        <Head title="Worth a look on GitHub"
              meta={repos.length === 1 ? "1 repository" : `${repos.length} repositories`}
              hint={"Open-source projects close to these ideas. Each one lists what it "
                + "needs and where it falls short. Nothing opens unless you click it."} />
        <p>Working code to read or run next to the ideas above.</p>
        <div className="repogrid">
          {repos.map((pick) => {
            const [owner, ...rest] = pick.name.split("/");
            const leaf = rest.join("/");
            return (
              <article className="repopick" key={pick.id}>
                <a className="reponame" href={pick.url} target="_blank" rel="noreferrer"
                   aria-label={`${pick.name} on GitHub`}>
                  <GitHubMark size={18} />
                  <span className="repofull">
                    {leaf ? <span className="repoowner">{owner}/</span> : null}
                    <span className="repoleaf">{leaf || owner}</span>
                  </span>
                  <span className="repoarrow" aria-hidden="true">↗</span>
                </a>
                <p className="repowhat">{pick.what}</p>
                <details><summary>Requirements and limitations</summary><p className="repowhy">{pick.why}</p>
                {pick.caveat ? (
                  <p className="repocaveat">{pick.caveat}</p>
                ) : null}</details>
              </article>
            );
          })}
        </div>
      </Card>
    ) : null}
    </>
  );
}

// The owner/name leaf of a github project URL, for the receipt label.
function repoLeaf(url) {
  const path = url.replace(/^https:\/\/github\.com\//, "").replace(/\/$/, "");
  return path || url;
}

// Only positively classified human events support this observation.
export function TrainingBody({ view }) {
  const n = view.noticed;
  if (!n?.attention_confident || !n.attention_active_days_28d) return null;
  return <>
    <p className="relheadline">On {count(n.attention_high_switch_days_28d)} of {count(n.attention_active_days_28d)} observed
      days, your recorded interactions moved between workstreams at least three times.</p>
    <p className="muted">Last 28 days · human-origin interactions in supported tools.
      Agent and tool traffic is excluded. Switching alone does not indicate a problem.</p>
  </>;
}

export function Training({ view }) {
  if (!view.noticed?.attention_confident || !view.noticed.attention_active_days_28d) return null;
  return <Card><Head title="Moving between workstreams" /><TrainingBody view={view} /></Card>;
}

export function QuietHours({ view }) {
  const q = view.quiet_hours;
  if (!q || !view.schedule?.confirmed) return null;
  return <Card className="quiet-hours">
    <Head title="Your quiet hours"
          meta={`${q.from_day} to ${q.to_day} · ${view.schedule.quiet_start}–${view.schedule.quiet_end}`}
          hint={"Days on which a supported tool recorded a human interaction inside your "
            + "confirmed quiet hours. Agent activity is excluded; work outside supported "
            + "tools is not observed."} />
    <p className="relheadline">{quietHoursLine(q)}</p>
    <details className="stat-details"><summary>How this is counted</summary>
      <p>A day is observed when a supported tool records a human-origin interaction.
        One or more such interactions during your confirmed quiet hours counts as a quiet-hours day.
        Background agent activity is excluded. Weeks may have different coverage.
        Based on positively identified human interactions in supported tools, not your complete working day.</p>
    </details>
  </Card>;
}

export function WorkPatterns({view}) {
  const q = view.schedule?.confirmed ? view.quiet_hours : null;
  const o = view.observation;
  const hasObservation = o?.title && o.body && o.period && o.confidence && o.caveat;
  const hasSwitching = view.noticed?.attention_confident && view.noticed.attention_active_days_28d;
  if (!hasObservation && !q?.observed_days && !hasSwitching) return null;
  return <Card className="work-patterns">
    <Head title="Your activity patterns" />
    {hasObservation ? <Observation view={view} /> : null}
    {q?.observed_days > 0 && o?.id !== "quiet-hours-activity" ? <section className="quiet-hours">
      <h3>Activity during quiet hours</h3>
      <p>{quietHoursLine(q)}</p>
      <p className="muted">{q.from_day} to {q.to_day} · {view.schedule.quiet_start}–{view.schedule.quiet_end}
        <Info text="Based on human interactions recorded by supported tools. Background agent activity is excluded; this is not your complete working day." /></p>
    </section> : null}
    {hasSwitching ? <section><h3>Moving between workstreams</h3><TrainingBody view={view} /></section> : null}
  </Card>;
}

// What it means, against the reader's own last week (COPY_RULES rules 1-2).
function quietHoursLine(q) {
  const days = q.observed_days;
  const lead = q.quiet_days === 0
    ? `No recorded AI-tool activity during quiet hours across ${days} observed days`
    : q.quiet_days === days
      ? `AI-tool activity was recorded during quiet hours on all ${days} observed days`
      : `AI-tool activity was recorded during quiet hours on ${q.quiet_days} of ${days} observed days`;
  if (!(q.prior_observed_days > 0)) return `${lead}.`;
  const now = q.quiet_days / days;
  const before = q.prior_quiet_days / q.prior_observed_days;
  const word = now === before ? "same as" : now > before ? "more than" : "fewer than";
  return `${lead} — ${word} the week before (${q.prior_quiet_days} of ${q.prior_observed_days}).`;
}

// An in-page focus/break countdown, native to the dashboard. A block runs in
// the browser; start and completion record to the local API so the counters
// still reach the daily record. Nothing hops out through a protocol.
// Focus block maps to the "block-*" events; both break kinds to "break-*" —
// the closed lifecycle the local API records (BLOCK_EVENTS). The rest is a
// break that happens to be long; the server's event set stays closed.
const TIMER_MIN = { focus: 90, break: 10, rest: 25 };
const TIMER_EVENT = { focus: "block", break: "break", rest: "break" };
const TIMER_LABEL = { focus: "focus block", break: "quick break",
                     rest: "long rest" };

function fmtClock(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// The guided break (design decision 2026-08-21): while a break or rest runs,
// the page dims and this panel is the only live thing — a big countdown
// and instructions that advance with the clock. The instructions push the
// break AWAY from the screen (water, movement, a window); the panel is
// designed to be left, not watched.
const BREAK_STEPS = {
  break: [
    { at: 0, text: "Stand up now. Fill a glass of water." },
    { at: 60, text: "Move — stairs, a corridor walk, a few slow stretches." },
    { at: 240, text: "Find a window. Look at the farthest thing you can "
      + "see, then at something moving." },
    { at: 480, text: "Head back. One slow breath before the code." },
  ],
  rest: [
    { at: 0, text: "Water first. Shoes on if you can." },
    { at: 120, text: "Leave the screen behind — outside if at all "
      + "possible. Distance is the point." },
    { at: 1200, text: "On the way back: water again, and one long look at "
      + "something far away." },
  ],
};

function BreakPanel({ timer }) {
  const total = TIMER_MIN[timer.kind] * 60;
  const [left, setLeft] = useState(() =>
    Math.max(0, Math.round((timer.endAt - Date.now()) / 1000)));
  useEffect(() => {
    const tick = () =>
      setLeft(Math.max(0, Math.round((timer.endAt - Date.now()) / 1000)));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [timer.endAt]);
  const elapsed = total - left;
  const steps = BREAK_STEPS[timer.kind];
  const current = [...steps].reverse().find((s) => elapsed >= s.at) || steps[0];
  return (
    <section className="breakpanel">
      <div className="bpclock">{fmtClock(left)}</div>
      <div className="bpbody">
        <p className="bplead">
          This screen has nothing more for you — the break happens away
          from it. The clock keeps running while you're gone.
        </p>
        <ol className="bpsteps">
          {steps.map((step) => (
            <li key={step.at}
                className={step === current ? "on"
                  : elapsed > step.at ? "done" : ""}>
              {step.text}
            </li>
          ))}
        </ol>
        <details className="bpgame">
          <summary>
            Stuck at the desk? A bounded puzzle — it ends when the break ends.
          </summary>
          <BreakGames />
        </details>
        {timer.stop ? (
          <button type="button" className="bpstop"
                  onClick={() => timer.stop()}>
            End the break early
          </button>
        ) : null}
      </div>
    </section>
  );
}

// The bounded puzzles for a break that cannot leave the desk: falling
// blocks or a sudoku, one chooser, one mounted at a time. Both are
// structurally bounded — they live inside the break panel and unmount
// with the timer, so neither can outlive the break.
function BreakGames() {
  const [game, setGame] = useState(null); // "blocks" | "sudoku" | null
  return (
    <div className="bpgames">
      <div className="bpgamepick">
        <button type="button" className={game === "blocks" ? "on" : ""}
                onClick={() => setGame("blocks")}>Falling blocks</button>
        <button type="button" className={game === "sudoku" ? "on" : ""}
                onClick={() => setGame("sudoku")}>Sudoku</button>
      </div>
      {game === "blocks" ? <Tetris /> : null}
      {game === "sudoku" ? <Sudoku /> : null}
    </div>
  );
}

// The sudoku. A fresh legal board every break: the classic base pattern
// shuffled by digit relabeling plus row/column swaps inside bands — every
// transform preserves validity — then holes dug for the givens that stay.
// Low-stakes on purpose: no clock, no difficulty ladder, no best anything,
// and "solved" means the grid is legally full, whichever completion the
// person found.
const SUDOKU_GIVENS = 36;

function sudokuShuffle(items) {
  const list = [...items];
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  return list;
}

function sudokuBands() {
  return sudokuShuffle([0, 1, 2])
    .flatMap((band) => sudokuShuffle([0, 1, 2]).map((i) => band * 3 + i));
}

function sudokuPuzzle() {
  const base = (r, c) => (Math.floor(r / 3) + (r % 3) * 3 + c) % 9;
  const digits = sudokuShuffle([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  const rows = sudokuBands();
  const cols = sudokuBands();
  const holes = new Set(
    sudokuShuffle([...Array(81).keys()]).slice(0, 81 - SUDOKU_GIVENS));
  return Array.from({ length: 9 }, (_, r) =>
    Array.from({ length: 9 }, (_, c) => {
      const value = digits[base(rows[r], cols[c])];
      return holes.has(r * 9 + c)
        ? { v: 0, given: false } : { v: value, given: true };
    }));
}

function sudokuConflict(cells, r, c) {
  const v = cells[r][c].v;
  if (!v) return false;
  for (let i = 0; i < 9; i++) {
    if (i !== c && cells[r][i].v === v) return true;
    if (i !== r && cells[i][c].v === v) return true;
  }
  const br = Math.floor(r / 3) * 3;
  const bc = Math.floor(c / 3) * 3;
  for (let y = br; y < br + 3; y++) {
    for (let x = bc; x < bc + 3; x++) {
      if ((y !== r || x !== c) && cells[y][x].v === v) return true;
    }
  }
  return false;
}

function Sudoku() {
  const [cells, setCells] = useState(sudokuPuzzle);
  const [sel, setSel] = useState(null);
  const put = (r, c, v) => setCells((cur) => {
    if (cur[r][c].given) return cur;
    const next = cur.map((row) => row.map((cell) => ({ ...cell })));
    next[r][c].v = v;
    return next;
  });
  const onKey = (event) => {
    if (!sel) return;
    const { r, c } = sel;
    if (/^[1-9]$/.test(event.key)) {
      event.preventDefault();
      put(r, c, Number(event.key));
      return;
    }
    if (event.key === "Backspace" || event.key === "Delete"
        || event.key === "0") {
      event.preventDefault();
      put(r, c, 0);
      return;
    }
    const arrows = { ArrowLeft: [0, -1], ArrowRight: [0, 1],
                     ArrowUp: [-1, 0], ArrowDown: [1, 0] };
    const move = arrows[event.key];
    if (move) {
      event.preventDefault();
      setSel({ r: Math.min(8, Math.max(0, r + move[0])),
               c: Math.min(8, Math.max(0, c + move[1])) });
    }
  };
  const solved = cells.every((row, r) =>
    row.every((cell, c) => cell.v !== 0 && !sudokuConflict(cells, r, c)));
  return (
    <div className="sudoku" tabIndex={0} onKeyDown={onKey}
         aria-label="Sudoku. Click a cell, then type 1 to 9.">
      <div className="sudogrid" aria-hidden="true">
        {cells.map((row, r) => row.map((cell, c) => (
          <span key={r * 9 + c}
                className={"sudocell"
                  + (cell.given ? " given" : "")
                  + (sel && sel.r === r && sel.c === c ? " sel" : "")
                  + (!cell.given && cell.v
                     && sudokuConflict(cells, r, c) ? " off" : "")}
                onClick={() => setSel({ r, c })}>
            {cell.v || ""}
          </span>
        )))}
      </div>
      <div className="tetside">
        {solved ? (
          <>
            <p className="tetlines">solved — that's the stop</p>
            <button className="tetagain"
                    onClick={() => { setCells(sudokuPuzzle()); setSel(null); }}>
              Another
            </button>
          </>
        ) : (
          <p className="tethint">Click a cell, then type 1-9. Arrows move.</p>
        )}
      </div>
    </div>
  );
}

// The bounded puzzle for a break that cannot leave the desk. Deliberately
// low-stakes: no levels, no speeding up, no best score — only lines this
// break, in the page's own two inks. The hard stop is structural: the game
// lives inside the break panel and unmounts with the timer, so it cannot
// outlive the break.
const TET_W = 10;
const TET_H = 14;
const TET_TICK_MS = 650;
const TET_SHAPES = [
  [[1, 1, 1, 1]],
  [[1, 1], [1, 1]],
  [[0, 1, 0], [1, 1, 1]],
  [[1, 0, 0], [1, 1, 1]],
  [[0, 0, 1], [1, 1, 1]],
  [[1, 1, 0], [0, 1, 1]],
  [[0, 1, 1], [1, 1, 0]],
];

function tetRotate(shape) {
  return shape[0].map((_, x) => shape.map((row) => row[x]).reverse());
}

function tetFits(board, shape, ox, oy) {
  return shape.every((row, y) => row.every((cell, x) => {
    if (!cell) return true;
    const bx = ox + x;
    const by = oy + y;
    return bx >= 0 && bx < TET_W && by < TET_H
      && (by < 0 || board[by][bx] === 0);
  }));
}

function tetSpawn() {
  const shape = TET_SHAPES[Math.floor(Math.random() * TET_SHAPES.length)];
  return { shape, x: Math.floor((TET_W - shape[0].length) / 2), y: -1 };
}

function Tetris() {
  const emptyBoard = () =>
    Array.from({ length: TET_H }, () => Array(TET_W).fill(0));
  const [game, setGame] = useState(() => ({
    board: emptyBoard(), piece: tetSpawn(), lines: 0, over: false,
  }));

  const step = (move) => setGame((g) => {
    if (g.over) return g;
    const p = g.piece;
    if (move === "left" || move === "right") {
      const nx = p.x + (move === "left" ? -1 : 1);
      return tetFits(g.board, p.shape, nx, p.y)
        ? { ...g, piece: { ...p, x: nx } } : g;
    }
    if (move === "rotate") {
      const shape = tetRotate(p.shape);
      return tetFits(g.board, shape, p.x, p.y)
        ? { ...g, piece: { ...p, shape } } : g;
    }
    // gravity / soft drop
    if (tetFits(g.board, p.shape, p.x, p.y + 1)) {
      return { ...g, piece: { ...p, y: p.y + 1 } };
    }
    // lock, clear, respawn
    const board = g.board.map((row) => [...row]);
    p.shape.forEach((row, y) => row.forEach((cell, x) => {
      if (cell && p.y + y >= 0) board[p.y + y][p.x + x] = 1;
    }));
    const kept = board.filter((row) => row.some((cell) => cell === 0));
    const cleared = TET_H - kept.length;
    while (kept.length < TET_H) kept.unshift(Array(TET_W).fill(0));
    const piece = tetSpawn();
    const over = !tetFits(kept, piece.shape, piece.x, piece.y);
    return { board: kept, piece, lines: g.lines + cleared, over };
  });

  useEffect(() => {
    const id = setInterval(() => step("down"), TET_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const onKey = (event) => {
    const moves = { ArrowLeft: "left", ArrowRight: "right",
                    ArrowDown: "down", ArrowUp: "rotate" };
    const move = moves[event.key];
    if (!move) return;
    event.preventDefault();
    step(move);
  };

  const cells = game.board.map((row) => [...row]);
  if (!game.over) {
    game.piece.shape.forEach((row, y) => row.forEach((cell, x) => {
      const by = game.piece.y + y;
      if (cell && by >= 0) cells[by][game.piece.x + x] = 2;
    }));
  }
  return (
    <div className="tetris" tabIndex={0} onKeyDown={onKey}
         aria-label="Falling blocks. Click the board, then use the arrow keys.">
      <div className="tetboard" aria-hidden="true">
        {cells.map((row, y) => (
          <div className="tetrow" key={y}>
            {row.map((cell, x) => (
              <span key={x}
                    className={cell === 2 ? "tetcell live"
                      : cell === 1 ? "tetcell set" : "tetcell"} />
            ))}
          </div>
        ))}
      </div>
      <div className="tetside">
        <p className="tetlines">lines <b>{game.lines}</b></p>
        {game.over ? (
          <button className="tetagain"
                  onClick={() => setGame({ board: emptyBoard(),
                    piece: tetSpawn(), lines: 0, over: false })}>
            Again
          </button>
        ) : (
          <p className="tethint">Click the board, then arrows. ↑ rotates.</p>
        )}
      </div>
    </div>
  );
}

// Read the day's state into a break plan (design decision 2026-08-21, built on
// the recovery evidence the owner supplied): the everyday nudge is the
// 10-minute movement break, and two moments escalate to the 25-minute rest
// instead. Two or more completed blocks means the day has earned a real
// pause, not another squeeze; a run of quick re-fires after failures is
// the moment attention starts chasing novelty, which is exactly when a
// two-minute reset does the least. Thresholds are guidance about the
// moment, never a judgment of the person.
function breakPlan() {
  // Timers are voluntary tools. Reply timing cannot establish a need for rest.
  return null;
}

function FocusTimer({ onDone, onTimerChange, view, coaching, load,
                      autoBreak }) {
  const [kind, setKind] = useState(null);       // "focus" | "break" | "rest" | null
  const [left, setLeft] = useState(0);          // seconds remaining
  const endRef = useRef(0);
  const doneRef = useRef(false);
  const stopRef = useRef(() => {});
  const suggest = breakPlan(view, coaching, load);
  // The toast's intent: start the break the moment the window opens, at
  // the kind the plan calls for. Mount-only by design — the intent was
  // consumed from the hash exactly once upstream.
  useEffect(() => {
    if (autoBreak) {
      start(suggest && suggest.which === "rest" ? "rest" : "break");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stop = (completed) => {
    // Only a completed block records a completion; a manual stop mid-block
    // records nothing (there is no "cancelled" event — the start already
    // counted the attempt).
    if (kind && completed && !doneRef.current) {
      doneRef.current = true;
      recordBlock(`${TIMER_EVENT[kind]}-completed`)
        .then(() => onDone && onDone())
        .catch(() => {});
    }
    setKind(null);
    setLeft(0);
    if (onTimerChange) onTimerChange(null);
  };

  const start = (which) => {
    doneRef.current = false;
    endRef.current = Date.now() + TIMER_MIN[which] * 60 * 1000;
    setLeft(TIMER_MIN[which] * 60);
    setKind(which);
    recordBlock(`${TIMER_EVENT[which]}-started`).catch(() => {});
    // The mirror carries a live stop so the break panel — the one surface
    // a running break has — can end it. The ref always points at the
    // latest render's stop.
    if (onTimerChange) {
      onTimerChange({ kind: which, endAt: endRef.current,
                      stop: () => stopRef.current(false) });
    }
  };
  stopRef.current = stop;

  useEffect(() => {
    if (!kind) return undefined;
    const tick = () => {
      const remaining = Math.max(0, Math.round((endRef.current - Date.now()) / 1000));
      setLeft(remaining);
      if (remaining <= 0) stop(true);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [kind]);

  if (kind === "focus") {
    return (
      <div className={`timer running ${kind}`}>
        <div className="tclock">{fmtClock(left)}</div>
        <div className="tmeta">
          <span className="tlabel">{TIMER_LABEL[kind]} in progress</span>
          <button className="tstop" onClick={() => stop(false)}>Stop</button>
        </div>
      </div>
    );
  }
  if (kind) {
    // A running break renders NOTHING here: the takeover panel is the one
    // break surface — clock, guidance and the way out all live there. Two
    // clocks counting the same break down read as a bug.
    return null;
  }
  // Idle: a proactive nudge when the moment calls for it, and the button
  // that matches the plan carries the emphasis. All three choices stay
  // visible with their purpose on the button (design decision 2026-08-21: a
  // newcomer should be able to read what each is for), and the plan only
  // moves the emphasis, never hides an option.
  const which = suggest ? suggest.which : null;
  return (
    <div className="timeridle">
      {suggest ? (
        <p className="tsuggest">
          <span className="tsdot" aria-hidden="true" />
          {suggest.line}
        </p>
      ) : null}
      <div className="acts">
        <button className={!which || which === "focus" ? "primary" : ""}
                onClick={() => start("focus")}>
          <span className="actname">▶&nbsp; Focus block</span>
          <span className="actsub">90 min on one thing</span>
        </button>
        <button className={which === "break" ? "primary" : ""}
                onClick={() => start("break")}>
          <span className="actname">Quick break · 10 min</span>
          <span className="actsub">stand up, move, look far</span>
        </button>
        <button className={which === "rest" ? "primary" : ""}
                onClick={() => start("rest")}>
          <span className="actname">Long rest · 25 min</span>
          <span className="actsub">leave the screens behind</span>
        </button>
      </div>
    </div>
  );
}

// A slim strip of the day's actual controls, directly under the header where
// they are easy to reach: the focus/break timer (with its coach-driven nudge)
// and the close-the-day shutdown. Everything below the bar stays a read-only
// surface. The close flow expands inline here rather than living in a card at
// the bottom of the page.
function ActionBar({ view, coaching, load, refresh, onTimerChange,
                     autoBreak }) {
  const alreadyClosed = Boolean(view.dayclose && view.dayclose.closed);
  const [closeOpen, setCloseOpen] = useState(false);
  const [closed, setClosed] = useState(alreadyClosed);
  const onClosed = () => {
    setClosed(true);
    setCloseOpen(false);
    refresh();
  };
  return (
    <section className="actionbar" aria-label="Today's actions">
      <div className="abrow">
        <FocusTimer onDone={refresh} onTimerChange={onTimerChange}
                    view={view} coaching={coaching} load={load}
                    autoBreak={autoBreak} />
        <div className="abclose">
          {closed ? (
            <span className="dcclosed">Day closed.</span>
          ) : (
            <button className={`dcopen ${closeOpen ? "on" : ""}`}
                    aria-expanded={closeOpen}
                    onClick={() => setCloseOpen((v) => !v)}>
              Close the day
            </button>
          )}
        </div>
      </div>
      {closeOpen && !closed ? (
        <CloseTheDayBody view={view} onClosed={onClosed} />
      ) : null}
    </section>
  );
}

// The Advisor's verdict card ("If you change one thing") left the interface
// on the owner's call (2026-08-21). Its standing audit line survives as the
// advice section's summary — the server still mints the whole board.

// Your models: the staffing view, split by harness. WHICH model fills each
// role comes from the served models.json benchmark artifact (view.
// model_guidance — centralized, so recasting the roles is a publish on the
// TeamLandi side, never a client release). What each role is FOR is stable
// UI copy; what you actually ran comes from your own range rows, grouped
// per tool so Claude Code and Codex read separately.
const MODEL_ROLE_COPY = {
  strongest: {
    label: "For demanding work",
    use: "Work where a mistake is expensive to discover later — designs, "
      + "migrations, the bug that resisted a cheaper attempt.",
  },
  everyday: {
    label: "The everyday model",
    use: "Most feature work, multi-file edits, standard debugging — the "
      + "sound default for a routine session.",
  },
  fast: {
    label: "The fast model",
    use: "Mechanical edits, renames, boilerplate, summaries — work where "
      + "speed beats depth.",
  },
};

// The range rows carry the store's tool ids; the guidance carries the
// benchmark's. Normalize both to the benchmark's so usage lands under the
// right harness heading.
function harnessOf(tool) {
  return /codex/i.test(tool) ? "codex" : "claude_code";
}

function harnessUsage(view) {
  const ranges = view.ranges || [];
  const range = ranges.find((r) => r.range_id === "range-month")
    || ranges.find((r) => r.range_id === "range-week");
  const byTool = new Map();
  for (const row of (range && range.models) || []) {
    const key = harnessOf(row.tool);
    const entry = byTool.get(key) || { turns: 0, cost: 0, models: new Map() };
    entry.turns += row.assistant_turns;
    entry.cost += row.cost_micro_usd;
    entry.models.set(row.model,
      (entry.models.get(row.model) || 0) + row.cost_micro_usd);
    byTool.set(key, entry);
  }
  return { range, byTool };
}

// What is currently set versus the catalog's recommended floor, in one or
// two sentences composed from the served catalog plus this machine's own
// pins (both computed server-side). Never a judgment of the person — a
// reading of a config file against a published ladder.
function catalogLine(c) {
  const pinnedModel = c.models.find((m) => m.pinned);
  const pinnedEffort = c.efforts.find((e) => e.pinned);
  const recommended = c.efforts.find((e) => e.recommended);
  const everyday = c.models.find((m) => m.role === "everyday");
  const effort = c.pinned_effort ? ` · ${c.pinned_effort}` : "";
  const start = c.pinned_model
    ? `You start on ${c.pinned_model}${effort}`
      + (pinnedModel ? "." : " — outside this catalog's ladder.")
    : "No pinned model — sessions start on the tool's own choice"
      + (c.pinned_effort ? `, at ${c.pinned_effort} effort.` : ".");
  const costlyPin = pinnedEffort && pinnedEffort.tone === "costly";
  const strongestPin = pinnedModel && pinnedModel.role === "strongest";
  if ((costlyPin || strongestPin) && everyday && recommended) {
    return `${start} Recommended: ${everyday.model} · ${recommended.level}, `
      + "stepping up per task.";
  }
  if (pinnedEffort && pinnedEffort.recommended) {
    return `${start} That is the recommended floor.`;
  }
  return start;
}

// What each pin outcome means, in a sentence. The engine speaks in closed
// codes; the page owns the words (COPY_RULES: meaning first).
const PIN_OUTCOME_COPY = {
  pinned: "Done - the file now carries the new start. The previous version "
    + "sits beside it as a backup.",
  refused_unreadable: "That config file did not parse cleanly, so it was "
    + "left exactly as it was. Fix it by hand first.",
  refused_value: "This model cannot be written into that tool's config "
    + "automatically. Nothing was changed - set it by hand in the tool's "
    + "own settings.",
  refused_profile_active: "Your Codex config runs an active profile that "
    + "would override this - change the model inside that profile instead.",
  verify_failed: "The change did not read back as expected, so it was "
    + "undone - the file is exactly as it was, with a backup beside it.",
  io_error: "The file could not be written. Nothing was changed.",
  unknown_project: "That project folder is not in your recent sessions.",
  invalid_pin: "That model is not on this tool's ladder.",
};

// The one-click default: apply the catalog's recommended floor to the
// tool's own config. The button names the file it will change before it
// changes anything; the outcome sentence carries what actually happened.
export function PinControl({ c, view, refresh }) {
  const [reviewing, setReviewing] = useState(false);
  const [scope, setScope] = useState("machine");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const everyday = c.models.find((m) => m.role === "everyday");
  const recommended = c.efforts.find((e) => e.recommended);
  // No everyday rung, or a catalog that does not say what the config takes
  // for it: no button. The server refuses the same case (row.pin null).
  if (!everyday || !everyday.pin) return null;
  // Already starting there: nothing to offer.
  const already = everyday.pinned
    && (!recommended || c.pinned_effort === recommended.level);
  if (already && !result) return null;
  const projects = c.tool === "claude_code" ? (view.pin_projects || []) : [];
  const effort = recommended ? recommended.level : null;
  const target = scope === "machine"
    ? "this machine's own config"
    : `the ${scope} project's config`;
  const apply = async () => {
    setBusy(true);
    const body = { tool: c.tool, model: everyday.model, scope: "machine" };
    if (effort) body.effort = effort;
    if (scope !== "machine") { body.scope = "project"; body.project = scope; }
    try {
      const outcome = await pinModel(body);
      setResult(outcome);
      if (outcome.ok) refresh();
    } catch {setResult({ok: false, error: "The change could not be saved. Try again."});}
    finally {setBusy(false);}
  };
  return (
    <div className="pincontrol">
      {!reviewing && !result ? <button className="primary" onClick={() => setReviewing(true)}>Review change</button> : null}
      {result ? (
        <p className={result.ok ? "pindone" : "pinrefused"}>
          {PIN_OUTCOME_COPY[result.outcome]
            || PIN_OUTCOME_COPY[result.error]
            || "That did not work."}
          {result.path ? <span className="pinpath"> {result.path}</span> : null}
        </p>
      ) : null}
      {reviewing && (!result || !result.ok) ? (
        <div className="pinrow">
          <button className="pinbtn" disabled={busy} onClick={apply}>
            Make {everyday.model}{effort ? ` at ${effort}` : ""} the start
          </button>
          {projects.length > 0 ? (
            <select className="pinscope" value={scope} disabled={busy}
                    onChange={(e) => setScope(e.target.value)}
                    aria-label="where to apply">
              <option value="machine">on this machine</option>
              {projects.map((leaf) => (
                <option key={leaf} value={leaf}>for {leaf} only</option>
              ))}
            </select>
          ) : null}
          <span className="pinnote">writes {target}, keeps a backup</span>
        </div>
      ) : null}
    </div>
  );
}

export function matchedBenchmarks(catalog, guidance) {
  if (!guidance || guidance.tool !== catalog.tool || guidance.review_needed
      || !/^\d{4}-\d{2}-\d{2}$/.test(guidance.published_on || "") || !guidance.attribution) return [];
  const name = value => typeof value === "string" ? value.trim().toLowerCase() : "";
  const seen = new Set();
  return (guidance.roles || []).filter(row => {
    // Roles can change independently between hosted editions. Only attach
    // evidence to the exact published model and explicitly recorded effort.
    const exactModel = catalog.models.some(model => name(model.model) === name(row.model));
    const effort = name(row.effort);
    const supportedEffort = effort && (!catalog.efforts.length
      || catalog.efforts.some(item => name(item.level) === effort));
    const key = `${name(row.model)}:${effort}`;
    if (!exactModel || !supportedEffort || row.index == null || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function ModelsCard({ view, refresh }) {
  const guidance = view.model_guidance || [];
  const catalog = view.model_catalog || [];
  const [selectedTool, setSelectedTool] = useState("");
  const { range, byTool } = harnessUsage(view);
  const published = guidance.find((g) => g.published_on);
  const guidanceOf = new Map(guidance.map((g) => [g.tool, g]));
  return (
    <Card className="span2 modelscard">
      <div className="local-tabs" role="group" aria-label="Tool defaults">{catalog.map(c => <button key={c.tool}
        aria-pressed={(selectedTool || catalog[0]?.tool) === c.tool} onClick={() => setSelectedTool(c.tool)}>{c.tool_label}</button>)}</div>
      {catalog.filter(c => c.tool === (selectedTool || catalog[0]?.tool)).map((c) => {
        const usage = byTool.get(c.tool);
        const g = guidanceOf.get(c.tool);
        const benchmarks = matchedBenchmarks(c, g);
        const starting = c.models.find(row => row.role === "everyday");
        const effort = c.efforts.find(row => row.recommended);
        return (
          <div className="harness" key={c.tool}>
            <div className="defaults-comparison"><div><div className="eyebrow">Your current settings</div><p className="mdefaultline">{catalogLine(c)}</p></div>
            {starting ? <div className="model-start">
              <div className="eyebrow">Recommended start</div>
              <h3>{starting.model}{effort ? ` · ${effort.level} effort` : ""}</h3>
              <p>{starting.when}</p><p className="muted">{starting.price_note}</p>
            </div> : null}
            </div>
            <PinControl c={c} view={view} refresh={refresh} />
            <details className="stat-details model-comparison"><summary>Compare models and reasoning · {c.tool_label}</summary>
            <div className="harnesshead">
              <span className="harnessname">{c.tool_label}</span>
              {usage ? (
                <span className="harnessuse">
                  Largest estimated cost: {topModel(usage)}
                  {g?.current_model && !g.current_model_matched
                    ? " · not in the benchmark set" : ""}
                </span>
              ) : (
                <span className="harnessuse">No priced usage for this period</span>
              )}
            </div>

            <div className="modelrow">
              {c.models.map((row) => {
                return (
                  <div className={row.pinned ? "modeltile pinned" : "modeltile"}
                       key={c.tool + row.role}>
                    <span className="mrole">
                      {MODEL_ROLE_COPY[row.role].label}
                      {row.pinned ? (
                        <span className="mtag pin">Your default</span>
                      ) : null}
                    </span>
                    <span className="mname">{row.model}</span>
                    <p className="malso">
                      {row.price_note}
                    </p>
                    <p className="muse">{row.when}</p>
                  </div>
                );
              })}
            </div>
            {c.efforts.length > 0 ? (
              <>
                <p className="effintro">
                  Reasoning effort is a separate setting. Higher effort can
                  increase reasoning-token usage; check the result for your task.
                </p>
                <div className="modelrow effrow">
                  {c.efforts.map((row) => (
                    <div className={`modeltile eff ${row.tone}`
                           + (row.pinned ? " pinned" : "")}
                         key={c.tool + row.level}>
                      <span className="mrole">
                        {row.level}
                        {row.pinned ? (
                          <span className="mtag pin">Your default</span>
                        ) : null}
                        {row.recommended ? (
                          <span className="mtag rec">Suggested minimum</span>
                        ) : null}
                      </span>
                      <p className="muse">{row.when}</p>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="mdefault">
                This harness pins a model, not a reasoning dial — depth is
                chosen per session.
              </p>
            )}
            </details>
            {benchmarks.length ? <details className="stat-details benchmark-evidence">
              <summary>Published benchmark evidence</summary>
              <p className="muted">{g.attribution} · {g.published_on} · {c.tool_label}.
                These results describe the named models at the stated effort, not your work.
                Catalog edition: {c.version}.</p>
              <div className="table-scroll"><table><thead><tr><th>Model</th><th>Effort tested</th>
                <th>Benchmark index</th><th>Benchmark tokens</th></tr></thead><tbody>
                {benchmarks.map(b => <tr key={`${b.model}:${b.effort}`}><td>{b.model}</td>
                  <td>{b.effort}</td><td>{b.index}</td><td>{b.tokens || "not recorded"}</td></tr>)}
              </tbody></table></div>
              <p className="muted">Index and token figures use the publisher's benchmark setup.
                A higher index is a benchmark result, not a proficiency score.
                Results for other models, efforts, or older guidance are omitted.</p>
            </details> : null}

            {c.practices && c.practices.length > 0 ? (
              <details className="mfold">
                <summary>
                  What the benchmarks add — {c.practices.length} facts
                </summary>
                <div className="mfoldbody">
                  {c.practices.map((p) => (
                    <p key={p.title}>
                      <b>{p.title}.</b> {p.body}
                    </p>
                  ))}
                  {c.practices_source ? (
                    <p className="msourceline">Source: {c.practices_source}</p>
                  ) : null}
                </div>
              </details>
            ) : null}
            {c.switch && c.switch.length > 0 ? (
              <details className="mfold">
                <summary>How to switch — under a minute</summary>
                <ol className="mfoldbody msteps">
                  {c.switch.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </details>
            ) : null}
          </div>
        );
      })}
      {(() => {
        const closing = catalog.find((c) => c.closing);
        return closing ? (
          <p className="minscription">{closing.closing}</p>
        ) : null;
      })()}
      {published ? (
        <div className="modelsource">
          <span>{published.attribution} · benchmark of {published.published_on}
            {published.review_needed ? " · aging, re-verify" : ""}</span>
        </div>
      ) : null}
      <details className="stat-details"><summary>Guidance sources and period</summary>      <Head title="Models and reasoning"
            hint={"What each model is for, the reasoning dial, and what "
              + "your sessions start on against the recommended floor - "
              + "read from each tool's own settings. Benchmark figures "
              + "appear only when published."}
            meta={range
              ? <span>{range.from_day} → {range.to_day} · est.</span>
              : null} />
      <ContentStatus status={view.feed_status?.["model-catalog"]} label="Guidance" />
      <ContentStatus status={view.feed_status?.models} label="Benchmarks" />

      </details>
    </Card>
  );
}

function topModel(usage) {
  let best = null;
  for (const [model, cost] of usage.models) {
    if (best === null || cost > best.cost) best = { model, cost };
  }
  return best ? best.model : "—";
}

// What the harnesses ARE, one tab each: versions from the tools' own logs
// against the latest published release (tools), connectors and plugins
// from their configs (connectors), skills from disk (skills). All read
// the served "tools" payload; names only — commands, bodies and paths
// never travel.
export function ToolVersions({ view }) {
  const rows = (view.tools && view.tools.versions) || [];
  if (rows.length === 0) return null;
  return (
    <Card className="span2 toolversions">
      <Head title="Tool versions"
            hint={"Observed is the newest version recorded in your recent session logs; "
              + "the tool may have changed since. Latest comes from its public releases, "
              + "checked daily, on the channel you run - stable or "
              + "prerelease."} />
      {rows.map((v) => {
        const newerLabel = v.channel === "prerelease"
          ? `newer on your channel — ${v.latest}`
          : `newer available — ${v.latest}`;
        return (
          <div className="doclink verline" key={v.tool}>
            <span className="harnessname">{v.tool_label}</span>
            <span className="verinst">
              {v.installed
                ? `in your logs ${v.installed}`
                : "no version stamp in the logs yet"}
            </span>
            {v.latest ? (
              v.newer ? (
                httpsUrl(v.notes_url) ? (
                  <a className="vernew" href={httpsUrl(v.notes_url)}
                     target="_blank" rel="noreferrer">
                    {newerLabel} ↗
                  </a>
                ) : (
                  <span className="vernew">{newerLabel}</span>
                )
              ) : (
                <span className="docwhy">
                  latest {v.channel === "prerelease" ? "on your channel "
                    : "published "}{v.latest}
                </span>
              )
            ) : (
              <span className="docwhy">latest release not checked yet</span>
            )}
          </div>
        );
      })}
    </Card>
  );
}

// The functionality each client's sessions actually reached for, from the
// features scan: a closed taxonomy where every row carries its own
// one-line documentation, so the list teaches what each capability IS
// while it says how much it was used.
export function ToolFeatures({ view }) {
  const [tool, setTool] = useState("");
  const rows = ((view.tools && view.tools.features) || [])
    .filter((h) => h.features.length > 0);
  if (rows.length === 0) return null;
  return (
    <Card className="span2 toolfeatures">
      <Head title="Features in use"
            hint={"Recorded tool invocations and feature events from the last month, grouped by "
              + "harness's own features. A count of how often, not how "
              + "well."}
            meta={`last ${rows[0].window_days} days`} />
      <div className="local-tabs" role="group" aria-label="Feature tool">{rows.map(h => <button key={h.tool} aria-pressed={h.tool === (tool || rows[0]?.tool)} onClick={() => setTool(h.tool)}>{h.tool_label}</button>)}</div>
      {rows.filter(h => h.tool === (tool || rows[0]?.tool)).map((h) => (
        <div className="harness" key={h.tool}>
          <div className="harnesshead">
            <span className="harnessname">{h.tool_label}</span>
            <span className="harnessuse">
              {count(h.sessions)} session logs scanned
            </span>
          </div>
          {h.features.map((f) => (
            <details className="featrow" key={f.id} open
              style={{"--share": `${Math.round(1000 * f.count / Math.max(1, ...h.features.map(x => x.count))) / 10}%`}}>
              <summary className="feathead">
                <span className="featname">{f.label}</span>
                <span className="featbar" aria-hidden="true"><i /></span>
                <span className="featcount">{count(f.count)}{f.count === 1 ? " call" : " calls"}</span>
              </summary>
              <p className="featdoc">{f.doc}</p>
            </details>
          ))}
        </div>
      ))}
    </Card>
  );
}

const readableName = name => name.split("@")[0].replaceAll(/[-_]/g, " ");

export function ToolConnectors({ view }) {
  const [kind, setKind] = useState("");
  const rows = (view.tools?.connectors || []).flatMap(h => [
    ...h.mcp.map(m => ({...m, kind: "MCP server", tool: h.tool_label})),
    ...h.plugins.map(m => ({...m, kind: "Plugin", tool: h.tool_label, enabled: true})),
  ]);
  const filtered = rows.filter(r => !kind || r.kind === kind);
  return <Card className="span2 toolconnectors">
    <Head title={rows.length ? "Configured integrations" : "No configured integrations found"} />
    <p className="inventory-coverage">MCP servers and plugins declared in readable tool settings. Enabled means configured, not a tested connection. Account connections and undiscoverable configurations are outside this inventory.</p>
    <label className="filter-label">Type<select value={kind} onChange={e => setKind(e.target.value)}><option value="">All integrations</option><option>MCP server</option><option>Plugin</option></select></label>
    {filtered.map((r, i) => <details className="inventory-row" key={`${r.tool}:${r.kind}:${r.name}:${r.scope}:${i}`}>
      <summary><strong>{readableName(r.name)}</strong><span>{r.tool} · {r.kind}</span><span>{r.enabled ? "Enabled in settings" : "Disabled"}</span>
        <span>{typeof r.calls === "number" ? `${count(r.calls)} recent calls` : "Usage not recorded"}</span></summary>
      <p><code>{r.name}</code></p><p>Scope: {r.scope || "Not recorded"}{r.version ? ` · Version ${r.version}` : ""}</p>
    </details>)}
    {!rows.length ? <a href="#docs">Browse the setup guides</a> : null}
  </Card>;
}

// What the work WAS (PRODUCTIVITY_PROFILE P2): each session classified by
// documented rules into one bucket. The rule travels with every row, so
// the mix never has to be taken on faith.
export function WorkMixCard({ view }) {
  const rows = (Array.isArray(view.work_mix) ? view.work_mix : []).filter((h) => h.mix.length > 0);
  if (rows.length === 0) return null;
  return (
    <Card className="span2 workmix">
      <Head title="What the work was"
            hint={"Each session lands in one bucket by the rule on its "
              + "tile. A count of sessions, never a judgment of them."}
            meta={`last ${rows[0].window_days} days`} />
      {rows.map((h) => (
        <div className="harness" key={h.tool}>
          <div className="harnesshead">
            <span className="harnessname">{h.tool_label}</span>
            <span className="harnessuse">
              {count(h.sessions)} sessions this window
            </span>
          </div>
          <div className="modelrow">
            {h.mix.map((m) => (
              <div className="modeltile" key={h.tool + m.id}>
                <span className="mrole">{m.label}</span>
                <span className="mname">{m.share_pct}%</span>
                <p className="malso">{count(m.sessions)} sessions</p>
                <p className="muse">{m.doc}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </Card>
  );
}

export function ToolSkills({ view }) {
  const [query, setQuery] = useState("");
  const [tool, setTool] = useState("");
  const groups = view.tools?.skills || [];
  const rows = groups.flatMap(h => h.skills.map((s, index) => ({...s, tool: h.tool_label, key: `${h.tool}:${s.name}:${s.scope}:${index}`})));
  const filtered = rows.filter(s => (!tool || s.tool === tool) && `${s.name} ${s.about || ""}`.toLowerCase().includes(query.toLowerCase()));
  return <Card className="span2 toolskills">
    <Head title="Installed skills" />
    <div className="inventory-filters"><label>Find a skill<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Name or purpose" /></label>
      <label>Tool<select value={tool} onChange={e => setTool(e.target.value)}><option value="">All tools</option>{groups.map(h => <option key={h.tool}>{h.tool_label}</option>)}</select></label></div>
    <p className="muted" aria-live="polite">{filtered.length} of {rows.length} installations</p>
    {filtered.map(s => <article className="skill-inventory-row" key={s.key}>
      <div className="feathead"><h3>{readableName(s.name)}</h3><span>{s.tool}</span></div>
      <p>{s.about || "No description recorded."}</p>
    </article>)}
    {!filtered.length ? <p>No installed skills match. Clear the search or choose another tool.</p> : null}
  </Card>;
}

// Documentation: the official shelves for the harnesses this page reads,
// organized into the sections the served docs.json artifact defines —
// reorganizing the shelf is a publish, never a client release. Links are
// validated to https server-side; every link opens on the person's click.
export function DocsCard({ view }) {
  const docs = view.docs || { sections: [] };
  const sections = docs.sections || [];
  return (
    <Card className="span2 docscard">
      <Head title="Official guides"
            hint={"Official references for the tools this page reads. "
              + "Nothing opens unless you click it."} />
      <ContentStatus status={view.feed_status?.docs} label="Documentation" />
      {!sections.length ? <p>No official learning links are available yet.</p> : null}
      {sections.map((section, index) => (
        <div className="docsec" id={`guide-${index}`} key={section.title}>
          <h3 className="docsechead">{section.title}</h3>
          {section.links.map((link) => (
            <div className="doclink" key={link.url}>
              {httpsUrl(link.url) ? (
                <a href={httpsUrl(link.url)} target="_blank" rel="noreferrer">
                  {link.title}
                </a>
              ) : (
                <span className="docwhy">{link.title}</span>
              )}
              <span className="docwhy">{link.why}</span>
            </div>
          ))}
        </div>
      ))}
    </Card>
  );
}

// The playbook: each detected finding arrives with its handle — a paste-ready
// brief carrying the one measured number that fired (analysis/playbook.py).
// The agent can read the code; it cannot read its own usage economics. These
// prompts hand it exactly the part it is blind to. "Open in Codex" prefills
// the composer through the codex:// scheme and sends nothing; Copy is the
// universal lane and the whole Claude Code lane, since no public URL scheme
// prefills a Claude Code session today. Every move folds to one line — title,
// the number that fired, the copy button — because the ten-line prompt body
// is written to be pasted, not read on a dashboard; expanding is one click.
export function Playbook({ view }) {
  const moves = view.playbook || [];
  const [copiedId, setCopiedId] = useState(null);
  const copyTimer = useRef(null);
  useEffect(() => () => {
    if (copyTimer.current !== null) clearTimeout(copyTimer.current);
  }, []);
  if (!moves.length) return null;
  const copy = async (move) => {
    try {
      await navigator.clipboard.writeText(move.prompt);
      if (copyTimer.current !== null) clearTimeout(copyTimer.current);
      setCopiedId(move.id);
      copyTimer.current = setTimeout(() => {
        copyTimer.current = null;
        setCopiedId((current) => (current === move.id ? null : current));
      }, 2400);
    } catch {
      // The prompt is already selectable text right above the button.
    }
  };
  return (
    <Card className="span2 playbook">
      <Head title="Prompts to try"
            hint={"A ready-to-paste brief for each pattern in your own "
              + "numbers. Open in Codex only prefills; nothing sends until "
              + "you do. Prompts carry counts, never file paths."} />
      {moves.map((move, index) => (
        <details className="pbmove" key={move.id}>
          <summary>
            <span className="pbtitle">{move.title}</span>
            {moves.slice(0, index).some((m) => m.why === move.why)
              ? null : <span className="pbwhy">{move.why}</span>}
            {/* Copy lives on the folded line: the prompt is made to be
                pasted, so the everyday gesture never requires the expand. */}
            <button className="skcopy pbsumcopy"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      copy(move);
                    }}>
              {copiedId === move.id ? "Copied — paste it" : "Copy prompt"}
            </button>
          </summary>
          <pre className="pbprompt">{move.prompt}</pre>
          <div className="pbactions">
            <span className="pbchip">{move.finding}</span>
            {/* target=_blank on purpose: the native shell routes new-window
                requests to the OS (which owns the codex: scheme) instead of
                navigating the dashboard away. */}
            {codexUrl(move.codex_url) ? (
              <a className="skcopy pbopen" href={codexUrl(move.codex_url)}
                 target="_blank" rel="noreferrer">
                Open in Codex
              </a>
            ) : null}
          </div>
        </details>
      ))}
    </Card>
  );
}



// Skills for your practice: the org's registry, matched locally to the work
// this machine actually did (server served the catalog; the match never left
// here). One lean card per skill — title, one line, one action; the WHY
// renders once at shelf level (per-card chips were near-identical noise).
// A successful copy is reported to the local ledger so delivered skills
// retire from the shelf for a while.
const SKILLS_VISIBLE = 1;

export function SkillShelf({ view, onCopied = recordSkillCopy }) {
  const skills = view.skills || [];
  const [feedback, setFeedback] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const feedbackTimer = useRef(null);
  useEffect(() => () => {
    if (feedbackTimer.current !== null) {
      clearTimeout(feedbackTimer.current);
    }
  }, []);
  const outcome = view.skill_outcome;
  if (skills.length === 0 && !outcome) return null;
  const sourceMeta = view.skills_source === "teamlandi_public"
    ? "TeamLandi public registry"
    : "local catalog";
  const visible = showAll ? skills : skills.slice(0, SKILLS_VISIBLE);
  const trustedInstall = (skill) => (
    view.skills_source === "teamlandi_public"
    && typeof skill.source_url === "string"
    && skill.source_url.trim().length > 0
    && typeof skill.install_command === "string"
    && skill.install_command.trim().length > 0
  );
  const copy = async (skill, which) => {
    const installable = trustedInstall(skill);
    const text = !installable ? skill.prompt
      : which === "claude" ? skill.claude_install_command
      : skill.install_command;
    const clearFeedbackTimer = () => {
      if (feedbackTimer.current !== null) {
        clearTimeout(feedbackTimer.current);
        feedbackTimer.current = null;
      }
    };
    try {
      await navigator.clipboard.writeText(text);
      clearFeedbackTimer();
      setFeedback({
        id: skill.id,
        kind: !installable ? "prompt"
          : which === "claude" ? "installed-claude" : "installed",
      });
      // Delivered: the local ledger retires this skill from future shelves
      // for a few weeks. Fire-and-forget; a failure never blocks the copy.
      try { Promise.resolve(onCopied(skill.id)).catch(() => {}); } catch { /* noop */ }
      feedbackTimer.current = setTimeout(() => {
        feedbackTimer.current = null;
        setFeedback((current) => (
          current?.id === skill.id ? null : current
        ));
      }, 2400);
    } catch {
      clearFeedbackTimer();
      setFeedback({ id: skill.id, kind: "error", text });
    }
  };
  return (
    <Card delay={260} className="span2">
      <Head title="Suggested skills"
            hint={"Prompts matched to your recent work. Copy one to steer a "
              + "session; a copied skill rests for a few weeks so the shelf "
              + "stays fresh."}
            meta={sourceMeta} />
      {outcome ? (
        <p className="skoutcome">
          {outcome.line}
          {outcome.note ? <Info text={outcome.note} /> : null}
        </p>
      ) : null}
      <div className="skillgrid">
        {visible.map((skill) => (
          <div className="skill" key={skill.id}>
            <div className="skhead">
              <span className="sktitle">{skill.title}</span>
              {skill.installed ? (
                <span className="skinstalled">installed</span>
              ) : null}
              {skill.role ? <span className="skrole">{skill.role}</span> : null}
            </div>
            <p className="sksum">{skill.summary}</p>
            <details className="skpreview">
              <summary>Preview instructions</summary>
              <p>{skill.prompt}</p>
            </details>
            <div className="skactions">
              {trustedInstall(skill) && httpsUrl(skill.source_url) ? (
                <a className="sksource" href={httpsUrl(skill.source_url)}
                   target="_blank" rel="noreferrer">View source</a>
              ) : null}
              {trustedInstall(skill) ? (
                <>
                  <button className="skcopy" onClick={() => copy(skill, "codex")}>
                    Copy for Codex
                  </button>
                  {skill.claude_install_command ? (
                    <button className="skcopy" onClick={() => copy(skill, "claude")}>
                      Copy for Claude Code
                    </button>
                  ) : null}
                </>
              ) : (
                <button className="skcopy" onClick={() => copy(skill)}>
                  Copy prompt
                </button>
              )}
            </div>
            {feedback?.id === skill.id && feedback.kind === "installed" ? (
              <p className="skfeedback">
                <strong>Codex install copied</strong>
                <span>Paste it into a Codex task; mention the skill as
                  ${skill.id} afterwards.</span>
              </p>
            ) : null}
            {feedback?.id === skill.id && feedback.kind === "installed-claude" ? (
              <p className="skfeedback">
                <strong>Claude Code install copied</strong>
                <span>Paste it into a Claude Code session or shell; the
                  skill is available from the next session.</span>
              </p>
            ) : null}
            {feedback?.id === skill.id && feedback.kind === "prompt" ? (
              <p className="skfeedback"><strong>Prompt copied</strong></p>
            ) : null}
            {feedback?.id === skill.id && feedback.kind === "error" ? (
              <div className="skfailure">
                <span>Couldn’t copy automatically. Select and copy this text:</span>
                <code className="skcommand">{feedback.text}</code>
              </div>
            ) : null}
          </div>
        ))}
      </div>
      {skills.length > SKILLS_VISIBLE ? (
        <button className="skmore" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Show fewer" : `Show ${skills.length - SKILLS_VISIBLE} more`}
        </button>
      ) : null}
    </Card>
  );
}



// How you worked with it: the collaboration-shape reading. Anthropic's own RCT
// found the sign of AI's effect on comprehension depends on HOW the tool is
// used, not how much — so this shows the shape of the exchange and refuses to
// score it. Each facet names both ways of reading its number; the hint carries
// what it measures. Null when too little is readable.
// A newer release, offered and never installed. The client verified an
// Ed25519 signature over the version, the asset URL, its SHA-256 and its size
// before this could render, so a repointed update URL cannot put a link here.
// The download is a plain link the person clicks; nothing is fetched, written
// or executed by the app.
// Something wrong with the INSTALL, not the practice. Deliberately plain: both
// cases are ordinary situations with one clear next step, and dressing them as
// warnings would train the person to dismiss the card. It leads the page
// because a leftover second agent makes every other number here ambiguous.
export function InstallNotices({ view }) {
  const notices = view.install_notices || [];
  if (notices.length === 0) return null;
  return (
    <Card className="span2 installnotice noticebar" delay={20}>
      {notices.map((n) => (
        <details className="installrow" key={n.id}>
          <summary><span className="installlabel">{n.label}</span><span className="installline">{n.line}</span><span className="installmore">Why</span></summary>
          <p className="muted installwhy">{n.why}</p>
          <p className="installdo">{n.action}</p>
        </details>
      ))}
    </Card>
  );
}

export function UpdateBanner({ view }) {
  const u = view.update;
  if (!u) return null;
  return (
    <Card className="span2 updatecard" delay={40}>
      <Head title={u.label} meta={<span>signature verified</span>} />
      <p className="updateline">{u.line}</p>
      <div className="updateacts">
        <a className="act primary" href={httpsUrl(u.url) || undefined}
           target="_blank" rel="noreferrer">{u.action}</a>
        <a className="act quiet" href={httpsUrl(u.notes_url) || undefined}
           target="_blank" rel="noreferrer">What changed</a>
      </div>
      <p className="muted updatenote">{u.note}</p>
      {/* Published alongside the file so a download can be checked by hand. */}
      <p className="updatesum"><span className="mlabel">sha256</span>
        <code>{u.sha256}</code></p>
    </Card>
  );
}

export function Reliance({ view }) {
  const r = view.reliance;
  if (!r) return null;
  const facets = r.facets.filter(f => f.id !== "engagement");
  return <details className="card stat-details reliance">
    <summary>Interaction details · last {r.window_days} days</summary>
    <p>{r.headline}</p>
    {facets.map(f => <div className="relfacet" key={f.id}>
      <p><strong>{f.label}</strong></p><p>{f.line}</p>
      {f.delta != null ? <p className="muted">{f.delta === 0 ? "Unchanged from" : `${Math.abs(f.delta)} ${f.delta_unit || (f.id === "grain" ? "events per handoff" : "percentage points")} ${f.delta > 0 ? "higher" : "lower"} than`} the preceding {r.window_days} days.</p> : null}
      <p className="muted">{f.measures} {f.why}</p>
    </div>)}
  </details>;
}

// A monotone cubic through the weekly points: the curve never overshoots a
// reading, so a smooth line cannot invent a later or earlier week than the
// composer shipped. Presentation only; the values stay the shipped ones.
function tailCurve(points) {
  const n = points.length;
  const f = (v) => v.toFixed(1);
  if (n < 3) return points.map(([px, py], i) => `${i ? "L" : "M"}${f(px)},${f(py)}`).join(" ");
  const dx = [], slope = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(points[i + 1][0] - points[i][0]);
    slope.push((points[i + 1][1] - points[i][1]) / dx[i]);
  }
  const tangent = [slope[0]];
  for (let i = 1; i < n - 1; i++) {
    if (slope[i - 1] * slope[i] <= 0) { tangent.push(0); continue; }
    const a = 2 * dx[i] + dx[i - 1], b = dx[i] + 2 * dx[i - 1];
    tangent.push((a + b) / (a / slope[i - 1] + b / slope[i]));
  }
  tangent.push(slope[n - 2]);
  let d = `M${f(points[0][0])},${f(points[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = points[i], [x1, y1] = points[i + 1], h = dx[i] / 3;
    d += ` C${f(x0 + h)},${f(y0 + tangent[i] * h)} ${f(x1 - h)},${f(y1 - tangent[i + 1] * h)} ${f(x1)},${f(y1)}`;
  }
  return d;
}

// Where the day ends: the session-tail reading. The chart renders from
// geometry the composer ships (axis bounds, grid marks, quiet-band edge) —
// nothing is re-derived here. Only positively classified human prompts
// affect this history; background activity does not describe the person's day.
export function SessionTail({ view }) {
  const t = view.session_tail;
  const [shown, setShown] = useState(null);
  if (!t || !t.weeks || t.weeks.length < 2) return null;
  const W = 760, H = 264, PL = 58, PR = 26, PT = 26, PB = 36;
  const base = H - PB, count = t.weeks.length;
  const span = Math.max(1, t.chart.axis_hi - t.chart.axis_lo);
  const x = (i) => PL + (i * (W - PL - PR)) / (count - 1);
  const y = (m) => PT + (base - PT) * (1 - (m - t.chart.axis_lo) / span);
  const points = t.weeks.map((w, i) => [x(i), y(w.minutes)]);
  const line = tailCurve(points);
  const area = `${line} L${x(count - 1).toFixed(1)},${base} L${x(0).toFixed(1)},${base} Z`;
  const quietY =
    t.chart.quiet_start != null && t.chart.quiet_start < t.chart.axis_hi
      ? y(Math.max(t.chart.quiet_start, t.chart.axis_lo))
      : null;
  const wkLabel = (iso) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString("en", {
      month: "short", day: "numeric",
    });
  // The edge of the quiet hours is where the last prompt of the day would
  // ideally have been sent already. Its position is the composer's; its clock
  // is the confirmed schedule's own string, never computed here.
  const quietClock = view.schedule && view.schedule.quiet_start;
  const quietLabel = quietClock ? `quiet hours begin · ${quietClock}` : "quiet hours begin";
  const late = (w) => quietY != null && w.minutes >= t.chart.quiet_start;
  const first = t.weeks[0], last = t.weeks[count - 1];
  // Every week is dated while they fit; a long history dates every other week
  // and always the last one.
  const dated = (i) => count <= 9 || i === count - 1 || (i % 2 === 0 && i < count - 2);
  const edge = (i) => (i === 0 ? PL : (x(i - 1) + x(i)) / 2);
  const tip = shown == null ? null : (() => {
    const w = t.weeks[shown], label = `wk of ${wkLabel(w.start_day)} · ${w.clock}`;
    const width = label.length * 7.4 + 22, [px, py] = points[shown];
    const left = Math.min(Math.max(px - width / 2, PL), W - PR - width);
    const above = py - 40 >= 2;
    return { label, width, left, top: above ? py - 40 : py + 14, px, py };
  })();
  return (
    <details className="card stat-details sessiontail" open>
      <summary>When your AI activity ends<Info text={t.intro} /></summary>
      <p className="muted">Weekly median of the last recorded prompt · local time · {t.active_days} active days</p>
      <div className="tail-hero" data-testid="tail-hero">
        <div className="tail-story">
          <p className="eyebrow">Latest weekly median</p>
          <div className="tail-figure">
            <span className="tail-now">{last.clock}</span>
            <p className="tail-from">from {first.clock} in the week of {wkLabel(first.start_day)}</p>
          </div>
          <p className="tailheadline">{t.headline}</p>
          {t.drift_line ? <p className="taildrift">{t.drift_line}</p> : null}
        </div>
        <svg className="tailchart" viewBox={`0 0 ${W} ${H}`} role="group"
             aria-label="Weekly median time of the last prompt"
             onMouseLeave={() => setShown(null)}>
          <defs>
            <linearGradient id="tail-area-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" style={{ stopColor: "var(--chart-active)", stopOpacity: 0.3 }} />
              <stop offset="100%" style={{ stopColor: "var(--chart-active)", stopOpacity: 0.02 }} />
            </linearGradient>
          </defs>
          {quietY != null ? (
            <g>
              <rect className="tail-quiet" x={PL} y={PT - 10} rx="8"
                    width={W - PL - PR} height={quietY - PT + 10} />
              <line className="tail-quiet-line" x1={PL} y1={quietY} x2={W - PR} y2={quietY} />
              <text className="tailband" x={PL + 10} y={quietY - 8} textAnchor="start">{quietLabel}</text>
            </g>
          ) : null}
          {t.chart.grid.map(([m, label]) => (
            <g key={m}>
              <line className="tail-gridline" x1={PL} y1={y(m)} x2={W - PR} y2={y(m)} />
              <text x={PL - 10} y={y(m) + 4} textAnchor="end"
                    className="tailaxis">{label}</text>
            </g>
          ))}
          <line className="tail-baseline" x1={PL} y1={base} x2={W - PR} y2={base} />
          <path className="tail-area" d={area} fill="url(#tail-area-fill)" />
          <path className="tail-line" d={line} />
          {tip ? <line className="tail-guide" x1={tip.px} y1={PT - 10} x2={tip.px} y2={base} /> : null}
          {t.weeks.map((w, i) => (
            <circle key={w.start_day} cx={points[i][0]} cy={points[i][1]}
                    r={i === count - 1 || i === shown ? 5.5 : 4}
                    className={`tail-dot${i === count - 1 ? " latest" : ""}${i === shown ? " on" : ""}${late(w) ? " late" : ""}`}>
              <title>{`wk of ${wkLabel(w.start_day)} · ${w.clock}`}</title>
            </circle>
          ))}
          <circle className={`tail-halo${late(last) ? " late" : ""}`} cx={points[count - 1][0]} cy={points[count - 1][1]} r="12" />
          {t.weeks.map((w, i) => dated(i) ? (
            <text key={w.start_day} className="tailtick" x={x(i)} y={H - 10}
                  textAnchor={i === 0 ? "start" : i === count - 1 ? "end" : "middle"}>
              {wkLabel(w.start_day)}</text>
          ) : null)}
          {t.weeks.map((w, i) => (
            <rect key={w.start_day} className="tail-hit" tabIndex={0}
                  x={edge(i)} y={PT - 10}
                  width={(i === count - 1 ? W - PR : edge(i + 1)) - edge(i)} height={base - PT + 10}
                  aria-label={`Week of ${wkLabel(w.start_day)}: median last prompt ${w.clock}`}
                  onMouseEnter={() => setShown(i)} onFocus={() => setShown(i)}
                  onBlur={() => setShown(null)} />
          ))}
          {tip ? (
            <g role="tooltip" className="tail-tip" pointerEvents="none">
              <rect x={tip.left} y={tip.top} width={tip.width} height="26" rx="8" />
              <text x={tip.left + tip.width / 2} y={tip.top + 17} textAnchor="middle">{tip.label}</text>
            </g>
          ) : null}
        </svg>
      </div>
      <details className="stat-details"><summary>How days and midnight are handled</summary><p className="muted tailnote">{t.note}</p></details>
    </details>
  );
}

// The Calibration Mirror: the estimate comes before the clock, or it is not
// an estimate. The probe deliberately carries no duration — the server holds
// the answer until the guess is in. Skipping costs nothing and never returns
// for that session; there is no streak and no score here, only the two numbers
// side by side.
export function Calibration({ view, refresh, span }) {
  const c = view.calibration;
  const [busy, setBusy] = useState(false);
  const [skipped, setSkipped] = useState(false);
  if (!c) return null;
  const answer = async (bucketId) => {
    setBusy(true);
    const ok = await recordEstimate(bucketId);
    setBusy(false);
    if (ok) refresh && refresh();
  };
  const showProbe = c.probe && !skipped;
  return (
    <details className="card stat-details calibration"><summary>Reflect on a recorded session</summary>
      <Head title={c.eyebrow}
            hint={"Your guess against the clock. The length is held back until "
              + "you answer."} />
      {showProbe ? (
        <div className="calprobe">
          <div className="calq">{c.probe.question}</div>
          <p className="muted calintro">{c.probe.intro}</p>
          <div className="calopts">
            {c.probe.options.map((opt) => (
              <button key={opt.id} disabled={busy}
                      onClick={() => answer(opt.id)}>{opt.label}</button>
            ))}
          </div>
          <button className="calskip" onClick={() => setSkipped(true)}>
            {c.skip}
          </button>
        </div>
      ) : null}
      {c.last ? (
        <div className={showProbe ? "calresult calmuted" : "calresult"}>
          <p className="calline">{c.last.line}</p>
        </div>
      ) : null}
      {c.summary ? <p className="muted calsummary">{c.summary}</p> : null}
    </details>
  );
}

// The strain reading's two halves. The probe
// asks how drained the day felt BEFORE the verification components show —
// the calibration mirror's no-peek rule; while today's answer is pending
// the server withholds the components from the wire entirely. What shows
// afterwards is components against the person's own prior window: never a
// score, never a state. The evidence it leans on (Fan et al., CHI 2026) is
// printed under it, because an association without its source is a claim.
export function VerificationCard({ view }) {
  const v = view.verification;
  if (!v?.available) return null;
  const components = v.components.filter(c => ["failures", "retries"].includes(c.id));
  if (!components.length) return null;
  return <details className="card stat-details verification">
    <summary>Recorded failures and retries · last {v.window_days} days</summary>
    <p>Counts from supported tool logs. More recorded activity or different tasks can change these totals;
      they do not measure review quality or a failure rate.</p>
    {components.map(c => <div className="featrow" key={c.id}>
      <p><strong>{c.label}</strong>: {c.value == null ? "not observable" : `${count(c.value)} ${c.unit}`}</p>
      {c.delta != null ? <p className="muted">{c.delta === 0 ? "Unchanged from" : `${count(Math.abs(c.delta))} ${c.unit} ${c.delta > 0 ? "more" : "fewer"} than`} the preceding {v.window_days} days.</p> : null}
      <p className="muted">{c.doc}</p>
    </div>)}
  </details>;
}

export function DailyReflection({ view, refresh }) {
  const d = view.drain;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  if (!d?.pending) return null;
  const answer = async felt => {
    setBusy(true); setError(false);
    try {
      if (!await recordDrain(felt)) throw new Error("save");
      await refresh?.();
    } catch { setError(true); }
    finally { setBusy(false); }
  };
  return <details className="card stat-details drainprobe">
    <summary>Your own reflection · optional</summary>
    <p>How drained do you feel after today's AI work?</p>
    <p className="muted">Your answer stays here. It is separate from the recorded activity.</p>
    <div className="calopts">{d.options.map(opt => <button key={opt.id} disabled={busy}
      onClick={() => answer(opt.id)}>{opt.label}</button>)}</div>
    <button disabled={busy} onClick={() => answer("skip")}>{d.skip_label}</button>
    {error ? <p role="alert">That reflection did not save. Try again.</p> : null}
  </details>;
}

// The close-the-day flow body (A2): today's three numbers, the same 1-5 checkin
// as the closing act, and one button. Today only — no history, no chain, no
// count of days closed. Copy mirrors the Python DAYCLOSE_COPY (lexicon-scanned
// there); calm register, no exclamation. Rendered inside the action bar's
// expansion, so it carries no card chrome of its own.
//
// The pieces probe (W4 validation): asked at the moment of closing, BEFORE
// any measured unit count exists anywhere in the UI — the no-peek rule the
// calibration mirror established. One tap or skip; the answer rides the
// same day-close POST and the measured side is computed server-side only.
const PIECE_CHOICES = [1, 2, 3, 4, 5, 6, 8, 10, 15];

function CloseTheDayBody({ view, onClosed }) {
  const [pieces, setPieces] = useState(undefined); // undefined = not asked yet
  const close = async (felt) => {
    if (await closeDay(view.local_day || view.day, felt)) onClosed && onClosed();
  };
  const blocks = (view.blocks && view.blocks["block-completed"]) || 0;
  if (pieces === "asking") {
    return (
      <div className="dcbody">
        <p className="dcpieces">
          Before the numbers: how many distinct pieces of work did today hold?
        </p>
        <div className="dcpiecerow">
          {PIECE_CHOICES.map((n) => (
            <button key={n} className="dcpiece" onClick={() => close(n)}>
              {n}{n === 15 ? "+" : ""}
            </button>
          ))}
        </div>
        <button className="dcskip" onClick={() => close(undefined)}>
          Skip this one
        </button>
      </div>
    );
  }
  return (
    <div className="dcbody">
      <div className="dcnums">
        <div className="dcnum">
          <span className="dcv">{usd(view.totals.cost_micro_usd)}</span>
          <span className="dcl">{moneyLabel(view)} · UTC accounting day {view.accounting_day_utc}<UsageBasis view={view} /></span>
        </div>
        <div className="dcnum">
          <span className="dcv">{count(blocks)}</span>
          <span className="dcl">completed timer blocks</span>
        </div>
        <div className="dcnum">
          <span className="dcv">{count(view.totals.assistant_turns)}</span>
          <span className="dcl">recorded assistant turns</span>
        </div>
      </div>
      <RateRow />
      <button className="dcbtn" onClick={() => setPieces("asking")}>
        Close the day
      </button>
    </div>
  );
}

// Theme: the saved choice wins; otherwise follow the OS. Applied to the <html>
// element as data-theme so the token overrides in styles.css take effect, and
// persisted so the choice survives reloads. When the user has NOT chosen, we
// keep tracking the system preference live.
const THEME_KEY = "pg-theme";

// Storage access is guarded: a browser that declines storage must still get
// a themed, rendered page.
const savedTheme = () => { try { return localStorage.getItem(THEME_KEY); } catch { return null; } };

function useTheme() {
  const [theme, setTheme] = useState(() => {
    const saved = savedTheme();
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches
      ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  useEffect(() => {
    // Follow the OS only while the user hasn't pinned a choice.
    if (savedTheme()) return undefined;
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return undefined;
    const onChange = (e) => setTheme(e.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const toggle = () => {
    setTheme((current) => {
      const next = current === "dark" ? "light" : "dark";
      try { localStorage.setItem(THEME_KEY, next); } catch { /* the choice still applies for this session */ }
      return next;
    });
  };
  return [theme, toggle];
}

function ThemeToggle({ theme, toggle }) {
  const dark = theme === "dark";
  return (
    <button className="themetoggle" onClick={toggle}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            title={dark ? "Light mode" : "Dark mode"}>
      {dark ? (
        // sun
        <svg width="16" height="16" viewBox="0 0 20 20" fill="none"
             stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
          <circle cx="10" cy="10" r="3.4" />
          <path d="M10 1.5v2M10 16.5v2M1.5 10h2M16.5 10h2M4 4l1.4 1.4M14.6 14.6L16 16M16 4l-1.4 1.4M5.4 14.6L4 16" />
        </svg>
      ) : (
        // moon
        <svg width="16" height="16" viewBox="0 0 20 20" fill="none"
             stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
             strokeLinejoin="round">
          <path d="M16.5 11.2A6.6 6.6 0 1 1 8.8 3.5a5.2 5.2 0 0 0 7.7 7.7z" />
        </svg>
      )}
    </button>
  );
}

// Dashboard is the same layout rendered by App and the interaction tests.
// Every category is directly available in the menu. Stable hashes and
// content fingerprints preserve saved links and per-page update indicators.
const OBSERVATION_SURFACE = { "repeated-no-pause": "mindfulness", "quiet-hours-activity": "mindfulness" };
const REFLECTION_SURFACE = {};

// A category lights up when something NEW arrived for it since that surface
// was last open. "New" means content identity — a news item, an advisor
// take, an observation, a pending probe — never a number moving: spend
// changes every refresh, and a dot that is always on says nothing. The
// fingerprint is the sorted id-set of what the surface would show; the one
// stored at last look is the baseline (kept locally, like the theme).
function surfaceFingerprint(view, surface) {
  const ids = [];
  if (surface === "community") ids.push(...(view.community || []).map(item => item.id));
  const o = view.observation;
  if (o && (OBSERVATION_SURFACE[o.id] || "practice") === surface) {
    ids.push(`o:${o.id}`);
  }
  for (const line of view.reflections || []) {
    if ((REFLECTION_SURFACE[line.id] || "practice") === surface) {
      ids.push(`r:${line.id}`);
    }
  }
  if (surface === "news") {
    for (const item of activeNews(view)) ids.push(`n:${item.id}`);
  }
  if (surface === "practice") {
    for (const move of view.playbook || []) ids.push(`p:${move.id}`);
  }
  if (surface === "build") {
    for (const idea of view.build_ideas || []) ids.push(`b:${idea.id}`);
    for (const pick of view.build_repos || []) ids.push(`gh:${pick.id}`);
  }
  if (surface === "models") {
    for (const g of view.model_guidance || []) {
      for (const role of g.roles) ids.push(`g:${g.tool}:${role.model}`);
    }
    // A republished catalog and a CHANGED pin are both content identity:
    // repointing your own config is exactly the kind of update the dot
    // should surface.
    for (const c of view.model_catalog || []) {
      ids.push(`mc:${c.tool}:${c.version}`);
      ids.push(`pin:${c.tool}:${c.pinned_model || "-"}:${c.pinned_effort || "-"}`);
    }
    const { byTool } = harnessUsage(view);
    for (const [tool, usage] of byTool) {
      for (const model of usage.models.keys()) ids.push(`u:${tool}:${model}`);
    }
  }
  if (surface === "docs" && view.docs) {
    // A republished shelf lights the tab; the links themselves are the
    // version's content.
    ids.push(`d:${view.docs.version}`);
  }
  if (surface === "tools" && view.tools) {
    // A new release (or a version change in the logs) is content identity,
    // and so is a FEATURE appearing or vanishing — but never its count,
    // which moves with every session.
    for (const v of view.tools.versions) {
      ids.push(`v:${v.tool}:${v.installed || "?"}:${v.latest || "?"}`);
    }
    for (const h of view.tools.features || []) {
      for (const f of h.features) ids.push(`ft:${h.tool}:${f.id}`);
    }
  }
  if (surface === "connectors" && view.tools) {
    // A connector appearing or flipping its switch lights the tab.
    for (const h of view.tools.connectors) {
      for (const m of h.mcp) {
        ids.push(`c:${h.tool}:${m.name}:${m.enabled ? 1 : 0}`);
      }
      for (const p of h.plugins) ids.push(`pl:${h.tool}:${p.name}`);
    }
  }
  if (surface === "projects" && view.tools) {
    // A NEW project folder appearing is content identity; session counts
    // and dates move constantly and never light the tab.
    for (const h of view.tools.projects || []) {
      for (const p of h.projects) ids.push(`pj:${h.tool}:${p.name}`);
    }
  }
  if (surface === "skills") {
    // Both halves of the tab: what is installed and what the registry
    // shelf currently offers.
    for (const h of (view.tools && view.tools.skills) || []) {
      for (const s of h.skills) ids.push(`sk:${h.tool}:${s.name}`);
    }

  }
  if (surface === "mindfulness") {
    const weeks = view.session_tail && view.session_tail.weeks;
    if (weeks && weeks.length) {
      ids.push(`tail:${weeks[weeks.length - 1].start_day}`);
    }
  }
  if (surface === "spend") {
    ids.push(`rc:${view.rate_card_version}`);
  }
  if (surface === "practice") {
    for (const facet of (view.reliance && view.reliance.facets) || []) {
      ids.push(`f:${facet.id}`);
    }
  }
  return ids.sort().join("|");
}

const SEEN_KEY = (id) => `pg-seen-${id}`;

// One plain line per section saying what it is (design decision 2026-09-11) -
// the reader's words, no method, no caveat; those stay behind the ⓘ.
const SECTION_INTRO = {
  spend: "Recorded token usage valued at API prices, by period, model and session.",
  models: "Choose default models and reasoning settings for your tools.",
  "api-prices": "Explore the API market and compare provider prices.",
  community: "What practitioners are trying and learning.",
  tools: "Your tool versions and the features you use.",
  skills: "The reusable instructions installed for your tools.",
  connectors: "The integrations configured for your tools.",
  practice: "Choose one improvement, try it on a task and check the result.",
  mindfulness: "Review your work rhythm and make room for a pause.",
  build: "Small projects to try with the APIs behind your tools.",
  docs: "Official training, courses and certification paths by tool.",
};

function SectionIntro({ id }) {
  const text = SECTION_INTRO[id];
  if (id === "spend") return null;
  return <header className="page-heading"><h1>{SURFACES.find(s => s.id === id)?.label}</h1>{text ? <p className="section-intro">{text}</p> : null}</header>;
}

// Field report 2026-09-17: a user's window showed "Usage could not be
// loaded" and an unavailable privacy status. The page was current; the engine
// answering it was 0.1.31, a process left running from an install weeks older,
// and nothing on screen said so. The page now names the mismatch and the way
// out. (0.2.19 replaces such an engine by itself; this is what remains visible
// when that cannot happen.)
export function EngineMismatch({ view, bundle = BUNDLE_VERSION }) {
  const engine = view?.app_version;
  if (!bundle || !engine || engine === bundle) return null;
  return (
    <div className="scan-notice engine-mismatch" role="alert" data-bundle={BUNDLE_MARK}>
      <strong>An older engine is still running.</strong>{" "}
      This window is {bundle}, but the engine answering it is {engine}, left over from an earlier
      install, so some pages cannot load. Exit PracticeGraph from its tray icon and open it again.
      If this message stays, restart the computer.
    </div>
  );
}

export function Dashboard({ view, refresh }) {
  const practiceTime = usePracticeTime();
  const [surface, setSurface] = useState(() => resolveSurface(window.location.hash));
  const learning = useLearningPaths();
  const contentRef = useRef(null);
  const previousSurface = useRef(surface);
  const ongoing = currentPractice(view, practiceTime.data, learning.data);
  useEffect(() => {
    const followHash = () => setSurface(resolveSurface(window.location.hash));
    window.addEventListener("hashchange", followHash);
    return () => window.removeEventListener("hashchange", followHash);
  }, []);
  useEffect(() => {
    if (previousSurface.current !== surface) {
      contentRef.current?.focus({preventScroll: true});
      document.querySelector(".wrap")?.scrollIntoView?.({block: "start"});
      previousSurface.current = surface;
    }
  }, [surface]);
  // While a break or rest runs, the guided panel takes over and the page
  // body dims — breaking the flow is the feature, so the data stops
  // competing for attention until the clock is done. A focus block never
  // takes over: the work continues.
  const [timer, setTimer] = useState(null); // {kind, endAt} | null
  const focusControls = useRef(null);
  // Folded on arrival; a running timer opens it, and it stays as the reader left it.
  const [focusOpen, setFocusOpen] = useState(false);
  const [focusHeight, setFocusHeight] = useState(0);
  useEffect(() => {
    if (timer?.kind !== "focus" || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setFocusHeight(focusControls.current?.getBoundingClientRect().height || 0));
    if (focusControls.current) observer.observe(focusControls.current);
    return () => observer.disconnect();
  }, [timer?.kind]);
  const onBreak = timer !== null && timer.kind !== "focus";
  // The toast's door: the shell opens the window with #break when the
  // break nudge is tapped, and the page starts the guided break at once.
  // Consumed exactly once — the hash is cleared here so a manual refresh
  // never replays the intent.
  const [breakIntent] = useState(() => {
    if (window.location.hash === "#break") {
      window.history.replaceState(null, "", "#");
      return true;
    }
    return false;
  });
  const pick = (event, id) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (id === surface) return;
    window.location.hash = id;
  };
  const fresh = Object.fromEntries(
    SURFACES.map((s) => [s.id, surfaceFingerprint(view, s.id)]),
  );
  // The open surface is seen by definition; surfaces never stored get
  // today's state as their baseline (a first open never lights everything —
  // a dot means "changed since you looked", not "unread ever"). Idempotent
  // writes, so running on every render is fine.
  // Storage is a convenience here, never a dependency: this effect runs after
  // EVERY render, so a storage failure that threw would empty the whole root
  // (the 2026-09-16 blank-window report). Guarded on both sides.
  useEffect(() => {
    try {
      localStorage.setItem(SEEN_KEY(surface), fresh[surface]);
      for (const s of SURFACES) {
        if (localStorage.getItem(SEEN_KEY(s.id)) === null) {
          localStorage.setItem(SEEN_KEY(s.id), fresh[s.id]);
        }
      }
    } catch { /* no dots this render; the page itself is unaffected */ }
  });
  const seenValue = (id) => { try { return localStorage.getItem(SEEN_KEY(id)); } catch { return null; } };
  const lit = (id) => id !== surface
    && seenValue(id) !== null
    && seenValue(id) !== fresh[id];
  return (
    <>
      <InstallNotices view={view} />
      <EngineMismatch view={view} />
      {view.refresh?.running && view.refresh.seconds >= SCAN_NOTICE_AFTER_S ? (
        <div className="scan-notice" role="status">
          Reading your tools' logs. Numbers fill in as the scan goes; a long history can take a few minutes the first time.
        </div>
      ) : null}
      <UpdateBanner view={view} />
      <details ref={focusControls} className={`focus-controls${timer?.kind === "focus" ? " has-active-timer" : ""}`}
        open={focusOpen || Boolean(timer)} onToggle={event => setFocusOpen(event.currentTarget.open)}>
        <summary>{timer ? `${timer.kind === "focus" ? "Focus" : "Break"} timer active` : "Focus & breaks"}</summary>
        <p className="muted">Focus timers help you pause.</p>
      <ActionBar view={view} coaching={view.coaching || []}
                 load={view.training_load || "unknown"} refresh={refresh}
                 onTimerChange={setTimer} autoBreak={breakIntent} />
      </details>
      {onBreak ? <BreakPanel timer={timer} /> : null}
      <div className={onBreak ? "pagebody onbreak" : "pagebody"} style={{"--focus-offset": timer?.kind === "focus" ? `${focusHeight}px` : "0px"}}>
      <nav className="surfaces" aria-label="Main navigation">
        {SURFACES.filter(({menu}) => menu !== false).map(({id, label}) => <a key={id} href={`#${id}`} onClick={event => pick(event, id)}
          className={surface === id || (surface === "baseline" && id === "practice") ? "on" : ""} aria-current={surface === id || (surface === "baseline" && id === "practice") ? "page" : undefined}
          title={lit(id) ? "updated since you last looked" : undefined}>
          <svg className="navicon" viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true"><path d={ICONS[id] || ICONS.baseline} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          {label}
          {lit(id) ? <span className="navdot" aria-hidden="true" /> : null}
        </a>)}
      </nav>
      {practiceTime.data?.active && surface !== "practice" ? <aside className="practice-session-link">
        Practice session {practiceTime.data.active.running ? "recording" : "paused"} · <a href="#practice">Return to the timer</a>
      </aside> : null}
      <main id="page-content" ref={contentRef} tabIndex={-1} aria-label={SURFACES.find(page => page.id === surface).label}>
      {surface === "news" ? <Reading editorialOnly view={view} editions={<News view={view} />} /> : null}
      {surface === "community" ? <div className="layout col"><SectionIntro id="community" />
        <Community items={view.community || []} status={view.feed_status?.community} />
      </div> : null}
      {surface === "api-prices" ? <div className="layout col"><SectionIntro id="api-prices" /><TokenPrices /></div> : null}
      {surface === "spend" ? (
        <div className="layout col">
          <SectionIntro id="spend" />
          <UsageExplorer view={view} refresh={refresh} />
        </div>
      ) : null}
      {surface === "tools" ? (
        <div className="layout col">
          <SectionIntro id="tools" />
          <ToolVersions view={view} />
          <ToolFeatures view={view} />
        </div>
      ) : null}
      {surface === "skills" ? (
        <div className="layout col">
          <SectionIntro id="skills" />
          <SurfaceInsights view={view} surface="skills" />
          <ToolSkills view={view} />
        </div>
      ) : null}
      {surface === "connectors" ? (
        <div className="layout col">
          <SectionIntro id="connectors" />
          <SurfaceInsights view={view} surface="connectors" />
          <ToolConnectors view={view} />
        </div>
      ) : null}
      {surface === "practice" ? (
        <div className="layout col">
          <SectionIntro id="practice" />
          <PracticeWorkspace current={ongoing} suggestion={view.capability?.opportunity ? {title: view.capability.opportunity.title, body: view.capability.opportunity.why, action: "Review this suggestion"} : null} ready={!!practiceTime.data && !!learning.data}
            error={!!practiceTime.error || !!learning.error}
            coach={view.capability ? <CapabilityCoach capability={view.capability} refresh={refresh} /> : null}
            hours={<PracticeHistory controller={practiceTime} />}
            learning={<LearningPaths controller={learning} standalone />}
            prompts={view.playbook?.length ? <><SurfaceInsights view={view} surface="advice" /><Playbook view={view} /></> : null}
            patterns={null} />
        </div>
      ) : null}
      {surface === "baseline" ? (
        <div className="layout col">
          <a href="#practice">← Back to Practice</a>
          <BaselineBuilder />
        </div>
      ) : null}
      {surface === "mindfulness" ? (
        <div className="layout col">
          <SectionIntro id="mindfulness" />
          <SurfaceInsights view={view} surface="mindfulness" />
          <SessionTail view={view} />
          <WorkPatterns view={view} />
          <WorkMixCard view={view} />
        </div>
      ) : null}
      {surface === "models" ? (
        <div className="layout col">
          <SectionIntro id="models" />
          <SurfaceInsights view={view} surface="models" />
          <ModelsCard view={view} refresh={refresh} />
        </div>
      ) : null}
      {surface === "build" ? (
        <div className="layout col">
          <SectionIntro id="build" />
          <SurfaceInsights view={view} surface="build" />
          <BuildIdeas view={view} />
        </div>
      ) : null}
      {surface === "docs" ? (
        <div className="layout col">
          <SectionIntro id="docs" />
          <SurfaceInsights view={view} surface="docs" />
          <DocsCard view={view} />
        </div>
      ) : null}
      </main>
      </div>
    </>
  );
}

export default function App() {
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const [working, setWorking] = useState(null);
  const [theme, toggleTheme] = useTheme();
  const missesRef = useRef(0);
  // A routine refresh that never tears down a working view on a single blip: a
  // transient failure just skips this tick. But a run of failures means the
  // server this page is talking to is actually gone (the agent was restarted,
  // or a newer instance took over on a different port). The native window's
  // watchdog re-points us in that case; where there is no window (a browser
  // tab) a reload re-reads the current endpoint. Either way, recover instead of
  // silently showing stale data forever.
  // One request at a time: a poll or a saved setting while the last request
  // is still waiting asks for one more afterwards instead of stacking up.
  const refreshRef = useRef(null);
  if (refreshRef.current === null) {
    refreshRef.current = coalesce(() =>
      fetchView()
        .then((v) => { setView(v); setError(null); missesRef.current = 0; })
        .catch(() => {
          missesRef.current += 1;
          if (missesRef.current >= REFRESH_MISS_LIMIT) location.reload();
        }));
  }
  const refresh = refreshRef.current;
  // First load is resilient to a cold start: when the shortcut launches the
  // app, the engine may still be spinning up its server, so the very first
  // fetch can fail for a few seconds. Retry across that window before showing
  // the "could not reach" screen, rather than flashing an error immediately.
  const loadInitial = async () => {
    const deadline = Date.now() + 25000;
    for (;;) {
      try {
        setView(await fetchView());
        setError(null);
        missesRef.current = 0;
        return;
      } catch (e) {
        if (Date.now() >= deadline) { setError(String(e)); return; }
        await new Promise((r) => setTimeout(r, 700));
      }
    }
  };
  useEffect(() => {
    loadInitial();
    const pollPresence = () =>
      fetchPresence().then((p) => setWorking(p ? p.working : null)).catch(() => {});
    pollPresence();
    const presenceTimer = setInterval(pollPresence, PRESENCE_POLL_MS);
    const viewTimer = setInterval(refresh, VIEW_REFRESH_MS);
    return () => { clearInterval(presenceTimer); clearInterval(viewTimer); };
  }, []);
  // Poll quickly while history is read or exchange rates are downloading.
  const scanning = Boolean(view?.refresh?.running || view?.refresh?.fetching_rates);
  useEffect(() => {
    if (!scanning) return undefined;
    const scanTimer = setInterval(refresh, SCAN_REFRESH_MS);
    return () => clearInterval(scanTimer);
  }, [scanning]);

  if (error) {
    return (
      <div className="loading">
        <p>Could not reach the local agent ({error}).</p>
        <p className="muted">Start it with: practicegraph ui serve --open</p>
      </div>
    );
  }
  if (!view) {
    return (
      <div className="loading">
        <div className="spin" />
        <p>Reading your local record…</p>
      </div>
    );
  }
  return (
    <div className="wrap">
      <header className="top">
        <div className="mark">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <rect x="2.5" y="11" width="3.2" height="6.5" rx="1.1" fill="var(--on-teal)" opacity="0.55" />
            <rect x="8.4" y="7" width="3.2" height="10.5" rx="1.1" fill="var(--on-teal)" opacity="0.8" />
            <rect x="14.3" y="2.5" width="3.2" height="15" rx="1.1" fill="var(--on-teal)" />
            <circle cx="4.1" cy="6.5" r="1.6" fill="var(--teal-100)" />
          </svg>
        </div>
        <div className="brand">
          <div><b>practice</b>graph</div>
        </div>
        {working !== null ? (
          <span className={`presence ${working ? "on" : ""}`}>
            <i /> {working ? "working now" : "quiet"}
          </span>
        ) : null}
        <PrivacyStatus privacy={view.privacy} />
        <ScheduleSettings schedule={view.schedule} onSaved={refresh} />
        {view.currency ? (
          <CurrencySetting currency={view.currency}
            fetching={Boolean(view.refresh?.fetching_rates)} onSaved={refresh} />
        ) : null}
        <a className="ghlink" href="https://github.com/TeamLandiLTD/practicegraph-app" target="_blank" rel="noreferrer" title="PracticeGraph on GitHub">
          <GitHubMark />
          GitHub
        </a>
        <ThemeToggle theme={theme} toggle={toggleTheme} />
      </header>
      <Dashboard view={view} refresh={refresh} />
      <footer>
        {/* Statements left this footer in the page cleanup (design decision
            2026-08-21, "we need a clean interface"): the header's trust
            badge carries the privacy fact, and the billing lens lives
            behind the economy card's hint. Only the operational rate-card
            identity remains — a stale card prices new models at old rates
            and says nothing about it, so the age is shown rather than left
            to be discovered. */}
        <a className="landi-mark" href="https://teamlandi.com" target="_blank" rel="noreferrer" title="Team Landi">
          <img src="assets/team-landi.png" alt="Team Landi" width="106" height="26" />
        </a>
        <a className="issuelink" href="https://github.com/TeamLandiLTD/practicegraph-app/issues" target="_blank" rel="noreferrer">Report an issue</a>
        <span className="appversion">v{view.app_version}</span>
        <span title={view.rate_card_age_days != null
          ? `published ${view.rate_card_age_days} ${view.rate_card_age_days === 1 ? "day" : "days"} ago`
          : "supplied by your organisation"}>
          rate card {view.rate_card_version}
          {view.rate_card_stale ? " · may be out of date" : ""}
        </span>
      </footer>
    </div>
  );
}
