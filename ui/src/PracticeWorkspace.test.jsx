import React from "react";
import {render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {beforeEach, afterEach, expect, it, vi} from "vitest";
import {PracticeWorkspace, currentPractice} from "./PracticeWorkspace.jsx";

beforeEach(() => {localStorage.clear(); sessionStorage.clear();});
afterEach(() => vi.restoreAllMocks());

it("starts with a choice and prefills a build draft without auto-saving it", async () => {
  const first = render(<PracticeWorkspace />);
  expect(screen.queryByLabelText("What will you try?")).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: "Plan my own practice"})).toBeVisible();
  first.unmount();
  sessionStorage.setItem("pg-build-practice", JSON.stringify({title: "A build idea", task: "Make a small version"}));
  render(<PracticeWorkspace />);
  expect(screen.getByLabelText("What will you try?")).toHaveValue("A build idea");
  expect(screen.getByLabelText("On which task?")).toHaveValue("Make a small version");
  expect(localStorage.getItem("pg-practice-brief-v1:personal")).toBeNull();
});

it("queues a build idea alongside an existing saved plan", () => {
  localStorage.setItem("pg-practice-brief-v1:personal", JSON.stringify({notes: {practice: "Existing plan", task: "Original task", check: "Test", result: "", finished: false}, history: []}));
  sessionStorage.setItem("pg-build-practice", JSON.stringify({title: "Next idea", task: "Another task"}));
  render(<PracticeWorkspace />);
  expect(screen.getByLabelText("On which task?")).toHaveValue("Original task");
  expect(screen.getByText(/Build idea saved for your next practice/)).toBeVisible();
});

it("continues an existing timer before coaching or saved learning", () => {
  const view = {capability: {practice_progress: {practice_id: "p", title: "Check a change"}}};
  const time = {active: {id: "t", skill: "review", running: true}, skills: [{id: "review", label: "Review"}]};
  const learning = {records: [{state: "saved", availability: "current", entry: {id: "l", title: "Course"}}]};
  expect(currentPractice(view, time, learning)).toMatchObject({key: "timer:t", panel: "hours", title: "Review"});
  expect(currentPractice(view, null, learning)).toMatchObject({key: "coach:p", panel: "coach"});
  expect(currentPractice({}, null, learning)).toMatchObject({key: "learning:l", panel: "learning"});
  expect(currentPractice({}, null, {records: []})).toBeNull();
});

it("waits for existing records before offering a new practice", () => {
  const {rerender} = render(<PracticeWorkspace ready={false} />);
  expect(screen.queryByLabelText("What will you try?")).not.toBeInTheDocument();
  rerender(<PracticeWorkspace ready={false} error />);
  expect(screen.getByRole("status")).toHaveTextContent("could not be loaded");
});

it("saves, restores and reviews a practice without losing the previous notes", async () => {
  const user = userEvent.setup();
  const {unmount} = render(<PracticeWorkspace />);
  await user.click(screen.getByRole("button", {name: "Plan my own practice"}));
  await user.type(screen.getByLabelText("What will you try?"), "Write a test first");
  await user.type(screen.getByLabelText("On which task?"), "Fix the parser");
  await user.type(screen.getByLabelText("How will you check the result?"), "Run the failing case");
  await user.click(screen.getByRole("button", {name: "Save practice notes"}));
  expect(screen.getByRole("status")).toHaveTextContent("saved");
  unmount();
  render(<PracticeWorkspace />);
  expect(screen.getByLabelText("On which task?")).toHaveValue("Fix the parser");
  await user.click(screen.getByText("Review the result", {selector: "summary"}));
  expect(screen.getByRole("button", {name: "Finish this practice"})).toBeDisabled();
  await user.type(screen.getByLabelText("What happened when you tried it?"), "The regression now passes");
  await user.click(screen.getByRole("button", {name: "Finish this practice"}));
  expect(screen.getByText("Practice reviewed")).toBeVisible();
  await user.click(screen.getByRole("button", {name: "Plan another practice"}));
  expect(screen.getByLabelText("What will you try?")).toHaveValue("");
  await user.click(screen.getByText("Previous practice notes"));
  await user.click(screen.getByText(/Write a test first ·/));
  expect(screen.getByText("The regression now passes")).toBeVisible();
});

it("keeps supporting controls mounted while changing the open panel", async () => {
  const user = userEvent.setup();
  render(<PracticeWorkspace current={{key: "timer:t", panel: "hours", title: "Review", body: "Timer running"}}
    hours={<input aria-label="Timer note" defaultValue="Keep me" />} learning={<p>Course choices</p>} />);
  expect(screen.getByRole("textbox", {name: "Timer note"})).toBeVisible();
  await user.click(screen.getByText("Learning resources"));
  await waitFor(() => expect(screen.getByText("Course choices")).toBeVisible());
  expect(screen.getByLabelText("Timer note")).not.toBeVisible();
  await user.click(screen.getByRole("button", {name: "Continue current practice"}));
  await waitFor(() => expect(screen.getByLabelText("Timer note")).toBeVisible());
  expect(screen.getByLabelText("Timer note")).toHaveValue("Keep me");
});

it("does not overwrite malformed stored history", () => {
  const bad = JSON.stringify({notes: {practice: "", task: "", check: "", result: "", finished: false}, history: [null]});
  localStorage.setItem("pg-practice-brief-v1:personal", bad);
  render(<PracticeWorkspace />);
  expect(screen.getByRole("alert")).toHaveTextContent("could not be read");
  expect(screen.queryByLabelText("What will you try?")).not.toBeInTheDocument();
  expect(localStorage.getItem("pg-practice-brief-v1:personal")).toBe(bad);
});

it("retains typed notes when storage fails and allows retry", async () => {
  const user = userEvent.setup();
  render(<PracticeWorkspace />);
  await user.click(screen.getByRole("button", {name: "Plan my own practice"}));
  await user.type(screen.getByLabelText("What will you try?"), "Test first");
  await user.type(screen.getByLabelText("On which task?"), "Parser");
  await user.type(screen.getByLabelText("How will you check the result?"), "Regression");
  const write = vi.spyOn(Storage.prototype, "setItem").mockImplementationOnce(() => {throw new Error("Full");});
  await user.click(screen.getByRole("button", {name: "Save practice notes"}));
  expect(screen.getByRole("alert")).toHaveTextContent("could not be saved");
  expect(screen.getByLabelText("On which task?")).toHaveValue("Parser");
  await user.click(screen.getByRole("button", {name: "Save practice notes"}));
  expect(write).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("status")).toHaveTextContent("saved");
});
