import React from "react";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { UsageExplorer, pricedAmount } from "./UsageExplorer.jsx";
import { AllowanceCard } from "./Usage.jsx";
import * as api from "./api.js";

afterEach(() => vi.restoreAllMocks());

it("distinguishes free, partly priced and unknown totals", () => {
  expect(pricedAmount({assistant_turns: 3, unpriced_turns: 3, cost_micro_usd: 0})).toBe("Price unknown");
  expect(pricedAmount({assistant_turns: 3, unpriced_turns: 1, cost_micro_usd: 2000000})).toBe("$2.00 + unpriced");
  expect(pricedAmount({assistant_turns: 3, unpriced_turns: 0, cost_micro_usd: 0})).toBe("$0.00");
});
const view = {billing: {default_mode: "subscription", by_tool: {}}};
const response = (period = "7d", overrides = {}) => ({period, page: 0, page_size: 30,
  from_day: "2026-09-01", to_day: "2026-09-07", summary: {cost_micro_usd: 5000000,
    tokens_total: 10000, assistant_turns: 20, unpriced_turns: 0, sessions: 1, models: []},
  drivers: {prompt_tokens: 8000, output_tokens: 2000, cache_pct: 50, edited_sessions: 1, without_test_attempt: 1},
  series: [{day: "2026-09-07", cost_micro_usd: 5000000, assistant_turns: 20}],
  sessions: [{id: "opaque-session", tool: "codex", first_ts: "2026-09-07T08:00:00Z",
    last_ts: "2026-09-07T09:00:00Z", assistant_turns: 20, tool_calls: 10, test_run_attempts: 0,
    cost_micro_usd: 5000000}], families: [], unresolved_forks: 0, ...overrides});

it("uses one period for totals, trends and sessions, and ignores a late request", async () => {
  let resolveOld;
  const fetch = vi.spyOn(api, "fetchUsage").mockImplementation(period => period === "7d"
    ? new Promise(resolve => {resolveOld = resolve;}) : Promise.resolve(response(period, {
      summary: {...response().summary, cost_micro_usd: 12000000}})));
  render(<UsageExplorer view={view} />);
  await userEvent.setup().click(screen.getByRole("button", {name: "Last 30 days"}));
  expect(await screen.findByText("$12")).toBeVisible();
  await act(async () => resolveOld(response()));
  expect(screen.queryByText("$5")).not.toBeInTheDocument();
  expect(fetch.mock.calls.at(-1)[0]).toBe("30d");
  // A one-day series is shown as today against yesterday, not as one bar.
  expect(screen.getByRole("group", {name: "Today against yesterday"})).toHaveTextContent("2026-09-07");
});

it("omits session investigation and family inspection from Usage", async () => {
  const fetch = vi.spyOn(api, "fetchUsage").mockResolvedValue(response());
  render(<UsageExplorer view={view} />);
  await screen.findByText("$5.00", {selector: ".usage-total"});
  expect(screen.queryByText("Investigate a session")).not.toBeInTheDocument();
  expect(screen.queryByText("Recorded session families")).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledOnce();
});

it("shows failures and retries without fabricating zero usage", async () => {
  vi.spyOn(api, "fetchUsage").mockRejectedValueOnce(new Error("offline")).mockResolvedValue(response());
  render(<UsageExplorer view={view} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Usage could not be loaded");
  expect(screen.queryByText("$0")).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", {name: "Try again"}));
  expect(await screen.findByText("$5.00", {selector: ".usage-total"})).toBeVisible();
});

it("leaves an active setup improvement to the Improve setup page", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response());
  render(<UsageExplorer view={view} setup={{active: {state: "attempted", availability: "ready",
    entry: {title: "Verification guidance", revision: 2, reviewed_on: "2026-09-05", sources: ["https://example.org/source"]},
    comparison: {state: "observed", before_count: 3, before_verified: 0, after_count: 3, after_verified: 2}}}} />);
  expect(await screen.findByText("$5.00", {selector: ".usage-total"})).toBeVisible();
  expect(screen.queryByText(/Continue your setup improvement/)).not.toBeInTheDocument();
  expect(screen.queryByRole("link", {name: "Source 1"})).not.toBeInTheDocument();
});

