import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api.js";
import { LearningPaths, LearningResource } from "./LearningPaths.jsx";

vi.mock("./api.js", () => ({fetchTraining: vi.fn(), changeTraining: vi.fn()}));
const entry = {
  id: "synthetic", title: "A synthetic learning lab", provider: "Synthetic provider",
  url: "https://example.org/course", summary: "Review a generated result.", why: "You chose verification.",
  credential: "completion_badge", cost: "free", cost_note: "No fee for this synthetic resource.",
  duration_minutes: 45, duration_basis: "provider", prerequisites: "A synthetic project.",
  limitations: "This is a test fixture.", reviewed_on: "2026-09-06",
  sources: ["https://example.org/evidence"], steps: [{title: "Try a check", url: "https://example.org/check", outcome: "Explain what was checked."}],
};
const base = () => ({
  schema: "practicegraph.learning/1", prefs: {goal: "", tool: "any", free_only: true, max_minutes: null},
  goals: {verify_results: "Verify AI-generated work", certification: "Explore a professional certification"},
  tools: {any: "Either tool", codex: "Codex", claude_code: "Claude Code"},
  feed: {version: "synthetic-2026-09-06", state: "current", edition_date: "2026-09-06"},
  suggestion: null, alternatives: [], records: [],
});
beforeEach(() => {vi.clearAllMocks(); api.fetchTraining.mockResolvedValue(base());});
afterEach(() => {vi.useRealTimers(); vi.restoreAllMocks();});

it.each([
  ["no_coverage", /This edition has no current resources for your chosen goal/],
  ["catalog_unavailable", /A current training selection is not available/],
  ["already_recorded", /matching resources are already in your learning record/],
])("explains %s without blaming the user's filters", async (reason, message) => {
  const result = base(); result.prefs.goal = "certification"; result.empty_reason = reason;
  api.fetchTraining.mockResolvedValue(result);
  render(<LearningPaths />); await userEvent.setup().click(screen.getByText("Learning paths · optional"));
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.queryByText(/Try another tool or a broader cost/)).not.toBeInTheDocument();
});

it("lets a reader review and choose a set-aside snapshot again", async () => {
  const result = base(); const user = userEvent.setup();
  result.records = [{entry, state: "dismissed", availability: "changed", updated_on: "2026-09-06"}];
  api.fetchTraining.mockResolvedValue(result);
  api.changeTraining.mockResolvedValue({...result, records: [{...result.records[0], state: "saved"}]});
  render(<LearningPaths />); await user.click(screen.getByText("Learning paths · optional"));
  await user.click(await screen.findByText("Your learning record"));
  await user.click(screen.getByText(/A synthetic learning lab · Set aside/));
  expect(screen.getByText(/saved guidance is changed/)).toBeVisible();
  await user.click(screen.getByRole("button", {name: "Choose again"}));
  expect(api.changeTraining).toHaveBeenCalledWith({action: "resume", id: entry.id});
  expect(await screen.findByText("Learning paths · Saved")).toBeVisible();
});

it("explains why returning to a set-aside item is disabled with another active step", async () => {
  const result = base(); const user = userEvent.setup();
  result.records = [{entry, state: "dismissed", availability: "current", updated_on: "2026-09-06"},
    {entry: {...entry, id: "second", title: "Another lab"}, state: "saved", availability: "current"}];
  api.fetchTraining.mockResolvedValue(result);
  render(<LearningPaths />); await user.click(await screen.findByText("Learning paths · Saved"));
  await user.click(screen.getByText("Your learning record"));
  await user.click(screen.getByText(/A synthetic learning lab · Set aside/));
  expect(screen.getByRole("button", {name: "Choose again"})).toBeDisabled();
  expect(screen.getByText(/Finish or set aside your selected step/)).toBeVisible();
});

