import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { PracticeHistory, practiceDuration, usePracticeTime } from "./PracticeHistory.jsx";
import * as api from "./api.js";

const base = () => ({enabled: true, skills: [
  {id: "ai-development", label: "AI-assisted development", confirmed_seconds: 453600,
    week_seconds: 12000, next_milestone_hours: 150, goal_hours: null},
  {id: "verification", label: "Reviewing results", confirmed_seconds: 0,
    week_seconds: 0, next_milestone_hours: 1, goal_hours: null}],
  active: null, history: [], pending: [], suggestions: [], week_start: "2026-08-31", timezone: "Europe/Sofia"});
function show(data = base()) {
  const run = vi.fn().mockResolvedValue(true);
  const controller = {data, run, busy: false, error: "", reload: vi.fn(), setError: vi.fn()};
  const rendered = render(<PracticeHistory controller={controller} />);
  return {...rendered, controller, run};
}
afterEach(() => {vi.restoreAllMocks(); vi.useRealTimers();});

it("offers an optional history without inventing prior practice", async () => {
  const {run} = show({...base(), enabled: false});
  expect(screen.queryByText(/126h/)).not.toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", {name: "Enable practice history"}));
  expect(run).toHaveBeenCalledWith({action: "enable", enabled: true});
});
it("shows reviewed totals with scope and no mastery percentage", () => {
  show();
  expect(screen.getByText(/126h 0m/)).toHaveTextContent("confirmed practice");
  expect(screen.getByText("3h 20m this week")).toBeVisible();
  expect(screen.queryByText(/Next milestone/)).not.toBeInTheDocument();
  // The week's start and the mastery caveat leave the face for the ⓘ.
  expect(screen.queryByText(/Week beginning/, {ignore: ".infotip"})).not.toBeInTheDocument();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.getByText(/not a level of mastery/, {selector: ".infotip"})).toHaveTextContent(/2026-08-31/);
  expect(screen.queryByText(/not a level of mastery/, {ignore: ".infotip"})).not.toBeInTheDocument();
  expect(screen.queryByText(/Private practice record/i)).not.toBeInTheDocument();
});
it("starts a session for the selected skill", async () => {
  const {run} = show(); const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("Skill"), "verification");
  await user.click(screen.getByRole("button", {name: "Start practice session"}));
  expect(run).toHaveBeenCalledWith({action: "start", skill: "verification"});
});
it("keeps a recovered paused session explicit and does not auto-resume", async () => {
  const active = {id: "entry", skill: "ai-development", seconds: 600, running: false, last_tick: 0};
  const {run} = show({...base(), active});
  expect(screen.getByRole("timer")).toHaveTextContent("10m · paused");
  expect(run).not.toHaveBeenCalled();
  await userEvent.setup().click(screen.getByRole("button", {name: "Resume practice"}));
  expect(run).toHaveBeenCalledWith({action: "resume", id: "entry"});
});
it("requires a review and permits a shorter duration with reflection", async () => {
  const entry = {id: "entry", skill: "ai-development", seconds: 1800, day: "2026-09-05", reflection: ""};
  const {run} = show({...base(), pending: [entry]}); const user = userEvent.setup();
  expect(run).not.toHaveBeenCalled();
  await user.clear(screen.getByLabelText("Minutes to count"));
  await user.type(screen.getByLabelText("Minutes to count"), "20");
  await user.selectOptions(screen.getByLabelText("What did you take away?"), "learned");
  await user.click(screen.getByRole("button", {name: "Count as practice"}));
  expect(run).toHaveBeenCalledWith({action: "confirm", id: "entry", skill: "ai-development",
    seconds: 1200, reflection: "learned"});
});
it("keeps activity estimates folded and separate from confirmed totals", async () => {
  const entry = {id: "observed-2026-09-05", seconds: 1800, day: "2026-09-05"};
  const {run} = show({...base(), suggestions: [entry]}); const user = userEvent.setup();
  expect(screen.getByText(/estimated human activity/)).not.toBeVisible();
  await user.click(screen.getByText("Review recent activity estimates"));
  expect(screen.getByText(/estimated human activity/)).toBeVisible();
  expect(screen.queryByText(/do not establish practice or learning/)).not.toBeInTheDocument();
  expect(screen.queryByText(/do not establish practice or learning/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name: "Skip", hidden: false}));
  expect(run).toHaveBeenCalledWith({action: "dismiss", id: entry.id});
});
it("keeps the hours goal optional without a pre-filled aspiration", async () => {
  const {run} = show(); const user = userEvent.setup();
  await user.click(screen.getByText("Set a personal hours goal"));
  expect(screen.queryByRole("button", {name: "Choose 10,000 hours"})).not.toBeInTheDocument();
  expect(screen.queryByText(/not a guarantee of expertise/)).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Hours for AI-assisted development"), "10000");
  await user.click(screen.getByRole("button", {name: "Save goal"}));
  expect(run).toHaveBeenCalledWith({action: "goal", skill: "ai-development", hours: 10000});
});
it("preserves an unsaved goal when the background reading refreshes", async () => {
  const {run, controller, rerender} = show(); const user = userEvent.setup();
  await user.click(screen.getByText("Set a personal hours goal"));
  await user.type(screen.getByLabelText("Hours for AI-assisted development"), "250");
  rerender(<PracticeHistory controller={{...controller, data: base()}} />);
  expect(screen.getByLabelText("Hours for AI-assisted development")).toHaveValue(250);
  await user.click(screen.getByRole("button", {name: "Save goal"}));
  expect(run).toHaveBeenCalledWith({action: "goal", skill: "ai-development", hours: 250});
});
it("keeps the displayed estimate aligned with new activity while preserving an edited amount", async () => {
  const entry = {id: "observed-2026-09-05", seconds: 1800, day: "2026-09-05"};
  const {run, controller, rerender} = show({...base(), suggestions: [entry]});
  const user = userEvent.setup();
  await user.click(screen.getByText("Review recent activity estimates"));
  rerender(<PracticeHistory controller={{...controller, data: {...base(),
    suggestions: [{...entry, seconds: 2400}]}}} />);
  expect(screen.getByLabelText("Minutes to count")).toHaveValue(40);
  await user.clear(screen.getByLabelText("Minutes to count"));
  await user.type(screen.getByLabelText("Minutes to count"), "20");
  rerender(<PracticeHistory controller={{...controller, data: {...base(),
    suggestions: [{...entry, seconds: 3000}]}}} />);
  expect(screen.getByLabelText("Minutes to count")).toHaveValue(20);
  await user.click(screen.getByRole("button", {name: "Count as practice"}));
  expect(run).toHaveBeenCalledWith({action: "confirm", id: entry.id,
    skill: "ai-development", seconds: 1200, reflection: ""});
});
it("previews a backup before explicitly restoring it", async () => {
  const {run} = show(); const user = userEvent.setup();
  await user.click(screen.getByText("Back up and manage this record"));
  expect(screen.queryByText(/never included in shared aggregates/)).not.toBeInTheDocument();
  const backup = {schema: "practicegraph.practice-time/1", entries: [{id: "example"}], goals: {}};
  const file = new File([JSON.stringify(backup)], "practice.json", {type: "application/json"});
  file.text = async () => JSON.stringify(backup);
  await user.upload(screen.getByLabelText("Restore a backup"), file);
  expect(screen.getByText("1 session records ready for validation.")).toBeVisible();
  expect(run).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", {name: "Restore history"}));
  expect(run).toHaveBeenCalledWith({action: "restore", backup});
  expect(await screen.findByRole("status")).toHaveTextContent("History restored.");
});
it("rejects an invalid backup without changing the record", async () => {
  const {run, controller} = show(); const user = userEvent.setup();
  await user.click(screen.getByText("Back up and manage this record"));
  const file = new File(["bad JSON"], "practice.json", {type: "application/json"});
  file.text = async () => "bad JSON";
  await user.upload(screen.getByLabelText("Restore a backup"), file);
  expect(controller.setError).toHaveBeenLastCalledWith("Choose a valid practice-history JSON backup under 16 MB.");
  expect(screen.queryByRole("button", {name: "Restore history"})).not.toBeInTheDocument();
  expect(run).not.toHaveBeenCalled();
});
it("requires confirmation before deleting the durable record", async () => {
  const {run} = show(); vi.spyOn(window, "confirm").mockReturnValue(false);
  const user = userEvent.setup(); await user.click(screen.getByText("Back up and manage this record"));
  await user.click(screen.getByRole("button", {name: "Clear practice-time history"}));
  expect(run).not.toHaveBeenCalled();
  window.confirm.mockReturnValue(true);
  await user.click(screen.getByRole("button", {name: "Clear practice-time history"}));
  expect(run).toHaveBeenCalledWith({action: "clear", confirm: true});
});
it("retains paused session state after a save failure", () => {
  const {controller, rerender} = show();
  rerender(<PracticeHistory controller={{...controller, error: "That did not save. Try again."}} />);
  expect(screen.getByRole("alert")).toHaveTextContent("That did not save");
  expect(within(screen.getByRole("alert")).getByRole("button", {name: "Reload history"})).toBeVisible();
});
it("keeps the heartbeat outside the selected page and clears it on unmount", async () => {
  vi.useFakeTimers();
  const data = {...base(), active: {id: "active", running: true, seconds: 0, last_tick: 0}};
  vi.spyOn(api, "fetchPracticeTime").mockResolvedValue(data);
  vi.spyOn(api, "changePracticeTime").mockResolvedValue(data);
  function App() {usePracticeTime(); return <div>Another page</div>;}
  const page = render(<App />);
  await act(async () => {await vi.advanceTimersByTimeAsync(30000);});
  expect(api.changePracticeTime).toHaveBeenCalledWith({action: "heartbeat", id: "active"});
  page.unmount();
  await act(async () => {await vi.advanceTimersByTimeAsync(60000);});
  expect(api.changePracticeTime).toHaveBeenCalledTimes(1);
});
it("keeps positive sub-minute time visible", () => {
  expect(practiceDuration(1)).toBe("less than 1m");
  expect(practiceDuration(3601)).toBe("1h 0m");
});
