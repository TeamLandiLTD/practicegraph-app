import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App, { BaselineBuilder, Dashboard, ScheduleSettings, Training } from "./App.jsx";
import * as api from "./api.js";

// The chosen category rides the URL hash and the seen-fingerprints ride
// localStorage; clear both so no test inherits the surface or the baseline
// a previous test established.
beforeEach(() => {
  window.history.replaceState(null, "", "#");
  localStorage.clear();
});

async function openPage(user, id) {
  const pages = {
    reading: "News", news: "News", community: "Community", build: "Build ideas",
    practice: "Practice", history: "Practice hours", learning: "Learning paths",
    baseline: "Baseline", mindfulness: "Work rhythm", advice: "Advice",
    models: "Models", tools: "Tools", skills: "Agent skills", connectors: "Integrations",
    projects: "Projects", docs: "Guides", setup: "Improve setup", spend: "Usage",
  };
  const label = pages[id];
  const link = within(screen.getByRole("navigation", {name: "Main navigation"})).queryByRole("link", {name: label, exact: true});
  if (link) await user.click(link);
  else {
    // Pages off the menu (Practice, for now) stay reachable by address.
    await act(async () => { window.location.hash = id; window.dispatchEvent(new HashChangeEvent("hashchange")); });
  }
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", label));
}

function makeView() {
  return {
    schema: "practicegraph.view/2",
    practice_observation: {
      observation_id: "protected-blocks",
      title: "Protected blocks are repeating",
      body: "Protected blocks appeared on 6 of the last 28 days.",
      period: "last 28 days",
      confidence: "repeated pattern",
      caveat: "A work-pattern observation, not a health assessment.",
    },
    schedule: {
      version: 0,
      confirmed: false,
      timezone_name: "Europe/Sofia",
      tzdata_version: "2026.2",
      working_days: [0, 1, 2, 3, 4],
      work_start: "09:00",
      work_end: "18:00",
      quiet_start: "22:00",
      quiet_end: "07:00",
      weekend_mode: "exceptional",
    },
    day: "2026-07-12",
    local_day: "2026-07-12",
    accounting_day_utc: "2026-07-11",
    coaching: [{
      pillar: "discipline",
      label: "Review & judgment",
      tone: "watch",
      summary: "Read the handoff before choosing the next step.",
      cue: "Review the observed failure before continuing.",
      measures: "Review timing and failure follow-up.",
    }],
    coach_ack: null,
    training_load: "steady",
    ribbon: { segments: [], ticks: [] },
    tips: [],
    blocks: {
      "block-started": 0,
      "block-completed": 0,
      "break-started": 0,
      "break-completed": 0,
    },
    focus: null,
    rhythm: null,
    runway: null,
    dayclose: { closed: false },
    weekly: null,
    reflections: [],
    queue: [],
    ranges: [],
    performance: null,
    weekly_scores: [],
    briefings: [],
    model_recommendations: [],
    skills: [],
    totals: {
      cost_micro_usd: 0,
      assistant_turns: 0,
      sessions: 0,
      unpriced_turns: 0,
    },
    dimension_labels: {},
    noticed: {},
    work_mix: {},
    maturity: [],
    findings: [],
  };
}

