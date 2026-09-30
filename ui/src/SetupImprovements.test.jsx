import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { SetupImprovements, useSetupImprovements } from "./SetupImprovements.jsx";
import * as api from "./api.js";

const base = () => ({schema: "practicegraph.setup-improvements/1", active: null, history: [],
  verification_gaps: 1, coach_active: false, catalog: {state: "current", source_label: "Synthetic catalog", edition_date: "2026-09-05"}});
const record = () => ({id: "a".repeat(32), label: "Website verification", tool: "codex", created_at: 1788609600,
  state: "inspected", availability: "ready", baseline: [{tests: 0}], attempted_at: null, feedback: null,
  inspection: {stacks: ["javascript"], declared_checks: ["test"], pytest_config: false,
    verification_mentioned: false, files: [{name: "package.json", state: "read"}]},
  entry: null, brief: null, comparison: {state: "not_attempted", before_count: 1,
    after_count: 0, before_verified: 0, after_verified: 0}});
const prepared = () => ({...record(), state: "prepared", brief: "Exact project-specific brief.",
  entry: {revision: 1, reviewed_on: "2026-09-05", limitations: "Attempts do not prove success.",
    sources: ["https://example.com/synthetic-source"]}});
function show(data = base()) {
  const run = vi.fn().mockResolvedValue(base());
  const controller = {data, busy: false, error: "", reload: vi.fn(), setError: vi.fn(), run};
  return {...render(<SetupImprovements controller={controller} />), controller, run};
}
afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers();});

