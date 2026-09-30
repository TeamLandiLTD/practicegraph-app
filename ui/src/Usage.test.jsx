import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { AllowanceCard, EconomyCard, GroupedUsage, UsageOverview } from "./Usage.jsx";
import { matchedBenchmarks, ModelsCard } from "./App.jsx";
import * as api from "./api.js";

afterEach(() => vi.restoreAllMocks());
const base = () => ({accounting_day_utc: "2026-09-05", billing: {default_mode: "subscription", by_tool: {}},
  totals: {cost_micro_usd: 25000000, assistant_turns: 100, sessions: 5, unpriced_turns: 2},
  ranges: [{range_id: "range-week", label: "Last 7 days", from_day: "2026-08-30", to_day: "2026-09-05",
    cost_micro_usd: 100000000, active_days: 4, tokens_total: 2000, assistant_turns: 600, unpriced_turns: 3,
    models: [{tool: "codex", model: "example-model", assistant_turns: 600, cost_micro_usd: 100000000}]}]});

it("labels subscription equivalence without an economy reading and scopes the period selector", async () => {
  render(<UsageOverview view={base()} />); const user = userEvent.setup();
  expect(screen.getByText("Estimated value at API prices")).toBeVisible();
  // The essential billing distinction stays visible beside the number.
  expect(screen.getByText("This is a price comparison, not a subscription charge.")).toBeVisible();
  expect(screen.queryByText(/Subscription amounts are comparisons/)).not.toBeInTheDocument();
  expect(screen.getByRole("tooltip")).toHaveTextContent(/a comparison, not a charge/);
  // Unpriced turns are named on the face only when they move the total (1% or more).
  expect(screen.getByText(/2 turns have no listed price/)).toBeVisible();
  expect(screen.queryByText(/spent|What the month cost you/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Last 7 days"}));
  expect(screen.getByText("$100")).toBeVisible();
  expect(screen.queryByText(/no listed price/)).not.toBeInTheDocument();
  await user.click(screen.getByText(/Usage details for last 7 days/));
  expect(screen.getByText(/600 assistant turns/)).toBeVisible();
  expect(screen.getByText("example-model")).toBeVisible();
});

it("keeps mixed billing separate by tool and only saves the changed choice", async () => {
  const save = vi.spyOn(api, "setBillingMode").mockResolvedValue(true);
  const refresh = vi.fn(); const v = base(); const user = userEvent.setup();
  v.billing.by_tool = {codex: "api", claude_code: "subscription"};
  render(<UsageOverview view={v} refresh={refresh} />);
  await user.click(screen.getByText("How model use is billed"));
  expect(screen.getByLabelText("Claude Code")).toHaveValue("subscription");
  expect(screen.getByLabelText("Codex")).toHaveValue("api");
  await user.selectOptions(screen.getByLabelText("Codex"), "mixed");
  expect(save).toHaveBeenCalledWith("mixed", "codex"); expect(refresh).toHaveBeenCalledOnce();
});

it("reports a failed save without pretending it succeeded", async () => {
  vi.spyOn(api, "setBillingMode").mockResolvedValue(false); const refresh = vi.fn();
  render(<UsageOverview view={base()} refresh={refresh} />); const user = userEvent.setup();
  await user.click(screen.getByText("How model use is billed"));
  await user.selectOptions(screen.getByLabelText("Codex"), "api");
  expect(screen.getByRole("alert")).toBeVisible(); expect(refresh).not.toHaveBeenCalled();
});

it("calls API amounts estimates and withholds missing allowance readings", () => {
  const v = base(); v.billing.default_mode = "api";
  render(<><UsageOverview view={v} /><AllowanceCard view={v} /></>);
  expect(screen.getByText("Estimated API usage cost")).toBeVisible();
  expect(screen.queryByText(/% used/)).not.toBeInTheDocument();
});

it("shows gauge provenance and age and drops the earlier peaks", () => {
  render(<AllowanceCard now={Date.parse("2026-09-05T13:00:00Z")}
    view={{runway: {buckets: [{bucket: "5h", label: "5-hour window", window_minutes: 300,
      source_id: "codex_cli", observed_at: "2026-09-05T08:00:00Z", latest_pct: 20, peak_pct: 95}]}}} />);
  expect(screen.getByText("Codex · 5-hour window")).toBeVisible();
  expect(screen.getByText(/20%/)).toHaveTextContent("used");
  expect(screen.getByText(/older reading/)).toBeVisible();
  expect(screen.queryByText(/95% used/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Earlier peaks/)).not.toBeInTheDocument();
  // What the log did not record is not printed as a fact.
  expect(screen.queryByText(/Reset time not recorded/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Account identity not recorded/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Snapshots from local logs/)).not.toBeInTheDocument();
});

it("retains unknown provenance for legacy gauges", () => {
  render(<AllowanceCard view={{runway: {buckets: [{bucket: "week", label: "Weekly window",
    latest_pct: 0, peak_pct: 0}]}}} />);
  expect(screen.getByText(/Source not recorded · Weekly/, {selector: "strong"})).toBeVisible();
  expect(screen.getByText(/observation time unknown/)).toBeVisible();
});

it("does not restore commit or completion KPIs in diagnostics", async () => {
  const v = {...base(), economy: {window_days: 28, cache_hit_pct: 75, cost_per_priced_turn_micro_usd: 25000,
    commit_cost: {per_commit_micro_usd: 999000000}}, work_units: {available: true, window_days: 28,
    units: 20, median_cost_micro_usd: 1000000, p90_cost_micro_usd: 10000000, landed_share_pct: 80,
    top: [{started_day: "2026-09-01", assistant_turns: 100, cost_micro_usd: 25000000, outcome: "commit"}]}};
  render(<><EconomyCard view={v} /><GroupedUsage view={v} /></>); const user = userEvent.setup();
  await user.click(screen.getByText(/Context, cache and reasoning/));
  await user.click(screen.getByText(/Grouped usage · experimental/));
  expect(screen.queryByText(/per commit|carried a commit|80%|no artifact/i)).not.toBeInTheDocument();
  expect(screen.getByText(/not confirmed tasks/)).toBeVisible();
});

const catalog = {tool: "codex", tool_label: "Codex", version: "edition-new", models: [
  {model: "example-2", role: "everyday", when: "Routine work.", price_note: "Listed rates."}],
  efforts: [{level: "high", when: "Complex work."}]};
const guidance = {tool: "codex", published_on: "2026-09-05", attribution: "Example benchmark",
  roles: [{id: "everyday", model: "example-1", effort: "high", index: 70, tokens: "1M"}]};

it("rejects a same-role benchmark for a different model", () => {
  expect(matchedBenchmarks(catalog, guidance)).toEqual([]);
});
it.each([{tool: "claude_code"}, {review_needed: true}, {published_on: ""}, {attribution: ""},
  {roles: [{model: "example-2", effort: "low", index: 70}]},
  {roles: [{model: "example-2", effort: "", index: 70}]}])("withholds unqualified evidence: %j", override => {
  expect(matchedBenchmarks(catalog, {...guidance,
    roles: [{...guidance.roles[0], model: "example-2"}], ...override})).toEqual([]);
});
it("deduplicates exact matches independently of role and displays the tested setup", async () => {
  const row = {...guidance.roles[0], model: "example-2", id: "strongest"};
  const g = {...guidance, roles: [row, {...row, id: "fast"}]};
  expect(matchedBenchmarks(catalog, g)).toEqual([row]);
  render(<ModelsCard view={{model_catalog: [catalog], ranges: [], model_guidance: [g]}} />);
  expect(screen.getByText("70")).not.toBeVisible();
  await userEvent.setup().click(screen.getByText("Published benchmark evidence"));
  expect(screen.getByText("70")).toBeVisible();
  expect(screen.getByRole("columnheader", {name: "Effort tested"})).toBeVisible();
});

it("keeps both billing choices usable while the view reloads", async () => {
  // The save takes milliseconds; the rebuilt view can take seconds on a long
  // history and minutes during a first scan. One shared busy flag once
  // disabled BOTH dropdowns for that whole reload: "clicking does nothing".
  const save = vi.spyOn(api, "setBillingMode").mockResolvedValue(true);
  const refresh = vi.fn(() => new Promise(() => {})); // a reload that never finishes
  const v = base(); v.billing.by_tool = {codex: "api", claude_code: "subscription"};
  render(<UsageOverview view={v} refresh={refresh} />); const user = userEvent.setup();
  await user.click(screen.getByText("How model use is billed"));
  await user.selectOptions(screen.getByLabelText("Claude Code"), "mixed");
  expect(screen.getByLabelText("Claude Code")).toHaveValue("mixed");
  expect(screen.getByLabelText("Claude Code")).toBeEnabled();
  expect(screen.getByLabelText("Codex")).toBeEnabled();
  await user.selectOptions(screen.getByLabelText("Codex"), "subscription");
  expect(screen.getByLabelText("Codex")).toHaveValue("subscription");
  await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  expect(save).toHaveBeenNthCalledWith(1, "mixed", "claude_code");
  expect(save).toHaveBeenNthCalledWith(2, "subscription", "codex");
});

it("saves billing choices one at a time so a quick second change cannot undo the first", async () => {
  // The server rewrites the whole per-tool map, so two saves in flight could
  // each write back the other's old value.
  const pending = [];
  const save = vi.spyOn(api, "setBillingMode").mockImplementation(
    () => new Promise(resolve => pending.push(resolve)));
  const v = base(); v.billing.by_tool = {codex: "api", claude_code: "subscription"};
  render(<UsageOverview view={v} refresh={vi.fn()} />); const user = userEvent.setup();
  await user.click(screen.getByText("How model use is billed"));
  await user.selectOptions(screen.getByLabelText("Claude Code"), "mixed");
  await user.selectOptions(screen.getByLabelText("Codex"), "subscription");
  await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  pending[0](true);
  await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  pending[1](true);
});

it("puts a billing choice back when its save fails", async () => {
  vi.spyOn(api, "setBillingMode").mockResolvedValue(false);
  const v = base(); v.billing.by_tool = {codex: "api"};
  render(<UsageOverview view={v} refresh={vi.fn()} />); const user = userEvent.setup();
  await user.click(screen.getByText("How model use is billed"));
  await user.selectOptions(screen.getByLabelText("Codex"), "mixed");
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(screen.getByLabelText("Codex")).toHaveValue("api");
});
