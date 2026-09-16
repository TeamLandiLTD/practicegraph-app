import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CapabilityCoach } from "./CapabilityCoach.jsx";
import { safeCodexUrl } from "./urls.js";

const baseCapability = {
  paths: ["knowledge", "software"],
  paths_confirmed: true,
  pending_outcome: null,
  opportunity: null,
  practice_result: null,
  practice_progress: null,
};

describe("safeCodexUrl", () => {
  it.each([
    ["codex://new?prompt=Review%20this", "codex://new?prompt=Review%20this"],
    ["https://example.com", null],
    ["javascript:alert(1)", null],
    [null, null],
  ])("closes the Codex composer scheme", (value, expected) => {
    expect(safeCodexUrl(value)).toBe(expected);
  });
});

describe("CapabilityCoach", () => {
  it("lets the employee privately choose both capability paths", async () => {
    const user = userEvent.setup();
    const savePaths = vi.fn().mockResolvedValue({ ok: true });
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(
      <CapabilityCoach
        capability={{ ...baseCapability, paths_confirmed: false }}
        refresh={refresh}
        savePaths={savePaths}
      />,
    );
    await user.click(screen.getByLabelText("Software work"));
    await user.click(screen.getByRole("button", { name: "Save my focus" }));
    expect(savePaths).toHaveBeenCalledWith(["knowledge"]);
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("keeps the final capability path selected so an invalid save cannot occur", async () => {
    const user = userEvent.setup();
    const savePaths = vi.fn().mockResolvedValue({ ok: true });
    render(
      <CapabilityCoach
        capability={{ ...baseCapability, paths: ["knowledge"], paths_confirmed: false }}
        refresh={vi.fn()}
        savePaths={savePaths}
      />,
    );

    await user.click(screen.getByLabelText("Knowledge work"));
    expect(screen.getByLabelText("Knowledge work")).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save my focus" }));
    expect(savePaths).toHaveBeenCalledWith(["knowledge"]);
    expect(savePaths).not.toHaveBeenCalledWith([]);
  });

  it("records only the chosen outcome enum", async () => {
    const user = userEvent.setup();
    const recordOutcome = vi.fn().mockResolvedValue({ ok: true });
    render(
      <CapabilityCoach
        capability={{
          ...baseCapability,
          pending_outcome: {
            question: "Did the latest piece of work reach the result you wanted?",
            options: [
              { id: "yes", label: "Yes" },
              { id: "partly", label: "Partly" },
              { id: "no", label: "No" },
            ],
          },
        }}
        refresh={vi.fn()}
        showRecentWork
        recordOutcome={recordOutcome}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Partly" }));
    expect(recordOutcome).toHaveBeenCalledWith("partly");
  });

  it("accepts only the server-minted practice id", async () => {
    const user = userEvent.setup();
    const acceptPractice = vi.fn().mockResolvedValue({ ok: true });
    render(
      <CapabilityCoach
        capability={{
          ...baseCapability,
          opportunity: {
            practice_id: "run_verification",
            capability_id: "verify-result",
            path: "software",
            title: "Make verification part of the handoff",
            observation: "The completed change had no observed test run.",
            why: "A short verification step makes the result easier to trust.",
            practice: "Ask for the smallest relevant check before accepting the result.",
            codex_url: "codex://new?prompt=Run%20the%20smallest%20relevant%20check",
            confidence: "high",
          },
        }}
        refresh={vi.fn()}
        acceptPractice={acceptPractice}
      />,
    );
    expect(screen.getByRole("link", { name: "Open in Codex" })).toHaveAttribute(
      "href",
      "codex://new?prompt=Run%20the%20smallest%20relevant%20check",
    );
    expect(screen.getByRole("link", { name: "Open in Codex" })).toHaveAttribute(
      "target",
      "_blank",
    );
    expect(screen.getByRole("link", { name: "Open in Codex" })).toHaveAttribute(
      "rel",
      "noreferrer",
    );
    await user.click(screen.getByRole("button", { name: "Try this practice" }));
    expect(acceptPractice).toHaveBeenCalledWith("run_verification");
  });

  it("disables controls while a save is pending", async () => {
    const user = userEvent.setup();
    let resolveSave;
    const savePaths = vi.fn(() => new Promise((resolve) => { resolveSave = resolve; }));
    render(
      <CapabilityCoach
        capability={{ ...baseCapability, paths_confirmed: false }}
        refresh={vi.fn()}
        savePaths={savePaths}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Save my focus" }));
    expect(screen.getByLabelText("Knowledge work")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save my focus" })).toBeDisabled();
    resolveSave({ ok: true });
  });

  it("keeps the current state and offers a retry after a rejected save", async () => {
    const user = userEvent.setup();
    const recordOutcome = vi.fn().mockRejectedValue(new Error("offline"));
    render(
      <CapabilityCoach
        capability={{
          ...baseCapability,
          pending_outcome: { question: "Did the latest piece of work reach the result you wanted?", options: [{ id: "yes", label: "Yes" }] },
        }}
        refresh={vi.fn()}
        showRecentWork
        recordOutcome={recordOutcome}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Yes" }));
    expect(screen.getByRole("alert")).toHaveTextContent("That did not save. Try once more.");
    expect(screen.getByRole("button", { name: "Yes" })).toBeEnabled();
  });

  it("does not render a malicious Codex link", () => {
    render(
      <CapabilityCoach
        capability={{ ...baseCapability, opportunity: { practice_id: "run_verification", title: "A practice", observation: "Observe.", why: "Why.", practice: "Do this.", codex_url: "javascript:alert(1)" } }}
        refresh={vi.fn()}
      />,
    );
    expect(screen.queryByRole("link", { name: "Open in Codex" })).not.toBeInTheDocument();
  });

  it("shows the server-written practice result without comparison values", () => {
    render(
      <CapabilityCoach
        capability={{ ...baseCapability, practice_result: { practice_id: "run_verification", title: "Make verification part of the handoff", status: "improved", comparable_units: 3, line: "The next three comparable changes included verification more often." } }}
        refresh={vi.fn()}
      />,
    );
    expect(screen.getByText("The next three comparable changes included verification more often.")).toBeVisible();
    expect(screen.getByText(/This local check used 3 comparable pieces of work/)).toBeVisible();
  });

  it("keeps the privacy and method notes off the coach face", () => {
    render(
      <CapabilityCoach
        capability={{ ...baseCapability, practice_result: { practice_id: "run_verification", title: "T", status: "improved", comparable_units: 3, line: "L." } }}
        refresh={vi.fn()}
      />,
    );
    expect(screen.queryByText(/stay on this device/)).not.toBeInTheDocument();
    expect(screen.getByText(/do not establish whether the practice caused/, { selector: ".infotip" })).toBeInTheDocument();
    expect(screen.queryByText(/do not establish whether/, { ignore: ".infotip" })).not.toBeInTheDocument();
  });

  it("keeps an accepted practice visible while its local check is pending", () => {
    render(
      <CapabilityCoach
        capability={{
          ...baseCapability,
          practice_progress: {
            title: "Make verification part of the handoff",
            practice: "Ask for the smallest relevant check before accepting the result.",
            codex_url: "codex://new?prompt=Run%20the%20smallest%20relevant%20check",
            comparable_units: 1,
            context: "One of three comparable pieces of work is ready for the local check.",
          },
        }}
        refresh={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Make verification part of the handoff" })).toBeVisible();
    expect(screen.getByText("Ask for the smallest relevant check before accepting the result.")).toBeVisible();
    expect(screen.getByText("One of three comparable pieces of work is ready for the local check.")).toBeVisible();
    expect(screen.queryByText("There is not enough recorded activity for a personal suggestion yet. You can plan a practice above or use the AI working checklist.")).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open in Codex" });
    expect(link).toHaveAttribute(
      "href",
      "codex://new?prompt=Run%20the%20smallest%20relevant%20check",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });

  it("waits quietly when there is no completed work to coach", () => {
    render(<CapabilityCoach capability={baseCapability} refresh={vi.fn()} />);
    expect(screen.getByText("There is not enough recorded activity for a personal suggestion yet. You can plan a practice above or use the AI working checklist.")).toBeVisible();
  });
});


it("can finish an untried practice and change a previously confirmed focus", async () => {
  const user = userEvent.setup();
  const reviewPractice = vi.fn().mockResolvedValue({ ok: true });
  const refresh = vi.fn();
  render(<CapabilityCoach capability={{ ...baseCapability,
    practice_progress: {title: "Verify", practice: "Run a check", context: "Waiting for work"} }}
    reviewPractice={reviewPractice} refresh={refresh} />);
  await user.click(screen.getByRole("button", {name: "Didn't try it"}));
  expect(reviewPractice).toHaveBeenCalledWith("not_tried");
  expect(refresh).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", {name: "Change my focus"}));
  expect(screen.getByLabelText("Knowledge work")).toBeChecked();
});

it("keeps recent-work reflection disabled by default", () => {
  render(<CapabilityCoach capability={{...baseCapability, pending_outcome: {question: "Reflect now", options: [{id: "yes", label: "Yes"}]}}} refresh={vi.fn()} />);
  expect(screen.queryByText("Reflect now")).not.toBeInTheDocument();
});