it("requires a named, explicitly selected local project before inspection", async () => {
  const {run} = show(); const user = userEvent.setup();
  expect(run).not.toHaveBeenCalled();
  await user.click(screen.getByText("Inspect a project"));
  // What the inspection reads and keeps rides the ⓘ; the face says it runs nothing.
  expect(screen.getByText(/Scripts are not executed/, {selector: ".infotip"})).toBeInTheDocument();
  expect(screen.queryByText(/Up to 128 KB per file/, {ignore: ".infotip"})).not.toBeInTheDocument();
  expect(screen.queryByText(/A change to your working environment/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/Latest 50 records shown/)).not.toBeInTheDocument();
  expect(screen.queryByText(/excluded from shared aggregates/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Curated by|Synthetic catalog ·/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Inspect selected project"})).toBeDisabled();
  await user.type(screen.getByLabelText("Name this check"), "Website verification");
  await user.type(screen.getByLabelText("Project folder"), "C:\\work\\website");
  await user.click(screen.getByRole("button", {name: "Inspect selected project"}));
  expect(run).toHaveBeenCalledWith({action: "inspect", id: expect.stringMatching(/^[a-f0-9]{32}$/),
    path: "C:\\work\\website", tool: "codex", label: "Website verification"});
  expect(screen.getByLabelText("Project folder")).toHaveValue("");
});
it("does not invent a recommendation when there is no compatible edition", () => {
  show({...base(), active: {...record(), availability: "no_compatible_playbook"}});
  expect(screen.getByRole("button", {name: "Prepare verification brief"})).toBeDisabled();
  expect(screen.getByText(/No current playbook matches/)).toBeVisible();
});
it("keeps already adequate setup explicit without another improvement task", () => {
  show({...base(), active: {...record(), availability: "no_verification_gap"}});
  expect(screen.getByText(/No supported verification gap was found/)).toBeVisible();
  expect(screen.getByRole("button", {name: "Prepare verification brief"})).toBeDisabled();
});
it("requires explicit preparation and presents the inspection evidence", async () => {
  const {run} = show({...base(), active: record()}); const user = userEvent.setup();
  expect(run).not.toHaveBeenCalled();
  await user.click(screen.getByText("Inspection evidence and limits"));
  expect(screen.getByText("package.json: read")).toBeVisible();
  await user.click(screen.getByRole("button", {name: "Prepare verification brief"}));
  expect(run).toHaveBeenCalledWith({action: "prepare", id: record().id});
});
it("shows the exact brief and copying never marks it tried", async () => {
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  const {run} = show({...base(), active: prepared()});
  run.mockResolvedValue({brief: "Current server-checked brief."});
  expect(screen.getByText("Exact project-specific brief.")).toBeVisible();
  await user.click(screen.getByRole("button", {name: "Copy task"}));
  expect(run).toHaveBeenCalledTimes(1);
  expect(run).toHaveBeenCalledWith({action: "handoff", id: record().id});
  expect(copy).toHaveBeenCalledWith("Current server-checked brief.");
  expect(screen.getByText(/Copying does not mark it tried/)).toBeVisible();
});
it("refuses copying withdrawn guidance while preserving the historical brief", () => {
  show({...base(), active: {...prepared(), availability: "withdrawn"}});
  expect(screen.getByRole("button", {name: "Copy task"})).toBeDisabled();
  expect(screen.getByText("Exact project-specific brief.")).toBeVisible();
  expect(screen.getByText(/withdrawn or removed/)).toBeVisible();
});
it("marks actual use separately before allowing a helpfulness review", async () => {
  const {run} = show({...base(), active: prepared()});
  expect(screen.queryByRole("button", {name: "Helpful"})).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", {name: "I tried this improvement"}));
  expect(run).toHaveBeenCalledWith({action: "attempt", id: record().id});
});
it("keeps a dismissed brief readable without offering another handoff", async () => {
  show({...base(), history: [{...prepared(), state: "dismissed"}]});
  await userEvent.setup().click(screen.getByText("Your setup playbook"));
  await userEvent.setup().click(screen.getByText(/Website verification.*dismissed/));
  expect(screen.getByRole("button", {name: "Copy task"})).toBeDisabled();
  expect(screen.getByRole("button", {name: "Export brief"})).toBeDisabled();
});
it("keeps feedback separate from a still incomplete comparison", async () => {
  const r = {...prepared(), state: "attempted", attempted_at: 1788609600,
    comparison: {...record().comparison, state: "waiting", after_count: 1, after_verified: 1}};
  const {run} = show({...base(), active: r});
  expect(screen.getByText(/After: verification attempts visible in 1 of 1/)).toBeVisible();
  await userEvent.setup().click(screen.getByRole("button", {name: "Helpful"}));
  expect(run).toHaveBeenCalledWith({action: "review", id: r.id, feedback: "helpful"});
});
it("does not present an empty baseline as zero successful checks", () => {
  show({...base(), active: {...prepared(), baseline: [],
    comparison: {...record().comparison, state: "context_unknown", before_count: 0}}});
  expect(screen.getByText("No matching earlier changes were available.")).toBeVisible();
  expect(screen.queryByText(/Before:.*0 of 0/)).not.toBeInTheDocument();
});
it("prevents a competing setup practice while the coach has an active practice", async () => {
  show({...base(), coach_active: true}); const user = userEvent.setup();
  await user.click(screen.getByText("Inspect a project"));
  await user.type(screen.getByLabelText("Name this check"), "Website");
  await user.type(screen.getByLabelText("Project folder"), "C:\\website");
  expect(screen.getByRole("button", {name: "Inspect selected project"})).toBeDisabled();
});
it("previews a backup and waits for restore before changing records", async () => {
  const {run} = show(); const user = userEvent.setup();
  await user.click(screen.getByText("Back up and manage setup records"));
  const backup = {schema: base().schema, records: [record()]};
  const file = new File([JSON.stringify(backup)], "setup.json", {type: "application/json"});
  file.text = async () => JSON.stringify(backup);
  await user.upload(screen.getByLabelText("Restore setup backup"), file);
  expect(screen.getByText("1 improvement records ready for validation.")).toBeVisible();
  expect(run).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", {name: "Restore setup history"}));
  expect(run).toHaveBeenCalledWith({action: "restore", backup});
});
it("requires explicit confirmation to clear setup history", async () => {
  const {run} = show(); const user = userEvent.setup();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await user.click(screen.getByText("Back up and manage setup records"));
  await user.click(screen.getByRole("button", {name: "Clear setup records"}));
  expect(run).not.toHaveBeenCalled(); confirm.mockReturnValue(true);
  await user.click(screen.getByRole("button", {name: "Clear setup records"}));
  expect(run).toHaveBeenCalledWith({action: "clear", confirm: true});
});
it("polls only while the page is selected and cleans up on unmount", async () => {
  vi.useFakeTimers();
  vi.spyOn(api, "fetchSetupImprovements").mockResolvedValue(base());
  function Page({enabled}) {useSetupImprovements(enabled); return null;}
  const page = render(<Page enabled={false} />);
  await act(async () => {await vi.advanceTimersByTimeAsync(30000);});
  expect(api.fetchSetupImprovements).not.toHaveBeenCalled();
  page.rerender(<Page enabled />);
  await act(async () => {await vi.advanceTimersByTimeAsync(30000);});
  expect(api.fetchSetupImprovements).toHaveBeenCalledTimes(2);
  page.unmount();
  await act(async () => {await vi.advanceTimersByTimeAsync(30000);});
  expect(api.fetchSetupImprovements).toHaveBeenCalledTimes(2);
});