it("starts quietly and saves explicit goals without inferring a deficit", async () => {
  const user = userEvent.setup(); render(<LearningPaths />);
  expect(screen.getByText("Learning paths · optional").closest("details")).not.toHaveAttribute("open");
  await user.click(screen.getByText("Learning paths · optional"));
  await screen.findByLabelText("Learning goal");
  expect(screen.getByLabelText("Free resources only")).toBeChecked();
  expect(screen.queryByText(entry.title)).not.toBeInTheDocument();
  const result = base(); result.prefs.goal = "verify_results"; result.suggestion = entry;
  api.changeTraining.mockResolvedValue(result);
  await user.selectOptions(screen.getByLabelText("Learning goal"), "verify_results");
  await user.click(screen.getByRole("button", {name: "Save learning preferences"}));
  expect(api.changeTraining).toHaveBeenCalledWith({action: "preferences", ...result.prefs});
  await screen.findByText(entry.title);
  expect(screen.getByText(/adds no practice hours/, {selector: ".infotip"})).toBeInTheDocument();
});
it("opening a provider link does not save or complete the item", async () => {
  const user = userEvent.setup(); const result = base(); result.suggestion = entry;
  api.fetchTraining.mockResolvedValue(result);
  render(<LearningPaths />); await user.click(screen.getByText("Learning paths · optional"));
  const link = await screen.findByRole("link", {name: "View at provider"});
  expect(link).toHaveAttribute("href", entry.url); expect(link).toHaveAttribute("rel", "noreferrer");
  await user.click(link); expect(api.changeTraining).not.toHaveBeenCalled();
});
it("presents saved snapshots with their availability and explicit progress", async () => {
  const user = userEvent.setup(); const result = base();
  result.records = [{entry, state: "saved", availability: "stale", updated_on: "2026-09-06"}];
  api.fetchTraining.mockResolvedValue(result); api.changeTraining.mockResolvedValue(result);
  render(<LearningPaths />);
  await waitFor(() => expect(screen.getByText("Learning paths · Saved")).toBeInTheDocument());
  await user.click(screen.getByText("Learning paths · Saved"));
  expect(screen.getByText(/saved guidance is stale/)).toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "I started this"}));
  expect(api.changeTraining).toHaveBeenCalledWith({action: "progress", id: entry.id, state: "in_progress"});
});
it("keeps unknowns honest and never renders unsafe links", () => {
  render(<LearningResource entry={{...entry, title: "<script>not executable</script>", url: "javascript:alert(1)", duration_minutes: null, cost: "unknown"}} />);
  expect(screen.getByText("<script>not executable</script>")).toBeInTheDocument();
  expect(screen.getByText(/Time commitment unverified/)).toBeInTheDocument();
  expect(screen.getByText(/Cost unverified/)).toBeInTheDocument();
  expect(screen.queryByRole("link", {name: "View at provider"})).not.toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
});
it("does not clear learning without an explicit confirmation", async () => {
  const user = userEvent.setup(); const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  api.changeTraining.mockResolvedValue(base());
  render(<LearningPaths />); await user.click(screen.getByText("Learning paths · optional"));
  await user.click(await screen.findByText("Your learning record"));
  await user.click(screen.getByRole("button", {name: "Clear learning record"}));
  expect(api.changeTraining).not.toHaveBeenCalled(); confirm.mockReturnValue(true);
  await user.click(screen.getByRole("button", {name: "Clear learning record"}));
  expect(api.changeTraining).toHaveBeenCalledWith({action: "clear", confirm: true});
});
it("keeps a late poll from overwriting a saved choice", async () => {
  const user = userEvent.setup(); let resolveLate;
  let poll;
  const originalInterval = globalThis.setInterval;
  vi.spyOn(globalThis, "setInterval").mockImplementation((callback, delay, ...args) => {
    if (delay === 60000) {poll = callback; return 123;}
    return originalInterval(callback, delay, ...args);
  });
  const result = base(); result.suggestion = entry;
  api.fetchTraining.mockResolvedValueOnce(result).mockImplementationOnce(() => new Promise(resolve => {resolveLate = resolve;}));
  const chosen = {...result, suggestion: null, records: [{entry, state: "saved", availability: "current"}]};
  api.changeTraining.mockResolvedValue(chosen);
  render(<LearningPaths />); await user.click(screen.getByText("Learning paths · optional"));
  await screen.findByText(entry.title);
  act(() => {void poll();});
  await user.click(screen.getByRole("button", {name: "Choose this next step"}));
  await screen.findByText("Learning paths · Saved");
  await act(async () => resolveLate(base()));
  expect(screen.getByText("Learning paths · Saved")).toBeInTheDocument();
});

it("keeps the method and privacy notes off the face", async () => {
  render(<LearningPaths standalone />);
  await screen.findByRole("button", {name: "Save learning preferences"});
  expect(screen.getByText(/never read as a skill gap/, {selector: ".infotip"})).toBeInTheDocument();
  expect(screen.queryByText(/never read as a skill gap/, {ignore: ".infotip"})).not.toBeInTheDocument();
  expect(screen.queryByText(/self-reported and stays on this device/)).not.toBeInTheDocument();
  expect(screen.getByText("Your learning record")).toBeInTheDocument();
});