it("explains the total against the reader's own previous period, in words", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response("7d", {previous: {from_day: "2026-08-25",
    to_day: "2026-08-31", cost_micro_usd: 3000000}}));
  render(<UsageExplorer view={view} />);
  expect(await screen.findByText("up from $3.00 the week before")).toBeVisible();
  // The captions that narrated the controls are gone; the controls speak for themselves.
  for (const gone of [/This period applies/, /Listed-rate estimate by/, /Select a bar/, /Previous period/,
    /Sorted by listed-rate/, /Activity counts do not measure/, /Subscription amounts are comparisons/]) {
    expect(screen.queryByText(gone)).not.toBeInTheDocument();
  }
  expect(screen.getByText("By model")).toBeInTheDocument();
});

it("puts the usage total above the allowance readings and folds the session table", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response());
  const {container} = render(<UsageExplorer view={{...view, runway: {buckets: [{bucket: "5h", label: "5-hour window",
    window_minutes: 300, source_id: "codex_cli", observed_at: "2026-09-07T08:00:00Z", latest_pct: 20, peak_pct: 95}]}}} />);
  await screen.findByText("$5.00", {selector: ".usage-total"});
  const overview = container.querySelector(".usage-overview"), allowance = container.querySelector(".allowance");
  expect(overview.compareDocumentPosition(allowance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByText(/Earlier peaks/)).not.toBeInTheDocument();
  expect(screen.queryByText("Investigate a session")).not.toBeInTheDocument();
});

it("does not present a passed reset as fresh remaining allowance", () => {
  render(<AllowanceCard now={Date.parse("2026-09-07T12:00:00Z")} view={{runway: {buckets: [{
    source_id: "codex_cli", label: "Weekly window", observed_at: "2026-09-07T08:00:00Z",
    resets_at: "2026-09-07T10:00:00Z", latest_pct: 90, peak_pct: 90,
    account_hash: "abc1230000", pool_hash: "def4560000"}]}}} />);
  expect(screen.getByText(/reset passed/)).toBeVisible();
  expect(screen.getByText(/account abc123/)).toBeVisible();
  expect(screen.queryByText(/Allowance pool/)).not.toBeInTheDocument();
});

it("draws gridlines with axis labels and shows the hovered day in a tooltip", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response("7d", {series: [
    {day: "2026-09-06", cost_micro_usd: 2000000, assistant_turns: 4},
    {day: "2026-09-07", cost_micro_usd: 5000000, assistant_turns: 20}]}));
  render(<UsageExplorer view={view} />);
  const chart = await screen.findByRole("group", {name: "Usage trend"});
  const plot = within(chart.closest(".chart-plot"));
  expect(chart.closest(".usage-trend").querySelectorAll(".chart-gridline")).toHaveLength(3);
  expect(plot.queryByRole("tooltip")).not.toBeInTheDocument();
  await userEvent.setup().hover(screen.getByRole("button", {name: /2026-09-06:/}));
  expect(plot.getByRole("tooltip")).toHaveTextContent("2026-09-06 · $2.00 · 4 assistant turns");
});

