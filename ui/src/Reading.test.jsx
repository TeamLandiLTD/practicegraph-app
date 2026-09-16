import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Reading, readingStep } from "./Reading.jsx";
import { Dashboard } from "./App.jsx";
import * as api from "./api.js";

const view = () => ({day: "2026-09-06", local_day: "2026-09-06", totals: {cost_micro_usd: 0, assistant_turns: 0, sessions: 0, unpriced_turns: 0},
  observation: {id: "synthetic-observation", title: "A synthetic work pattern", body: "Checks appeared in two recorded changes.",
    period: "last 28 days", confidence: "limited evidence", caveat: "Recorded checks do not establish a passing result."},
  capability: {paths: ["software"], paths_confirmed: true, history: []},
  news: [{id: "news-one", title: "First selected headline", hook: "A sourced development.", source: "Synthetic publisher"},
    {id: "news-two", title: "Second selected headline", hook: "The rest stays in the edition."}],
  community: [{id: "community-one", title: "A practitioner finding", hook: "A bounded observation.", source: "Synthetic forum", observed: "2026-09-05"}],
  build_ideas: [{id: "build-one", api: "codex", title: "An idea to try", hook: "Start with a small verified example.", steps: ["Try a check."]}],
  feed_status: {news: {version: "synthetic", state: "date_unknown"},
    community: {version: "synthetic", edition_date: "2026-09-05", state: "current"},
    "build-ideas": {version: "synthetic-old", edition_date: "2026-07-01", state: "stale"}},
});
const learning = () => ({schema: "practicegraph.learning/1", prefs: {goal: "", tool: "any", free_only: true, max_minutes: null},
  goals: {}, tools: {any: "Either tool"}, feed: {}, records: [], suggestion: null, alternatives: []});
beforeEach(() => {
  window.history.replaceState(null, "", "#"); localStorage.clear();
  vi.spyOn(api, "fetchPracticeTime").mockResolvedValue({skills: [], enabled: false, active: null, history: [], pending: []});
  vi.spyOn(api, "fetchSetupImprovements").mockResolvedValue({schema: "practicegraph.setup-improvements/1", active: null, history: [], catalog: {}});
  vi.spyOn(api, "fetchTraining").mockResolvedValue(learning());
});
afterEach(() => {vi.restoreAllMocks();});