it("gives empty connectors and advice an explanation and a working next destination", async () => {
  const user = userEvent.setup();
  const {container} = render(<Dashboard view={makeView()} refresh={vi.fn()} />);
  await openPage(user, "connectors");
  expect(screen.getByText("No configured integrations found")).toBeVisible();
  await user.click(screen.getByRole("link", {name: "Browse the setup guides"}));
  expect(await screen.findByRole("link", {name: "Guides", current: "page"})).toBeVisible();
  // Advice lives on Practice now; with nothing to say it renders nothing.
  await openPage(user, "practice");
  expect(screen.queryByText("No tailored advice yet")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", {name: "Prompts to try"})).not.toBeInTheDocument();
  expect(container.querySelector(".usage-basis")).toBeNull();
});

function withPracticeStats(view = makeView()) {
  return {
    ...view,
    focus: {
      longest_block_min: 89,
      approval_moments: 1,
      waved_through: 0,
      assistant_followups: 1,
      reflex_replies: 0,
      refire_replies: 0,
    },
    rhythm: {
      quiet_hours_activity_pct: 21,
      long_streak_days: 6,
      waiting_minutes: 3808,
      distinct_projects: 55,
      distinct_branches: 86,
    },
  };
}

function withRichNews(view = makeView()) {
  return {
    ...view,
    schedule: { ...view.schedule, confirmed: true },
    news: [
      {
        id: "first-agent-story",
        kind: "release",
        source: "OpenAI",
        title: "First agent headline",
        hook: "The first short reason to care.",
        summary: "The first full summary.",
        why: "It changes how agent work is reviewed.",
        url: "https://example.com/first",
      },
      {
        id: "second-agent-story",
        kind: "post",
        source: "Independent",
        title: "Second agent headline",
        hook: "The second short reason to care.",
        summary: "The second full summary.",
        why: "It adds a useful counterpoint.",
        url: "https://example.com/second",
      },
    ],
  };
}

describe("BaselineBuilder", () => {
  it("opens from the interface with a research-backed editable draft", async () => {
    const user = userEvent.setup();
    render(<Dashboard view={makeView()} refresh={vi.fn()} />);

    // Baseline left the menu (2026-09-10): a checklist, reached from Practice.
    await openPage(user, "practice");
    expect(within(screen.getByRole("navigation", {name: "Main navigation"}))
      .queryByRole("link", {name: "Baseline"})).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", {name: "AI working checklist"}));
    await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Baseline"));
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", {name: "Edit checklist"}));

    expect(screen.getByRole("heading", {name: "AI working checklist"})).toBeVisible();
    expect(screen.getAllByRole("textbox")).toHaveLength(10);
    expect(screen.getByDisplayValue(/Frame a task with an outcome/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Add practice" })).toBeVisible();
  });

  it("keeps revisions on this machine", async () => {
    const user = userEvent.setup();
    const first = render(<BaselineBuilder />);
    await user.click(screen.getByRole("button", {name: "Edit checklist"}));
    const item = screen.getByRole("textbox", { name: "Baseline practice 1" });

    await user.clear(item);
    await user.type(item, "I can verify a bounded change.");
    first.unmount();
    render(<BaselineBuilder />);
    await user.click(screen.getByRole("button", {name: "Edit checklist"}));

    expect(screen.getByDisplayValue("I can verify a bounded change.")).toBeVisible();
  });
});

describe("the actionable page", () => {
  it("keeps a personal rating separate from closing the day", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "checkin").mockResolvedValue(true);
    const close = vi.spyOn(api, "closeDay").mockResolvedValue(true);
    render(<Dashboard view={makeView()} refresh={vi.fn()} />);
    await userEvent.setup().click(screen.getByText("Focus & breaks"));
    await user.click(screen.getByRole("button", {name: "Close the day", expanded: false}));
    await user.click(screen.getByRole("button", {name: "4"}));
    expect(api.checkin).toHaveBeenCalledWith(4);
    expect(close).not.toHaveBeenCalled();
    const finish = screen.getAllByRole("button", {name: "Close the day"}).at(-1);
    expect(finish).toBeVisible();
    await user.click(finish);
    await user.click(screen.getByRole("button", {name: "Skip this one"}));
    expect(close).toHaveBeenCalledWith("2026-07-12", undefined);
  });

  it("keeps each category on its own surface behind the nav", async () => {
    vi.spyOn(api, "fetchUsage").mockResolvedValue({period: "7d", page: 0, page_size: 30,
      from_day: "2026-07-06", to_day: "2026-07-12", summary: {cost_micro_usd: 0,
        tokens_total: 0, assistant_turns: 0, sessions: 0, models: []},
      drivers: {without_test_attempt: 0}, sessions: [], series: [], families: []});
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      playbook: [{
        id: "x", title: "A move", finding: "F", why: "Why.",
        prompt: "Goal: x. Done when: y.", codex_url: "",
      }],
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    // Usage is the original landing page; the moves live on Practice now.
    expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Usage");
    expect(await screen.findByText(/No assistant usage is recorded for this period/)).toBeVisible();
    expect(screen.queryByRole("heading", {name: "Prompts to try"})).not.toBeInTheDocument();
    await openPage(user, "practice");
    expect(screen.queryByText("assistant turns")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Prompts to try"})).toBeInTheDocument();
    await openPage(user, "spend");
    expect(await screen.findByText(/No assistant usage is recorded for this period/)).toBeVisible();
  });

  it("lights a category when something new arrives for it", async () => {
    const user = userEvent.setup();
    const first = withRichNews();
    const { rerender, container } = render(
      <Dashboard view={first} refresh={vi.fn()} />,
    );
    // The first look establishes the baseline — nothing is lit.
    expect(container.querySelector(".navdot")).toBeNull();
    const more = {
      ...first,
      news: [...first.news, {
        id: "third-agent-story", kind: "post", source: "Later",
        title: "Third agent headline", hook: "New since the baseline.",
        summary: "The third full summary.", why: "It is new.",
        url: "https://example.com/third",
      }],
    };
    rerender(<Dashboard view={more} refresh={vi.fn()} />);
    const newsTab = screen.getByRole("link", { name: "News", exact: true });
    expect(newsTab.querySelector(".navdot")).not.toBeNull();
    // Only the surface that actually changed lights up.
    expect(container.querySelectorAll(".navdot")).toHaveLength(1);
    // Opening the surface marks it seen, and the dot stays gone for the
    // same content — even after moving away again.
    await user.click(newsTab);
    rerender(<Dashboard view={more} refresh={vi.fn()} />);
    expect(container.querySelector(".navdot")).toBeNull();
    await openPage(user, "spend");
    expect(container.querySelector(".navdot")).toBeNull();
  });

  it("keeps timers voluntary when retries and activity scores change", async () => {
    const user = userEvent.setup();
    const view = { ...makeView(), noticed: { refires_today: 300 }, training_load: "heavy",
      coaching: [{ pillar: "pattern", tone: "watch" }] };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await userEvent.setup().click(screen.getByText("Focus & breaks"));
    expect(screen.getByRole("button", { name: /Quick break/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Long rest/ })).not.toHaveClass("primary");
    expect(screen.queryByText(/step away|focus is getting split/)).not.toBeInTheDocument();
  });

  it("starts the guided break at once when opened through the toast", () => {
    // The shell appends #break when the break nudge is tapped; the page
    // consumes it exactly once and the takeover is already running.
    window.history.replaceState(null, "", "#break");
    const { container } = render(<Dashboard view={makeView()} refresh={vi.fn()} />);
    expect(container.querySelector(".breakpanel")).not.toBeNull();
    expect(container.querySelector(".pagebody.onbreak")).not.toBeNull();
    // The intent is consumed: the hash no longer carries it.
    expect(window.location.hash).not.toBe("#break");
  });

  it("takes over with the guided break and dims the page while it runs", async () => {
    const user = userEvent.setup();
    const { container } = render(<Dashboard view={makeView()} refresh={vi.fn()} />);
    await userEvent.setup().click(screen.getByText("Focus & breaks"));
    expect(container.querySelector(".breakpanel")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Quick break/ }));
    expect(container.querySelector(".breakpanel")).not.toBeNull();
    expect(screen.getByText(/the break happens away from it/)).toBeVisible();
    expect(screen.getByText(/Fill a glass of water/)).toBeVisible();
    // ONE clock: the action bar's running band yields to the takeover —
    // the panel is the break's only surface (owner report 2026-08-21:
    // "two clocks?").
    expect(container.querySelector(".timer.running")).toBeNull();
    expect(screen.queryByText(/in progress/)).not.toBeInTheDocument();
    // The bounded puzzles wait behind their fold, and the page body dims.
    expect(screen.getByText(/it ends when the break ends/)).toBeInTheDocument();
    expect(container.querySelector(".pagebody.onbreak")).not.toBeNull();
    // Two named options; one mounted at a time, each structurally bounded.
    await user.click(screen.getByText(/Stuck at the desk/));
    await user.click(screen.getByRole("button", { name: "Sudoku" }));
    expect(container.querySelectorAll(".sudocell")).toHaveLength(81);
    expect(container.querySelectorAll(".sudocell.given")).toHaveLength(36);
    expect(container.querySelector(".tetboard")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Falling blocks" }));
    expect(container.querySelector(".tetboard")).not.toBeNull();
    expect(container.querySelector(".sudogrid")).toBeNull();
    // The way out lives in the panel, and taking it returns the page.
    await user.click(screen.getByRole("button", { name: "End the break early" }));
    expect(container.querySelector(".breakpanel")).toBeNull();
    expect(container.querySelector(".pagebody.onbreak")).toBeNull();
    expect(screen.getByRole("button", { name: /Quick break/ })).toBeVisible();
  });

  it("shows the model ladder with pins judged against the catalog", async () => {
    // Owner order 2026-08-21: the models section must show what is
    // AVAILABLE (models and reasoning efforts) and what is currently set
    // versus the recommended floor. The catalog is served; the pins are
    // this machine's own config, marked server-side.
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      model_catalog: [
        { tool: "claude_code", tool_label: "Claude Code",
          version: "catalog-test",
          models: [
            { model: "Claude Opus", role: "strongest",
              when: "Work that must not be wrong.",
              price_note: "the premium rate", pinned: false },
            { model: "Claude Sonnet", role: "everyday",
              when: "Daily coding.", price_note: "the middle rate",
              pinned: false },
          ],
          efforts: [
            { level: "medium", when: "Well-scoped tasks.", tone: "good",
              recommended: false, pinned: false },
            { level: "high", when: "The harness default.", tone: "neutral",
              recommended: true, pinned: true },
          ],
          pinned_model: null, pinned_effort: "high" },
        { tool: "codex", tool_label: "Codex", version: "catalog-test",
          models: [
            { model: "gpt-5.6-sol", role: "strongest",
              when: "High-stakes work.", price_note: "several times the fast",
              pinned: true },
            { model: "gpt-5.6-terra", role: "everyday",
              when: "Normal daily work.", price_note: "about half",
              pinned: false },
          ],
          efforts: [
            { level: "medium", when: "The everyday setting.", tone: "good",
              recommended: true, pinned: false },
            { level: "xhigh", when: "The ceiling.", tone: "costly",
              recommended: false, pinned: true },
          ],
          pinned_model: "gpt-5.6-sol", pinned_effort: "xhigh",
          practices: [
            { title: "Cheap per word is not cheap per task",
              body: "Smaller models write more tokens to finish the job." },
          ],
          practices_source: "Artificial Analysis Coding Agent Index, read "
            + "2026-07-30.",
          switch: ["Start a new task.", "Open the model picker."],
          closing: "Start with the least expensive model that can "
            + "comfortably carry the work." },
      ],
    };
    const { container } = render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "models");
    // The comparison is open by default (owner call 2026-09-11).
    for (const fold of container.querySelectorAll("details.model-comparison")) expect(fold).not.toHaveAttribute("open");
    await user.click(within(screen.getByRole("group", {name: "Tool defaults"})).getByRole("button", {name: "Codex"}));
    await user.click(screen.getByText(/Compare models and reasoning/));
    // The Codex pin lands on the strongest tile AND the costliest effort,
    // and the composed line points at the recommended floor.
    expect(screen.getByText(/You start on gpt-5.6-sol · xhigh/)).toBeVisible();
    expect(screen.getByText(/Recommended: gpt-5.6-terra · medium/)).toBeVisible();
    expect(screen.getAllByText("Recommended start").length).toBeGreaterThan(0);
    expect(screen.queryByText(/starts-on lines/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Your default").length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("Suggested minimum").length)
      .toBeGreaterThanOrEqual(1);
    expect(container.querySelector(".modeltile.eff.costly")).not.toBeNull();
    // The best-practice teaching rides the served catalog: folded facts
    // with their stated source, the switch steps, and the closing rule.
    const factsFold = screen.getByText(/What the benchmarks add/);
    expect(factsFold).toBeInTheDocument();
    await user.click(factsFold);
    expect(screen.getByText(/Cheap per word is not cheap per task/))
      .toBeVisible();
    expect(screen.getByText(/Artificial Analysis Coding Agent Index/))
      .toBeVisible();
    expect(screen.getByText(/How to switch — under a minute/))
      .toBeInTheDocument();
    expect(screen.getByText(/least expensive model that can comfortably/))
      .toBeVisible();
    await user.click(within(screen.getByRole("group", {name: "Tool defaults"})).getByRole("button", {name: "Claude Code"}));
    // Claude: no pinned model, effort pinned at the harness default.
    expect(screen.getByText(/at high effort/)).toBeVisible();
    expect(screen.getByText(/That is the recommended floor/)).toBeVisible();

  });

  it("speaks to the productivity audience when the profile says so", async () => {
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      profile: "productivity",
      work_mix: [
        { tool: "codex", tool_label: "Codex", window_days: 30, sessions: 100,
          mix: [
            { id: "documents", label: "Document work",
              doc: "Office artifacts moved through the session.",
              sessions: 60, share_pct: 60 },
            { id: "writing", label: "Drafting and thinking",
              doc: "Conversation only: the work was the words.",
              sessions: 40, share_pct: 40 },
          ] },
      ],
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    // The lead reading and the classified card, rule on every tile.
    await openPage(user, "mindfulness");
    expect(screen.getByText("What the work was")).toBeVisible();
    expect(screen.getByText("Document work")).toBeVisible();
    expect(screen.getByText(/Office artifacts moved through/)).toBeVisible();
  });

  it("does not infer a different identity from classified session counts", async () => {
    const user = userEvent.setup();
    render(<Dashboard view={{ ...makeView(), work_mix: [{tool: "codex", mix: [
      {id: "documents", label: "Document work", sessions: 100, share_pct: 100}]}] }} refresh={vi.fn()} />);
    await openPage(user, "practice");
    expect(screen.queryByText(/profile may read this machine/)).not.toBeInTheDocument();
  });

  it("does not push a coder toward productivity over read-only sessions", async () => {
    // The owner's own month: 1,140 sessions, 53% conversation-only and 24%
    // tool use without an edit - and 15% code edits. That is how code work
    // looks from the logs; the old "not code work" share read it as 83%.
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      profile: "coding",
      work_mix: [
        { tool: "claude_code", tool_label: "Claude Code", window_days: 30,
          sessions: 100,
          mix: [
            { id: "writing", label: "Drafting and thinking",
              doc: "Conversation only.", sessions: 53, share_pct: 53 },
            { id: "organizing", label: "Organizing", doc: "Tool use.",
              sessions: 24, share_pct: 24 },
            { id: "coding", label: "Code work", doc: "Code was edited.",
              sessions: 15, share_pct: 15 },
            { id: "documents", label: "Document work",
              doc: "Office artifacts.", sessions: 5, share_pct: 5 },
            { id: "research", label: "Research", doc: "Web.",
              sessions: 3, share_pct: 3 },
          ] },
      ],
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    expect(screen.queryByText(/productivity profile may read/))
      .not.toBeInTheDocument();
  });

  it("omits failure details and reflections even when records are available", async () => {
    const user = userEvent.setup();
    const view = { ...makeView(), verification: { available: true, window_days: 28,
      components: [{id: "failures", label: "Command or test failures", unit: "failures", value: 130,
        delta: 24, doc: "Recorded failures."}, {id: "iterations_to_pass", label: "Tries before it worked", value: 3}]},
      drain: {pending: true, skip_label: "Not today", options: [{id: "worn", label: "Worn"}]} };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    await openPage(user, "mindfulness");
    expect(screen.queryByText(/Recorded failures and retries/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Your own reflection/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Worn"})).not.toBeInTheDocument();
  });

  it("keeps the focus block band with its clock and stop in the bar", async () => {
    // A focus block never takes over the page, so its clock and Stop stay
    // in the action bar — the one-surface rule is about breaks.
    const user = userEvent.setup();
    const { container } = render(<Dashboard view={makeView()} refresh={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /Focus block/ }));
    expect(container.querySelector(".timer.running.focus")).not.toBeNull();
    expect(screen.getByText(/focus block in progress/)).toBeVisible();
    expect(container.querySelector(".breakpanel")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(container.querySelector(".timer.running")).toBeNull();
  });

  it("shows the harness inventory on tools and marks installed skills", async () => {
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      tools: {
        versions: [
          { tool: "claude_code", tool_label: "Claude Code",
            installed: "2.1.234", latest: "2.1.240",
            notes_url: "https://github.com/anthropics/claude-code/releases/tag/v2.1.240",
            newer: true },
          { tool: "codex", tool_label: "Codex",
            installed: "0.148.0", latest: null, notes_url: null,
            newer: false },
        ],
        connectors: [
          { tool: "codex", tool_label: "Codex",
            mcp: [{ name: "node_repl", enabled: true, scope: "global",
                    calls: 162 },
                  { name: "xcode", enabled: false, scope: "global",
                    calls: 0 }],
            plugins: [] },
          { tool: "claude_code", tool_label: "Claude Code",
            mcp: [{ name: "garmin", enabled: true, scope: "global",
                    calls: 71 }],
            plugins: [{ name: "vercel@claude-plugins-official",
                        scope: "user", version: "0.45.1" }] },
        ],
        projects: [
          { tool: "claude_code", tool_label: "Claude Code", window_days: 30,
            projects: [
              { name: "PracticeGraph", sessions: 41, last_day: "2026-08-22" },
              { name: "aae-kit-v2", sessions: 7, last_day: "2026-08-19" },
            ] },
          { tool: "codex", tool_label: "Codex", window_days: 30,
            projects: [
              { name: "Dynaphos ERP", sessions: 3, last_day: "2026-08-19" },
            ] },
        ],
        features: [
          { tool: "claude_code", tool_label: "Claude Code",
            window_days: 30, sessions: 1017,
            features: [
              { id: "web", label: "Web research",
                doc: "Searches and page fetches the agent ran.",
                count: 2396 },
              { id: "design", label: "Claude Design",
                doc: "The design surface: syncing a visual design system.",
                count: 1 },
            ] },
          { tool: "codex", tool_label: "Codex",
            window_days: 30, sessions: 69, features: [] },
        ],
        skills: [
          { tool: "claude_code", tool_label: "Claude Code",
            skills: [
              { name: "ai-advisor",
                about: "Industry intelligence grounded in a local "
                  + "knowledge base." },
              { name: "frontend-design", about: null },
              { name: "two-liner",
                about: "First sentence stays. Second sentence goes behind the title." },
              { name: "codex-cost",
                about: "Use when the user asks about Codex Maturity Index, Codex spend, "
                  + "credits, tokens, cost drivers, user-level usage analysis, workstream "
                  + "attribution, FinOps-style overspend signals, model rightsizing." },
            ] },
          { tool: "codex", tool_label: "Codex", skills: [] },
        ],
      },
      skills: [{
        id: "frontend-design", title: "Frontend design", summary: "S.",
        prompt: "P.", reasons: [], for_work: [], installed: true,
      }],
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    // Owner call 2026-08-21: versions, skills and connectors are each
    // their OWN tab, every one opening with its green summary.
    await openPage(user, "tools");
    // The card row says it once; no green summary repeats it above.
    expect(screen.queryByText(/A newer Claude Code is available/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /newer available — 2.1.240/ }))
      .toHaveAttribute("href", view.tools.versions[0].notes_url);
    expect(screen.queryByText("ai-advisor")).not.toBeInTheDocument();
    // Features in use: each classified capability renders with its own
    // documentation line and its invocation count; a harness with nothing
    // classified renders no group.
    // Feature use is the page's substance: open, no fold to find first.
    expect(screen.queryByText("Recorded feature use")).not.toBeInTheDocument();
    expect(screen.getByText("Claude Design")).toBeVisible();
    expect(screen.getByText(/syncing a visual design system/)).toBeVisible();
    expect(screen.getByText(/2,396 calls/)).toBeInTheDocument();
    // Connectors show their off switches, and the summary counts them.
    // (Summary fragments avoid digits: reflectionRich splits numbers into
    // their own nodes.)
    await openPage(user, "connectors");
    expect(screen.getByText("Disabled")).toBeVisible();
    // How much each connector is used, and the honest zero for one that
    // is declared but idle.
    expect(screen.getByText(/162 recent calls/)).toBeInTheDocument();
    expect(screen.getByText(/0 recent calls/)).toBeInTheDocument();
    expect(screen.queryByText(/not measured yet/)).not.toBeInTheDocument();
    // Skills render in the features language: each name with the
    // description the skill states about itself, and an honest line for
    // one that states none. The registry shelf sits beside them, marking
    // what is already on disk.
    await openPage(user, "skills");
    expect(screen.queryByText(/Skills you already have are marked/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "ai advisor"})).toBeVisible();
    expect(screen.getAllByText(/Industry intelligence grounded/)[0]).toBeVisible();
    const skill = screen.getByRole("heading", {name: "two liner"}).closest("article");
    expect(within(skill).getAllByText(/First sentence stays/)).toHaveLength(1);
    expect(within(skill).queryByText("Details")).not.toBeInTheDocument();
    expect(within(skill).getByText(/Second sentence goes/)).toBeVisible();
    expect(screen.getAllByText("No description recorded.")[0]).toBeVisible();
    expect(screen.getAllByText(/^Use when the user asks about Codex Maturity Index/)[0]).toHaveTextContent(/model rightsizing\.$/);
    expect(screen.queryByRole("button", {name: "Suggested"})).not.toBeInTheDocument();
  });

  it("answers a finding with a prompt, a copy lane, and the codex link", async () => {
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      playbook: [{
        id: "context_bloat",
        title: "Cut the carried context",
        finding: "Heavy context per turn",
        why: "Turns carried about 669.6K prompt tokens each today.",
        prompt: "Goal: carry less context per turn. Measured from my local "
          + "logs: about 669.6K prompt tokens per turn. Done when: I have "
          + "the note.",
        codex_url: "codex://new?prompt=Goal%3A%20carry%20less",
      }],
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    await user.click(screen.getByText("Prompts to try", {selector: "summary"}));
    expect(screen.getByRole("heading", {name: "Prompts to try"})).toBeInTheDocument();
    // Folded by default: the copy lane rides the one-line summary; the
    // ten-line prompt body is one click away, not on the page.
    expect(screen.getByText("Cut the carried context")).toBeVisible();
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeVisible();
    expect(screen.getByText(/Measured from my local logs/)).not.toBeVisible();
    await user.click(screen.getByText("Cut the carried context"));
    expect(screen.getByText(/Measured from my local logs/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Open in Codex" }))
      .toHaveAttribute("href", view.playbook[0].codex_url);
  });

  it("states a shared finding once across the moves that answer it", async () => {
    const user = userEvent.setup();
    const move = (id, title) => ({id, title, finding: "Premium heavy", prompt: `Goal: ${id}.`,
      why: "Premium models carried 90% of priced spend today.", codex_url: null});
    render(<Dashboard view={{...makeView(), playbook: [
      move("a", "Find out what your sessions start on"), move("b", "Draft a routing rule")]}} refresh={vi.fn()} />);
    await openPage(user, "practice");
    expect(screen.getAllByText("Premium models carried 90% of priced spend today.")).toHaveLength(1);
    expect(document.querySelector(".usage-basis")).toBeNull();
  });

  it("holds practice hours, learning paths and advice on one page", async () => {
    const user = userEvent.setup();
    const view = {...makeView(), playbook: [{id: "x", title: "A move", finding: "F", why: "Why.",
      prompt: "Goal: x. Done when: y.", codex_url: ""}]};
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    expect(screen.getByText("Your practice history")).toBeInTheDocument();
    expect(screen.getByText("Learning resources")).toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Prompts to try"})).toBeInTheDocument();
    expect(screen.queryByRole("link", {name: "Improve a project setup"})).not.toBeInTheDocument();
    expect(screen.queryByRole("link", {name: "Record practice hours"})).not.toBeInTheDocument();
    const nav = within(screen.getByRole("navigation", {name: "Main navigation"}));
    for (const gone of ["Practice hours", "Learning paths", "Advice", "Projects", "Improve setup"]) {
      expect(nav.queryByRole("link", {name: gone})).not.toBeInTheDocument();
    }
  });

  it("opens the last-prompt history first on Mindfulness and keeps quiet hours below it", async () => {
    const user = userEvent.setup();
    const view = {...makeView(), schedule: {...makeView().schedule, confirmed: true},
      quiet_hours: {from_day: "2026-07-06", to_day: "2026-07-12", observed_days: 5, quiet_days: 2,
        prior_observed_days: 0, prior_quiet_days: 0},
      session_tail: {active_days: 10, intro: "I.", headline: "H.", note: "N.", drift_line: "",
        chart: {axis_lo: 0, axis_hi: 1440, grid: [[720, "12:00"]], quiet_start: 1320},
        weeks: [{start_day: "2026-06-29", minutes: 600, clock: "10:00"},
          {start_day: "2026-07-06", minutes: 660, clock: "11:00"}]}};
    const {container} = render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "mindfulness");
    const tail = container.querySelector("details.sessiontail");
    expect(tail).toHaveAttribute("open");
    const quiet = container.querySelector(".quiet-hours");
    expect(tail.compareDocumentPosition(quiet) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("refuses a playbook link that is not the codex composer", async () => {
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      playbook: [{
        id: "x", title: "A move", finding: "F", why: "Why.",
        prompt: "Goal: x. Done when: y.",
        codex_url: "https://evil.example.com/phish",
      }],
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    await user.click(screen.getByText("Prompts to try", {selector: "summary"}));
    await user.click(screen.getByText("A move"));
    // The prompt and copy lane stay; the link simply does not render.
    expect(screen.getByRole("button", { name: "Copy prompt" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Open in Codex" }))
      .not.toBeInTheDocument();
  });

  it("no longer renders the cut surfaces", () => {
    const { container } = render(
      <Dashboard view={makeView()} refresh={vi.fn()} />,
    );
    expect(container.querySelector(".layout")).not.toBeNull();
    expect(container.querySelector("[data-primary-section]")).toBeNull();
    expect(screen.queryByText(/Your practice ·/)).not.toBeInTheDocument();
    expect(screen.queryByText("Usage and cost")).not.toBeInTheDocument();
    expect(container.querySelector(".ribbon")).toBeNull();
  });

  it("retires ability scores even when a legacy payload supplies them", () => {
    const view = {...withPracticeStats(), conditioning: [
      {id: "capacity", label: "Capacity", value: 261}, {id: "composure", label: "Composure", value: 88},
      {id: "load_balance", label: "Load balance", value: 66}]};
    const {container} = render(<Training view={view} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("explains attention continuity from human actions instead of agent volume", async () => {
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      noticed: {
        attention_confident: 1,
        attention_high_switch_days_28d: 4,
        attention_active_days_28d: 18,
      },
    };
    render(<Dashboard view={view} refresh={vi.fn()} />);
    // With no reading yet, the plain noticed facts live on practice.
    await openPage(user, "practice");

    await openPage(user, "mindfulness");
    const fact = screen.getByText(/On 4 of 18 observed/);
    expect(fact).toBeVisible();
    expect(screen.getByText(/Agent and tool traffic is excluded/)).toBeVisible();
    expect(screen.queryByText(/moved between sessions mid-flow/))
      .not.toBeInTheDocument();
  });

  it("gives quiet hours one home and does not replay old fitness reflections", async () => {
    const user = userEvent.setup();
    const view = {...makeView(), schedule: {...makeView().schedule, confirmed: true},
      quiet_hours: {from_day: "2026-07-06", to_day: "2026-07-12", observed_days: 5, quiet_days: 2,
        prior_observed_days: 3, prior_quiet_days: 0},
      reflections: [{id: "pace", text: "The sharpest hour follows a break."}]};
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "practice");
    expect(screen.queryByText(/reached your quiet hours/)).not.toBeInTheDocument();
    await openPage(user, "mindfulness");
    expect(screen.getByText(/on 2 of 5 observed days/)).toBeVisible();
    expect(screen.getByText(/more than the week before \(0 of 3\)/)).toBeVisible();
    expect(screen.getByText(/not your complete working day/, {selector: ".infotip"})).toBeInTheDocument();
    expect(screen.queryByText(/sharpest hour/)).not.toBeInTheDocument();
  });

  it("does not substitute log volume when human-origin coverage is missing", () => {
    const {container} = render(<Training view={withPracticeStats()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("does not relabel an event percentage as days in quiet hours", async () => {
    const user = userEvent.setup();
    render(<Dashboard view={withPracticeStats()} refresh={vi.fn()} />);
    await openPage(user, "mindfulness");
    expect(screen.queryByText(/reached your quiet hours/)).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/undefined%|null%/);
  });

  it("opens news sources from titles and expands summaries only on request", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <Dashboard view={withRichNews()} refresh={vi.fn()} />,
    );
    // The magazine lives inside Reading now.
    await openPage(user, "reading");

    const first = screen.getByRole("button", { name: "First agent headline" });
    const second = screen.getByRole("button", { name: "Second agent headline" });
    const firstPanel = document.getElementById(first.getAttribute("aria-controls"));
    const secondPanel = document.getElementById(second.getAttribute("aria-controls"));
    const firstTeaser = screen.getByText("The first short reason to care.");
    const firstMeta = first.closest(".newsentry").querySelector(".newsmeta");

    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(firstTeaser).toHaveClass("newsteaser");
    expect(screen.getByRole("link", {name: "First agent headline"})).toHaveAttribute("href", "https://example.com/first");
    expect(firstTeaser.closest(".newsreveal")).toBeNull();
    expect(screen.getAllByText("The first short reason to care.")).toHaveLength(1);
    expect(firstMeta).not.toHaveTextContent("release");
    expect(firstMeta).toHaveTextContent("OpenAI");
    expect(first.closest(".newsentry").querySelector(".newstitlelink")).toHaveAttribute(
      "href", "https://example.com/first",
    );
    expect(firstMeta.closest(".newsreveal")).toBeNull();
    expect(firstPanel).toHaveAttribute("aria-hidden", "true");
    expect(firstPanel).toHaveAttribute("inert");
    expect(container.querySelectorAll(".newstitlebutton")).toHaveLength(2);
    expect(container.querySelectorAll(".newsreveal")).toHaveLength(2);
    expect(container.querySelector(".richnewstoggle")).toBeNull();

    await user.hover(first.closest(".newsentry"));
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(firstPanel).toHaveAttribute("aria-hidden", "true");
    await user.unhover(first.closest(".newsentry"));
    expect(firstPanel).toHaveAttribute("aria-hidden", "true");

    act(() => first.focus());
    expect(first).toHaveFocus();
    expect(firstPanel).toHaveAttribute("aria-hidden", "true");

    await user.click(first);
    expect(first).toHaveAttribute("aria-expanded", "true");
    await user.click(second);
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(firstPanel).toHaveAttribute("aria-hidden", "true");
    expect(second).toHaveAttribute("aria-expanded", "true");
    expect(secondPanel).toHaveAttribute("aria-hidden", "false");
  });

  it("hides the State card when conditioning and compact statistics are empty", () => {
    const { container } = render(
      <Training view={{ ...makeView(), conditioning: [] }} refresh={vi.fn()} />,
    );
    expect(container.querySelector(".card")).toBeNull();
  });

  it("saves the full confirmed schedule", async () => {
    const user = userEvent.setup();
    const savedSchedule = { ...makeView().schedule, version: 1, confirmed: true };
    const save = vi.fn().mockResolvedValue({ ok: true, schedule: savedSchedule });
    const onSaved = vi.fn();
    render(
      <ScheduleSettings
        schedule={makeView().schedule}
        onSaved={onSaved}
        save={save}
      />,
    );

    expect(screen.getByText(/Confirm your working hours/i)).toBeVisible();
    await user.click(screen.getByText("Confirm your working hours"));
    await user.selectOptions(screen.getByLabelText("Timezone"), "Europe/Sofia");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(save).toHaveBeenCalledWith({
      timezone_name: "Europe/Sofia",
      working_days: [0, 1, 2, 3, 4],
      work_start: "09:00",
      work_end: "18:00",
      quiet_start: "22:00",
      quiet_end: "07:00",
      weekend_mode: "exceptional",
    });
    expect(onSaved).toHaveBeenCalledWith({ ok: true, schedule: savedSchedule });
  });

  it("keeps schedule values after a save error", async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockRejectedValue(new Error("invalid timezone"));
    render(<ScheduleSettings schedule={makeView().schedule} onSaved={vi.fn()} save={save} />);

    await user.click(screen.getByText("Confirm your working hours"));
    await user.clear(screen.getByLabelText("Other IANA timezone"));
    await user.type(screen.getByLabelText("Other IANA timezone"), "Mars/Base");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save that schedule");
    expect(screen.getByLabelText("Other IANA timezone")).toHaveValue("Mars/Base");
  });

  it("lets a curated timezone replace a detected fallback", async () => {
    const user = userEvent.setup();
    const schedule = { ...makeView().schedule, timezone_name: "Europe/Kiev" };
    const save = vi.fn().mockResolvedValue({ ok: true, schedule });
    render(<ScheduleSettings schedule={schedule} onSaved={vi.fn()} save={save} />);

    await user.click(screen.getByText("Confirm your working hours"));
    expect(screen.getByLabelText("Other IANA timezone")).toHaveValue("Europe/Kiev");
    await user.selectOptions(screen.getByLabelText("Timezone"), "Europe/Sofia");
    await user.click(screen.getByRole("button", { name: "Save schedule" }));

    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      timezone_name: "Europe/Sofia",
    }));
  });
});

describe("the editorial shelves and the one-click pin", () => {
  it("shows build outcomes with their required API and a first-version recipe", async () => {
    const user = userEvent.setup();
    const view = {
      ...makeView(),
      build_ideas: [
        { id: "idea-a", api: "claude", api_label: "Claude API",
          feature: "Web search tool",
          title: "A morning brief that cites its sources",
          hook: "One call where the model searches the web itself.",
          summary: "Declare the web_search server tool and the searching "
            + "happens on the vendor side.",
          steps: ["Add the tool entry.", "Limit it.", "Schedule it."],
          why: "Grounded answers over recalled ones.",
          url: "https://docs.claude.com/en/docs/x", source: "Claude docs" },
        { id: "idea-b", api: "openai", api_label: "OpenAI API",
          feature: "Structured Outputs",
          title: "Forms that fill themselves from messy text",
          hook: "The response matches your JSON schema exactly.",
          summary: "Define the record as a schema with strict mode on.",
          steps: ["Write the schema.", "Send the text."],
          why: "Extraction without its one failure mode.",
          url: "https://platform.openai.com/docs/y", source: "OpenAI docs" },
      ],
      build_repos: [
        { id: "repo-a", name: "owner/useful-tool",
          url: "https://github.com/owner/useful-tool",
          what: "Converts documents into clean Markdown for model input.",
          why: "Clean input is half of retrieval quality.",
          caveat: "Complex layouts lose structure - spot-check the output." },
      ],
      build_ideas_days: [],
    };
    view.build_ideas[0].repo = "https://github.com/owner/live-demo";
    render(<Dashboard view={view} refresh={vi.fn()} />);
    await openPage(user, "build");
    expect(screen.queryByText(/ideas in this edition/)).not.toBeInTheDocument();
    expect(screen.getByText("Web search tool · Claude API")).toBeVisible();
    expect(screen.getByText("Structured Outputs · OpenAI API")).toBeVisible();
    expect(screen.getByText("A morning brief that cites its sources"))
      .toBeVisible();
    // The recipe sits behind the fold with the official-docs link.
    expect(screen.getByText("Add the tool entry.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Claude docs/ }))
      .toHaveAttribute("href", "https://docs.claude.com/en/docs/x");
    // The hand-picked GitHub shelf sits under the ideas, caveat and all -
    // and an idea's receipt links the project that does it for real.
    expect(screen.getByText("Worth a look on GitHub")).toBeVisible();
    expect(screen.queryByText(/picked by hand, never by stars/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /owner\/useful-tool/ }))
      .toHaveAttribute("href", "https://github.com/owner/useful-tool");
    await user.click(screen.getByText("Requirements and limitations"));
    expect(screen.getByText(/spot-check the output/)).toBeVisible();
    expect(screen.getByRole("link", { name: /see it real: owner\/live-demo/ }))
      .toHaveAttribute("href", "https://github.com/owner/live-demo");
  });

  it("offers previous days on the news shelf and fetches the chosen one", async () => {
    const user = userEvent.setup();
    const archived = {
      channel: "news", day: "2026-07-11",
      items: [{ id: "old-item", kind: "post", title: "Yesterday's edition",
                hook: "An archived hook.", summary: "An archived summary.",
                why: "It was worth reading.", url: "https://example.org/a",
                source: "Example" }],
    };
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true, json: () => Promise.resolve(archived),
    });
    vi.stubGlobal("fetch", fetchSpy);
    try {
      // news_days carries PREVIOUS days only: the engine drops today's own
      // key, because the shelf is keyed by the UTC day and this page only
      // knows the local one (see test_feeds.py, 2026-08-24 review). The
      // page renders exactly what it is given, plus "today" for the live
      // feed.
      const view = { ...makeView(), news_days: ["2026-07-11", "2026-07-10"] };
      render(<Dashboard view={view} refresh={vi.fn()} />);
      await openPage(user, "reading");
      expect(screen.getByRole("button", { name: "Latest edition" })).toBeVisible();
      await user.click(screen.getByRole("button", { name: "2026-07-11" }));
      expect(await screen.findByText("Yesterday's edition")).toBeVisible();
      const called = String(fetchSpy.mock.calls.find(([url]) => String(url).includes("/api/feed-day?"))[0]);
      expect(called).toContain("/api/feed-day?");
      expect(called).toContain("channel=news");
      expect(called).toContain("day=2026-07-11");
      // Back to today restores the live feed without another fetch.
      await user.click(screen.getByRole("button", { name: "Latest edition" }));
      expect(screen.queryByText("Yesterday's edition")).not.toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("the one-click pin posts the ladder name and reports what happened", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true, outcome: "pinned",
        path: "C:/homes/claude/settings.json", backup: null }),
    });
    vi.stubGlobal("fetch", fetchSpy);
    try {
      const view = {
        ...makeView(),
        pin_projects: ["PracticeGraph"],
        model_catalog: [
          { tool: "claude_code", tool_label: "Claude Code",
            version: "catalog-pin-test",
            models: [
              { model: "Claude Sonnet", role: "everyday",
                when: "Daily coding.", price_note: "the middle rate",
                pin: "sonnet", pinned: false },
            ],
            efforts: [
              { level: "high", when: "The harness default.", tone: "neutral",
                recommended: true, pinned: true },
            ],
            pinned_model: null, pinned_effort: "high",
            practices: [], practices_source: "", switch: [], closing: "" },
        ],
      };
      render(<Dashboard view={view} refresh={vi.fn()} />);
      await openPage(user, "models");
      // Claude Code's everyday model is not pinned: the offer shows, and it
      // says what it will write before it writes anything.
      expect(fetchSpy.mock.calls.some(([url]) => String(url) === "/api/pin-model")).toBe(false);
      await user.click(screen.getByRole("button", {name: "Review change"}));
      const button = screen.getByRole("button",
        { name: "Make Claude Sonnet at high the start" });
      expect(screen.getAllByText(/keeps a backup/).length).toBeGreaterThan(0);
      await user.click(button);
      expect(await screen.findByText(/the file now carries the new start/))
        .toBeVisible();
      const [url, options] = fetchSpy.mock.calls.find(([url]) => String(url) === "/api/pin-model");
      expect(String(url)).toBe("/api/pin-model");
      expect(JSON.parse(options.body)).toEqual({
        tool: "claude_code", model: "Claude Sonnet", effort: "high",
        scope: "machine",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});


it("exposes schedule confirmation on first use in the actual app shell", async () => {
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue({...makeView(),
    app_version: "9.9.9", rate_card_version: "rates-test"});
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  try {
    render(<App />);
    expect(await screen.findByText("Confirm your working hours")).toBeVisible();
    expect(screen.queryByText(/2026-07-12 · Europe\/Sofia/)).not.toBeInTheDocument();
    expect(within(document.querySelector("footer")).getByText(/v9\.9\.9/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Save schedule"})).not.toBeVisible();
    await userEvent.setup().click(screen.getByText("Confirm your working hours"));
    expect(screen.getByRole("button", {name: "Save schedule"})).toBeVisible();
  } finally {
    viewRequest.mockRestore();
    presenceRequest.mockRestore();
  }
});

it("has no audience switch in the app shell", async () => {
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue(makeView());
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  try {
    render(<App />);
    await screen.findByText("Confirm your working hours");
    expect(screen.queryByLabelText("Recommendation profile")).not.toBeInTheDocument();
    expect(screen.queryByText(/Recommendations for/)).not.toBeInTheDocument();
  } finally {
    viewRequest.mockRestore();
    presenceRequest.mockRestore();
  }
});

it("keeps rendering when browser storage refuses the per-render seen markers", async () => {
  // 2026-09-16 field report: the installed window went blank after a few
  // minutes. Without a boundary, one thrown effect empties the whole root;
  // the seen-marker effect writes localStorage on every render, so a storage
  // failure must never be able to take the page down.
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue(makeView());
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("QuotaExceededError"); });
  const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("SecurityError"); });
  try {
    render(<App />);
    expect(await screen.findByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("navigation", {name: "Main navigation"})).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  } finally {
    setItem.mockRestore();
    getItem.mockRestore();
    viewRequest.mockRestore();
    presenceRequest.mockRestore();
  }
});

it("keeps an install notice to one line with its reasoning folded", async () => {
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue({...makeView(), install_notices: [
    {id: "stale", label: "This page may be out of date", line: "The last update was 61 minutes ago.", why: "PracticeGraph refreshes about every 15 minutes.", action: "Open PracticeGraph from the Start menu to refresh now."}]});
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  try {
    render(<App />);
    const notice = (await screen.findByText("This page may be out of date")).closest(".installnotice");
    expect(notice).toHaveClass("noticebar");
    expect(within(notice).getByText("The last update was 61 minutes ago.")).toBeVisible();
    expect(within(notice).getByText(/refreshes about every 15 minutes/).closest("details")).not.toHaveAttribute("open");
  } finally { viewRequest.mockRestore(); presenceRequest.mockRestore(); }
});

it("starts with the focus strip folded when no timer is running", async () => {
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue(makeView());
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  try {
    render(<App />);
    await screen.findByText("Confirm your working hours");
    expect(screen.getByText("Focus & breaks").closest("details")).not.toHaveAttribute("open");
  } finally { viewRequest.mockRestore(); presenceRequest.mockRestore(); }
});

it("draws each feature's share of calls as a bar", async () => {
  const view = {...makeView(), tools: {versions: [], projects: [], skills: [], connectors: [], features: [
    { tool: "claude_code", tool_label: "Claude Code", window_days: 30, sessions: 10, features: [
      { id: "web", label: "Web research", doc: "Searches.", count: 40 },
      { id: "files", label: "Files and shell", doc: "The core loop.", count: 10 }] }]}};
  const { container } = render(<Dashboard view={view} refresh={vi.fn()} />);
  await openPage(userEvent.setup(), "tools");
  const rows = [...container.querySelectorAll(".featrow")];
  expect(rows.length).toBeGreaterThan(0);
  const shares = rows.map(r => r.style.getPropertyValue("--share"));
  expect(shares.every(v => /^\d+(\.\d+)?%$/.test(v))).toBe(true);
  expect(shares).toContain("100%");
  expect(container.querySelector(".featrow .featbar")).not.toBeNull();
});

it("gives every navigation destination its own icon", async () => {
  render(<Dashboard view={makeView()} refresh={vi.fn()} />);
  const links = within(screen.getByRole("navigation", {name: "Main navigation"})).getAllByRole("link");
  expect(links.length).toBeGreaterThan(5);
  for (const link of links) expect(link.querySelector("svg.navicon")).not.toBeNull();
  expect(new Set(links.map(l => l.querySelector("svg.navicon path")?.getAttribute("d"))).size).toBe(links.length);
});

it("links to the public repository and carries the Team Landi mark", async () => {
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue(makeView());
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  render(<App />);
  const github = await screen.findByRole("link", {name: /GitHub/});
  viewRequest.mockRestore(); presenceRequest.mockRestore();
  expect(github).toHaveAttribute("href", "https://github.com/TeamLandiLTD/practicegraph-app");
  expect(github).toHaveAttribute("target", "_blank");
  const landi = within(document.querySelector("footer")).getByRole("link", {name: /Team Landi/});
  expect(landi).toHaveAttribute("href", "https://teamlandi.com");
  expect(landi.querySelector("img")).toHaveAttribute("alt", "Team Landi");
});


it("keeps Practice off the menu for now but reachable by address", async () => {
  const user = userEvent.setup();
  render(<Dashboard view={makeView()} refresh={vi.fn()} />);
  const nav = screen.getByRole("navigation", {name: "Main navigation"});
  expect(within(nav).queryByRole("link", {name: "Practice", exact: true})).not.toBeInTheDocument();
  expect(within(nav).getByRole("link", {name: "Work rhythm", exact: true})).toBeInTheDocument();
  await openPage(user, "practice");
  expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Practice");
  expect(screen.queryByText(/recorded separately on Practice/)).not.toBeInTheDocument();
});

it("points feedback at the public issue tracker instead of a form", async () => {
  const viewRequest = vi.spyOn(api, "fetchView").mockResolvedValue(makeView());
  const presenceRequest = vi.spyOn(api, "fetchPresence").mockResolvedValue({ working: false });
  render(<App />);
  await screen.findByText("Confirm your working hours");
  viewRequest.mockRestore(); presenceRequest.mockRestore();
  const footer = document.querySelector("footer");
  expect(within(footer).queryByText(/Send feedback/)).not.toBeInTheDocument();
  expect(within(footer).getByRole("link", {name: /Report an issue/})).toHaveAttribute("href", "https://github.com/TeamLandiLTD/practicegraph-app/issues");
});