it("draws the previous period as ghost bars, ticks every day, and labels the peak", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response("7d", {
    series: [{day: "2026-09-06", cost_micro_usd: 2000000, assistant_turns: 4}, {day: "2026-09-07", cost_micro_usd: 5000000, assistant_turns: 20}],
    previous: {cost_micro_usd: 9000000, from_day: "2026-09-04", to_day: "2026-09-05",
      series: [{day: "2026-09-04", cost_micro_usd: 8000000, assistant_turns: 4}, {day: "2026-09-05", cost_micro_usd: 1000000, assistant_turns: 4}]}}));
  render(<UsageExplorer view={view} />);
  const chart = await screen.findByRole("group", {name: "Usage trend"});
  const plot = chart.closest(".chart-plot");
  expect(plot.querySelectorAll(".usage-ghost")).toHaveLength(2);
  // The scale belongs to this period; a taller previous day is capped and marked.
  expect(plot.querySelectorAll(".usage-ghost.over")).toHaveLength(1);
  expect(plot.querySelector(".usage-ghost.over").style.getPropertyValue("--bar-height")).toBe("100%");
  expect(plot.querySelector(".chart-gridline-top span")).toHaveTextContent("$5.00");
  expect(plot.querySelectorAll(".chart-ticks span")).toHaveLength(2);
  expect(plot.querySelector(".usage-bar.peak")).toHaveAccessibleName(/2026-09-07/);
  expect(plot.querySelector(".peak-label")).toHaveTextContent("$5.00");
  expect(screen.getByText("down from $9.00 the week before")).toHaveClass("usage-delta", "down");
});

it("pins a clicked day so its reading stays after the pointer leaves", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response("7d", {series: [
    {day: "2026-09-06", cost_micro_usd: 2000000, assistant_turns: 4},
    {day: "2026-09-07", cost_micro_usd: 5000000, assistant_turns: 20}]}));
  render(<UsageExplorer view={view} />);
  const chart = await screen.findByRole("group", {name: "Usage trend"});
  const plot = within(chart.closest(".chart-plot"));
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", {name: /2026-09-06:/}));
  await user.unhover(screen.getByRole("button", {name: /2026-09-06:/}));
  expect(plot.getByRole("tooltip")).toHaveTextContent("2026-09-06");
  await user.click(screen.getByRole("button", {name: /2026-09-06:/}));
  await user.unhover(screen.getByRole("button", {name: /2026-09-06:/}));
  expect(plot.queryByRole("tooltip")).not.toBeInTheDocument();
});

it("draws a single day as today against yesterday instead of one bar", async () => {
  vi.spyOn(api, "fetchUsage").mockResolvedValue(response("today", {
    from_day: "2026-09-15", to_day: "2026-09-15",
    series: [{day: "2026-09-15", cost_micro_usd: 64960000, assistant_turns: 20}],
    previous: {cost_micro_usd: 90000000, from_day: "2026-09-14", to_day: "2026-09-14",
      series: [{day: "2026-09-14", cost_micro_usd: 90000000, assistant_turns: 30}]}}));
  render(<UsageExplorer view={view} />);
  const strip = await screen.findByRole("group", {name: "Today against yesterday"});
  expect(screen.queryByRole("group", {name: "Usage trend"})).not.toBeInTheDocument();
  const rows = strip.querySelectorAll(".day-row");
  expect(rows).toHaveLength(2);
  expect(rows[0]).toHaveTextContent("Today");
  expect(rows[0]).toHaveTextContent("$64.96");
  expect(rows[1]).toHaveTextContent("Yesterday");
  expect(rows[1]).toHaveTextContent("$90.00");
  // Both bars share one scale: yesterday is the larger, so it is the full width.
  expect(rows[1].querySelector(".day-bar").style.getPropertyValue("--bar-width")).toBe("100%");
  expect(rows[0].querySelector(".day-bar").style.getPropertyValue("--bar-width")).toBe("72.2%");
});

it("labels and converts the chart in the display currency", async () => {
  api.setDisplayCurrency({code: "GBP", factor: "0.5", prefix: "£", digits: 2, date: "2026-09-29"});
  try {
    vi.spyOn(api, "fetchUsage").mockResolvedValue(response("7d", {series: [
      {day: "2026-09-06", cost_micro_usd: 2000000, assistant_turns: 4},
      {day: "2026-09-07", cost_micro_usd: 5000000, assistant_turns: 20}]}));
    render(<UsageExplorer view={view} />);
    const chart = await screen.findByRole("group", {name: "Usage trend"});
    expect(chart.closest(".usage-trend")).toHaveTextContent("GBP at listed rates");
    expect(screen.getByRole("button", {name: "2026-09-06: £1.00 at listed rates"})).toBeInTheDocument();
  } finally {
    api.setDisplayCurrency(null);
  }
});