it("keeps the reading bounded and retains the qualifications and edition status", () => {
  render(<Reading view={view()} />);
  // On its own, Reading carries the personal side and the build pointer; the
  // news and community editions arrive from the dashboard as `editions`.
  expect(screen.queryByText("First selected headline")).not.toBeInTheDocument();
  expect(screen.queryByText("A practitioner finding")).not.toBeInTheDocument();
  expect(screen.getByText("last 28 days · limited evidence")).toBeVisible();
  // The caveat stays, behind the ⓘ rather than on the face.
  expect(screen.getByRole("tooltip")).toHaveTextContent("Recorded checks do not establish a passing result.");
  expect(screen.queryByText(/Recorded checks do not establish/, {ignore: ".infotip"})).not.toBeInTheDocument();
  expect(screen.queryByText(/That is the overview/)).not.toBeInTheDocument();
  expect(screen.queryByText(/A small general selection/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", {name: /See your usage/})).toHaveAttribute("href", "#spend");
  expect(screen.queryByRole("link", {name: /Explore the build ideas/})).not.toBeInTheDocument();
});

it("keeps News focused and gives Community its own destination", () => {
  window.history.replaceState(null, "", "#reading");
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(screen.getByRole("button", {name: /First selected headline/})).toBeInTheDocument();
  expect(screen.getByRole("button", {name: /Second selected headline/})).toBeInTheDocument();
  expect(screen.queryByText("A practitioner finding")).not.toBeInTheDocument();
  expect(screen.queryByText("An idea to try")).not.toBeInTheDocument();
  const nav = within(screen.getByRole("navigation", {name: "Main navigation"}));
  expect(nav.getByRole("link", {name: "News"})).toBeInTheDocument();
  expect(nav.getByRole("link", {name: "Community"})).toBeInTheDocument();
});

it.each(["spend", "models", "tools", "skills", "connectors", "practice", "mindfulness", "reading", "build", "docs"])(
  "opens #%s with one plain line saying what the section is", (id) => {
  window.history.replaceState(null, "", `#${id}`);
  render(<Dashboard view={view()} refresh={vi.fn()} />);
    const intro = screen.getByRole("main").querySelector(".section-intro");
    if (id === "spend") {expect(screen.getByRole("heading", {name: "Usage"})).toBeVisible(); return;}
  expect(intro).not.toBeNull();
  expect(intro.textContent.trim().length).toBeGreaterThan(20);
});

it("withholds an incomplete observation instead of dropping its uncertainty", () => {
  const v = view(); delete v.observation.caveat;
  render(<Reading view={v} />);
  expect(screen.queryByText(v.observation.body)).not.toBeInTheDocument();
  expect(screen.getByText(/not enough supported evidence/)).toBeVisible();
});

it("continues a selected course before proposing a new practice", () => {
  const v = view(); v.capability.opportunity = {title: "A new practice", why: "New suggestion."};
  render(<Reading view={v} learning={{records: [{state: "saved", availability: "stale", entry: {title: "Chosen course"}}]}} />);
  expect(screen.getByText("Chosen course")).toBeVisible();
  expect(screen.getByText(/saved guidance is stale/)).toBeVisible();
  expect(screen.queryByText("A new practice")).not.toBeInTheDocument();
  expect(screen.getByRole("link", {name: /Continue your learning step/})).toHaveAttribute("href", "#practice");
});

it("does not propose a new action while existing choices are still unknown", () => {
  const v = view(); v.capability.opportunity = {title: "Wait for the records", why: "A new suggestion."};
  render(<Reading view={v} recordsReady={false} recordsError />);
  expect(screen.queryByText("Wait for the records")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("could not be loaded");
  expect(screen.queryByText(/No next step selected/)).not.toBeInTheDocument();
});

it("routes an existing timer to the practice page and no longer knows setup records", () => {
  expect(readingStep(view(), {practiceTime: {active: {running: true}}})).toMatchObject({href: "#practice"});
  expect(readingStep(view(), {setup: {active: {label: "A project"}}})).toBeNull();
});

it("opens on Usage with every category directly accessible in one menu", async () => {
  const user = userEvent.setup();
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  const nav = screen.getByRole("navigation", {name: "Main navigation"});
  expect(within(nav).getAllByRole("link").map(a => a.textContent)).toEqual([
    "Usage", "Models", "API Prices", "Tools", "Agent skills", "Integrations",
    "Work rhythm", "News", "Community", "Build ideas", "Guides",
  ]);
  expect(screen.getAllByRole("navigation")).toHaveLength(1);
  expect(within(nav).getByRole("link", {name: "Usage"})).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Usage");
  await user.click(within(nav).getByRole("link", {name: "News"}));
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "News"));
  expect(screen.getByRole("heading", {name: "News"})).toBeVisible();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/Checking your private records/)).not.toBeInTheDocument());
});

it.each([
  ["models", "Models"], ["tools", "Tools"], ["connectors", "Integrations"], ["skills", "Agent skills"],
  ["docs", "Guides"], ["mindfulness", "Work rhythm"],
  ["build", "Build ideas"], ["spend", "Usage"], ["reading", "News"],
  ["usage", "Usage"], ["toolkit", "Models"], ["unknown", "Usage"],
  // Retired addresses land on their new home instead of a dead end (2026-09-11).
  ["news", "News"], ["community", "Community"], ["api-prices", "API Prices"], ["projects", "Usage"],
])("preserves the #%s destination and selects its visible menu link", async (hash, page) => {
  window.history.replaceState(null, "", `#${hash}`);
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(within(screen.getByRole("navigation", {name: "Main navigation"})).getByRole("link", {name: page, exact: true})).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("main")).toHaveAttribute("aria-label", page);
  await waitFor(() => expect(api.fetchTraining).toHaveBeenCalledOnce());
});

