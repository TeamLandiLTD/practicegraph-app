import React from "react";
import {render, screen, within} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {beforeEach, expect, it, vi} from "vitest";
import TokenPrices, {modelRows} from "./TokenPrices.jsx";
import {fetchTokenPrices, refreshTokenPrices} from "./api.js";
import research from "../../tests/fixtures/token-prices-research.json";

vi.mock("./api.js", () => ({fetchTokenPrices: vi.fn(), refreshTokenPrices: vi.fn()}));
function fixture() {
  const edition = {...structuredClone(research), history: []};
  const first = edition.offers[0];
  const second = structuredClone(first); second.id = "second"; second.provider = "groq";
  second.pricing.input = "0"; second.pricing.output = "0";
  edition.offers.push(second);
  return {edition, changes: [], offer_states: {[first.id]: "current", second: "stale"}};
}
beforeEach(() => {vi.clearAllMocks(); fetchTokenPrices.mockResolvedValue(fixture());});

it("fetches a validated hosted edition and retains the cache after a failed download", async () => {
  const user = userEvent.setup();
  refreshTokenPrices.mockRejectedValueOnce(new Error("offline"));
  render(<TokenPrices />);
  await screen.findByText(/2 offers shown/);
  await user.click(screen.getByRole("button", {name: "Check for a newer edition"}));
  expect(refreshTokenPrices).toHaveBeenCalledOnce();
  expect(screen.getByRole("status")).toHaveTextContent("last accepted edition is preserved");
  expect(screen.getByText(/2 offers shown/)).toBeInTheDocument();
});

it("shows one row per model with a headline price and expands to the hosts", async () => {
  const user = userEvent.setup(); render(<TokenPrices />);
  await screen.findByText(/1 model · 2 offers shown/);
  const table = screen.getByRole("table", {name: /API prices by model/});
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(within(table).getByText("Example Model")).toBeInTheDocument();
  expect(within(table).getByText(/from Groq/)).toBeInTheDocument();
  expect(screen.queryByText("Terms, sources & history")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: /2 hosts/}));
  expect(screen.getAllByText("Terms, sources & history")).toHaveLength(2);
  expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$0").length).toBeGreaterThan(0);
  await user.click(screen.getAllByText("Terms, sources & history")[0]);
  expect(screen.getAllByText(/First observation/).length).toBeGreaterThan(0);
  expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", research.sources[0].url);
});

it("filters provider and active quotes without presenting stale quotes as current", async () => {
  const user = userEvent.setup(); render(<TokenPrices />);
  await screen.findByText(/2 offers shown/);
  await user.selectOptions(screen.getByLabelText("Provider"), "groq");
  expect(screen.getByText(/1 model · 1 offer shown/)).toBeInTheDocument();
  await user.click(screen.getByLabelText("Recently verified, active offers only"));
  expect(screen.getByText(/No models match/)).toBeInTheDocument();
});

it("groups offers under their model, sorts models by headline price, and hides unpriced offers", () => {
  const data = fixture();
  const other = structuredClone(data.edition.models[0]); other.id = "other"; other.name = "Other Model"; other.developer = "Other";
  data.edition.models.push(other);
  data.edition.offers.push({...structuredClone(data.edition.offers[0]), id: "other-offer", model_id: "other"});
  data.edition.offers.at(-1).pricing.input = "0.001"; data.edition.offers.at(-1).pricing.output = "5";
  data.edition.offers[1].pricing.input = "0.002"; data.edition.offers[1].pricing.output = "0.0001";
  data.edition.offers.push({...structuredClone(data.edition.offers[0]), id: "unknown"});
  data.edition.offers.at(-1).pricing.input = null;
  const rows = modelRows(data.edition, data.offer_states, {sort: "input"});
  expect(rows.map(r => r.model.id)).toEqual(["other", "example-model-r1"]);
  expect(rows[1].offers).toHaveLength(2);
  expect(rows[1].summary.headline.id).toBe("second");
  expect(rows[1].offers.map(o => o.id)).toEqual(["second", "example-host-model-r1-standard"]);
  expect(modelRows(data.edition, data.offer_states, {sort: "output"}).map(r => r.model.id)).toEqual(["example-model-r1", "other"]);
  expect(modelRows(data.edition, data.offer_states, {sort: "model"}).map(r => r.model.id)).toEqual(["example-model-r1", "other"]);
  expect(modelRows(data.edition, data.offer_states, {sort: "model", provider: "groq"}).map(r => r.model.id)).toEqual(["example-model-r1"]);
});

it("shows price changes and changed conditions", async () => {
  const data = fixture(); const o = data.edition.offers[0];
  data.changes = [{offer_id: o.id, rates: {input: {before: "2", after: o.pricing.input}}, conditions_changed: true}];
  data.edition.history = [structuredClone(research)];
  fetchTokenPrices.mockResolvedValue(data);
  render(<TokenPrices />);
  await userEvent.click(await screen.findByRole("button", {name: /2 hosts/}));
  expect(screen.getByText("Previously $2")).toBeInTheDocument();
  await userEvent.click(screen.getAllByText("Terms, sources & history")[0]);
  expect(screen.getByText(/Billing conditions also changed/)).toBeInTheDocument();
});

it("recovers from errors and shows an empty catalog", async () => {
  fetchTokenPrices.mockRejectedValueOnce(new Error("offline"));
  const user = userEvent.setup(); render(<TokenPrices />);
  const alert = await screen.findByRole("alert");
  fetchTokenPrices.mockResolvedValue({edition: null, changes: [], offer_states: {}});
  await user.click(within(alert).getByRole("button", {name: "Retry"}));
  expect(await screen.findByText(/No verified price edition/)).toBeInTheDocument();
});