it.each([["practice"], ["history"], ["learning"], ["advice"], ["setup"]])("keeps the #%s address on Practice without a menu entry (off the menu 2026-09-16)", (hash) => {
  window.history.replaceState(null, "", `#${hash}`);
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Practice");
  expect(within(screen.getByRole("navigation", {name: "Main navigation"})).queryByRole("link", {name: "Practice", exact: true})).not.toBeInTheDocument();
});

it("keeps the #baseline address without a menu entry", () => {
  window.history.replaceState(null, "", "#baseline");
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Baseline");
  expect(within(screen.getByRole("navigation", {name: "Main navigation"}))
    .queryByRole("link", {name: "Baseline"})).not.toBeInTheDocument();
});

it("separates the API market from Models and keeps focus controls as the reader left them", async () => {
  const prices = vi.spyOn(api, "fetchTokenPrices").mockResolvedValue({edition: null, changes: [], offer_states: {}});
  const user = userEvent.setup();
  window.history.replaceState(null, "", "#models");
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(prices).not.toHaveBeenCalled();
  expect(screen.queryByRole("group", {name: "Model view"})).not.toBeInTheDocument();
  expect(screen.queryByRole("region", {name: "Token prices"})).not.toBeInTheDocument();
  // Folded on arrival; once opened it stays as the reader left it across navigation.
  expect(screen.getByText("Focus & breaks").closest("details")).not.toHaveAttribute("open");
  await user.click(screen.getByText("Focus & breaks"));
  expect(screen.getByText("Focus & breaks").closest("details")).toHaveAttribute("open");
  await user.click(within(screen.getByRole("navigation", {name: "Main navigation"})).getByRole("link", {name: "API Prices"}));
  expect(await screen.findByRole("region", {name: "Token prices"})).toBeVisible();
  expect(prices).toHaveBeenCalledOnce();
  expect(screen.getByText("Focus & breaks").closest("details")).toHaveAttribute("open");
});

it("opens Community without embedding news or a build promotion", async () => {
  window.history.replaceState(null, "", "#community");
  render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(screen.getByText("A practitioner finding")).toBeVisible();
  expect(screen.queryByText("First selected headline")).not.toBeInTheDocument();
  expect(screen.queryByText("An idea to try")).not.toBeInTheDocument();
});

it("opens categories in one click and follows browser back and forward", async () => {
  const user = userEvent.setup(); render(<Dashboard view={view()} refresh={vi.fn()} />);
  const mainNav = within(screen.getByRole("navigation", {name: "Main navigation"}));
  await user.click(mainNav.getByRole("link", {name: "Tools"}));
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Tools"));
  await user.click(mainNav.getByRole("link", {name: "Integrations"}));
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Integrations"));
  await user.click(mainNav.getByRole("link", {name: "Usage"}));
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Usage"));
  act(() => window.history.back());
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Integrations"));
  act(() => window.history.back());
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Tools"));
  act(() => window.history.forward());
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Integrations"));
  await user.click(mainNav.getByRole("link", {name: "Usage"}));
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Usage"));
});

it("keeps the timer controller mounted and offers a way back from another destination", async () => {
  const user = userEvent.setup();
  window.history.replaceState(null, "", "#reading");
  vi.mocked(api.fetchPracticeTime).mockResolvedValue({skills: [], active: {id: "synthetic", running: true}, history: []});
  const {rerender} = render(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(await screen.findByRole("link", {name: "Return to the timer"})).toBeVisible();
  await user.click(within(screen.getByRole("navigation", {name: "Main navigation"})).getByRole("link", {name: "Tools"}));
  await waitFor(() => expect(screen.getByRole("main")).toHaveAttribute("aria-label", "Tools"));
  rerender(<Dashboard view={view()} refresh={vi.fn()} />);
  expect(api.fetchPracticeTime).toHaveBeenCalledOnce();
  expect(screen.getByRole("link", {name: "Return to the timer"})).toHaveAttribute("href", "#practice");
});