it("omits the input range when every host charges the same", async () => {
  const data = fixture(); data.edition.offers[1].pricing.input = data.edition.offers[0].pricing.input;
  fetchTokenPrices.mockResolvedValue(data);
  render(<TokenPrices />);
  const button = await screen.findByRole("button", {name: /2 hosts/});
  expect(button.parentElement).toHaveTextContent(/^2 hosts2 offers$/);
});

it("marks every model row with its developer monogram", async () => {
  render(<TokenPrices />);
  await screen.findByText(/1 model · 2 offers shown/);
  const badge = screen.getByRole("table", {name: /API prices by model/}).querySelector(".dev-badge");
  expect(badge).toHaveTextContent("ED");
  expect(badge.className).toMatch(/tone-\d/);
});

it("offers a compact density that persists for the reader", async () => {
  localStorage.removeItem("pg-density");
  const user = userEvent.setup(); const {unmount} = render(<TokenPrices />);
  await screen.findByText(/1 model · 2 offers shown/);
  const table = screen.getByRole("table", {name: /API prices by model/});
  expect(table).not.toHaveClass("compact");
  await user.click(screen.getByLabelText("Compact rows"));
  expect(table).toHaveClass("compact");
  expect(localStorage.getItem("pg-density")).toBe("compact");
  unmount(); render(<TokenPrices />);
  expect(await screen.findByRole("table", {name: /API prices by model/})).toHaveClass("compact");
});

it("no longer offers a workload comparison", async () => {
  render(<TokenPrices />);
  await screen.findByText(/1 model · 2 offers shown/);
  expect(screen.queryByRole("button", {name: "Compare a workload"})).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "Provider prices"})).not.toBeInTheDocument();
  expect(screen.queryByRole("region", {name: "Model recommendations"})).not.toBeInTheDocument();
  expect(screen.getByText(/Provider directory/)).toBeInTheDocument();
});

it("flags a model that a direct host sells for less than its developer", async () => {
  const data = fixture();
  data.edition.models[0].developer = "Anthropic";
  data.edition.offers[0].provider = "anthropic"; data.edition.offers[0].pricing.input = "3"; data.edition.offers[0].pricing.output = "15";
  data.edition.offers[1].provider = "groq"; data.edition.offers[1].pricing.input = "1.5"; data.edition.offers[1].pricing.output = "7.5";
  fetchTokenPrices.mockResolvedValue(data);
  render(<TokenPrices />);
  const table = await screen.findByRole("table", {name: /API prices by model/});
  const row = within(table).getByText("Example Model").closest("tr");
  expect(within(row).getByText(/cheaper at Groq/)).toBeInTheDocument();
  expect(within(row).getAllByText(/50% less/).length).toBeGreaterThan(0);
  expect(within(row).getByText("$3")).toBeInTheDocument();
});

it("shows the lowest direct price beside the developer's and can sort by it", async () => {
  const data = fixture();
  data.edition.models[0].developer = "Anthropic";
  data.edition.offers[0].provider = "anthropic"; data.edition.offers[0].pricing.input = "3"; data.edition.offers[0].pricing.output = "15";
  data.edition.offers[1].provider = "groq"; data.edition.offers[1].pricing.input = "1.5"; data.edition.offers[1].pricing.output = "7.5";
  data.offer_states = {[data.edition.offers[0].id]: "current", second: "current"};
  fetchTokenPrices.mockResolvedValue(data);
  const user = userEvent.setup(); render(<TokenPrices />);
  const table = await screen.findByRole("table", {name: /API prices by model/});
  const row = within(table).getByText("Example Model").closest("tr");
  const lowest = within(row).getByTestId("lowest");
  expect(lowest).toHaveTextContent("$1.5");
  expect(lowest).toHaveTextContent("$7.5");
  expect(lowest).toHaveTextContent("Groq");
  expect(lowest).toHaveTextContent("50% less");
  expect(within(screen.getByLabelText("Sort by")).getByRole("option", {name: "Lowest output price"})).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("Sort by"), "lowest");
  expect(screen.getByRole("table", {name: /API prices by model/})).toBeInTheDocument();
});

it("lists deals the edition proves and names their conditions", async () => {
  const data = fixture();
  data.edition.models[0].developer = "Anthropic";
  data.edition.offers[0].provider = "anthropic"; data.edition.offers[0].pricing.input = "2"; data.edition.offers[0].pricing.output = "10";
  data.edition.offers[0].effective_to = new Date(Date.now() + 40 * 86400000).toISOString().slice(0, 19) + "Z";
  data.edition.offers[1].provider = "anthropic"; data.edition.offers[1].service_tier = "batch"; data.edition.offers[1].pricing.input = "1"; data.edition.offers[1].pricing.output = "5";
  const host = structuredClone(data.edition.offers[0]); host.id = "host"; host.provider = "deepinfra"; host.effective_to = null; host.pricing.input = "1.6"; host.pricing.output = "8";
  data.edition.offers.push(host);
  data.offer_states = {[data.edition.offers[0].id]: "current", second: "current", host: "current"};
  fetchTokenPrices.mockResolvedValue(data);
  render(<TokenPrices />);
  const deals = await screen.findByRole("region", {name: "Deals right now"});
  expect(within(deals).getByText(/DeepInfra/)).toBeInTheDocument();
  expect(within(deals).getByText(/20% less/)).toBeInTheDocument();
  expect(within(deals).getByText(/asynchronous batch tier/)).toBeInTheDocument();
  expect(within(deals).getByText(/40 days/)).toBeInTheDocument();
});

it("says so when an edition proves no deals", async () => {
  render(<TokenPrices />);
  await screen.findByText(/1 model · 2 offers shown/);
  expect(screen.queryByRole("region", {name: "Deals right now"})).not.toBeInTheDocument();
});
