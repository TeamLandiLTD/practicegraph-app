import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  act, cleanup, fireEvent, render, screen, waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SkillShelf } from "./App.jsx";

const trustedSkill = {
  id: "debugging-read-the-error-first",
  title: "Read the error first",
  summary: "Extract the real failure.",
  prompt: "Read the complete error before changing code.",
  role: "debugging",
  for_work: ["Investigation"],
  reasons: ["answers command friction"],
  source_url: "https://github.com/TeamLandiLTD/skill-registry/tree/main/skills/debugging-read-the-error-first",
  install_command: "$skill-installer install https://github.com/TeamLandiLTD/skill-registry/tree/main/skills/debugging-read-the-error-first",
  claude_install_command: "mkdir -p ~/.claude/skills/debugging-read-the-error-first && "
    + "curl -fsSL https://raw.githubusercontent.com/TeamLandiLTD/skill-registry/main/skills/debugging-read-the-error-first/SKILL.md "
    + "-o ~/.claude/skills/debugging-read-the-error-first/SKILL.md",
};

describe("SkillShelf", () => {
  beforeEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("reports a successful copy to the local ledger", async () => {
    const onCopied = vi.fn().mockResolvedValue(true);
    render(
      <SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }}
                  onCopied={onCopied} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy for Codex" }));
    await waitFor(() => expect(onCopied).toHaveBeenCalledWith(trustedSkill.id));
  });

  // One recommendation, not six (2026-07-25). Ferraro & Price found generic
  // technical advice not statistically significant on its own — volume is the
  // arm that failed — so the shelf leads with the single skill that answers
  // the finding actually observed and keeps the rest behind a disclosure.
  it("shows one skill with a disclosure for the rest", () => {
    const many = Array.from({ length: 5 }, (_, i) => ({
      ...trustedSkill, id: `skill-${i}`, title: `Skill ${i}`,
    }));
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: many }} />);
    expect(screen.getByText("Skill 0")).toBeVisible();
    expect(screen.queryByText("Skill 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 4 more" }));
    expect(screen.getByText("Skill 4")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.queryByText("Skill 4")).not.toBeInTheDocument();
  });

  it("reports what the last taken skill's own measure did, either way", () => {
    const view = {
      skills_source: "teamlandi_public",
      skills: [trustedSkill],
      skill_outcome: {
        skill_id: "read-once",
        line: "Since you took “Read what you need once” in July, your prompts "
          + "carry about 50% more per turn — the other way.",
        note: "One window is not a verdict on the skill.",
      },
    };
    render(<SkillShelf view={view} />);
    expect(screen.getByText(/the other way/)).toBeVisible();
    // The caveat rides the ⓘ, not the face.
    expect(screen.getByText("One window is not a verdict on the skill.", { selector: ".infotip" }))
      .toBeInTheDocument();
  });

  it("keeps the ranking reasons off the shelf face", () => {
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    expect(screen.queryByText(/why these/)).not.toBeInTheDocument();
  });

  it("previews the canonical instructions without expanding by default", () => {
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    expect(screen.getByText("Preview instructions")).toBeVisible();
    expect(screen.getByText(trustedSkill.prompt)).not.toBeVisible();
    fireEvent.click(screen.getByText("Preview instructions"));
    expect(screen.getByText(trustedSkill.prompt)).toBeVisible();
  });

  it("copies the trusted installer command and links to the exact source", async () => {
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    expect(screen.getByRole("link", { name: "View source" })).toHaveAttribute(
      "href", trustedSkill.source_url,
    );
    expect(screen.getByRole("link", { name: "View source" })).toHaveAttribute(
      "rel", "noreferrer",
    );
    expect(screen.getByRole("link", { name: "View source" })).toHaveAttribute(
      "target", "_blank",
    );
    fireEvent.click(screen.getByRole("button", { name: "Copy for Codex" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      trustedSkill.install_command,
    ));
    expect(screen.getByText("Codex install copied")).toBeVisible();
    expect(screen.getByText(/Paste it into a Codex task/)).toBeVisible();
  });

  // The second deep link: the same registry SKILL.md is the Agent Skills
  // format Claude Code reads, so one fetch into ~/.claude/skills installs it.
  it("copies the Claude Code install with its own feedback", async () => {
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy for Claude Code" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      trustedSkill.claude_install_command,
    ));
    expect(screen.getByText("Claude Code install copied")).toBeVisible();
    expect(screen.getByText(/Claude Code session or shell/)).toBeVisible();
    expect(screen.queryByText("Codex install copied")).not.toBeInTheDocument();
  });

  it("offers only the Codex button when the Claude command is absent", () => {
    const codexOnly = { ...trustedSkill };
    delete codexOnly.claude_install_command;
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [codexOnly] }} />);
    expect(screen.getByRole("button", { name: "Copy for Codex" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Copy for Claude Code" }))
      .not.toBeInTheDocument();
  });

  it("keeps an enterprise install-only card prompt-only", async () => {
    const installOnly = { ...trustedSkill };
    delete installOnly.source_url;
    render(<SkillShelf view={{ skills_source: "enterprise", skills: [installOnly] }} />);
    expect(screen.queryByRole("link", { name: "View source" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      installOnly.prompt,
    ));
    expect(screen.getByText("Prompt copied")).toBeVisible();
    expect(screen.queryByText("Codex install copied")).not.toBeInTheDocument();
  });

  it.each([
    ["empty source URL", "teamlandi_public", { ...trustedSkill, source_url: "" }],
    ["empty install command", "teamlandi_public", { ...trustedSkill, install_command: "" }],
    ["enterprise source", "enterprise", trustedSkill],
    ["unknown source", "unknown", trustedSkill],
  ])("fails closed for %s", async (_case, skillsSource, skill) => {
    render(<SkillShelf view={{ skills_source: skillsSource, skills: [skill] }} />);
    expect(screen.queryByRole("link", { name: "View source" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      skill.prompt,
    ));
    expect(screen.getByText("Prompt copied")).toBeVisible();
    expect(screen.queryByText("Codex install copied")).not.toBeInTheDocument();
  });

  it("reveals selectable command text when clipboard access fails", async () => {
    navigator.clipboard.writeText.mockRejectedValueOnce(new Error("denied"));
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy for Codex" }));
    expect(await screen.findByText(trustedSkill.install_command)).toHaveClass("skcommand");
  });

  it("keeps an unknown catalog without install metadata prompt-only", async () => {
    const fallback = { ...trustedSkill };
    delete fallback.source_url;
    delete fallback.install_command;
    delete fallback.claude_install_command;
    render(<SkillShelf view={{ skills_source: "unknown", skills: [fallback] }} />);
    expect(screen.queryByRole("link", { name: "View source" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy prompt" }));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      fallback.prompt,
    ));
  });

  it("uses public-registry metadata only for the TeamLandi catalog", () => {
    const { rerender } = render(
      <SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />,
    );
    expect(screen.getByText("TeamLandi public registry")).toBeVisible();
    rerender(<SkillShelf view={{ skills_source: "enterprise", skills: [trustedSkill] }} />);
    expect(screen.queryByText("TeamLandi public registry"))
      .not.toBeInTheDocument();
    expect(screen.getByText("local catalog")).toBeVisible();
  });

  it("keeps newer same-card success visible for its full feedback window", async () => {
    vi.useFakeTimers();
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    const button = screen.getByRole("button", { name: "Copy for Codex" });
    await act(async () => fireEvent.click(button));
    act(() => vi.advanceTimersByTime(1200));
    await act(async () => fireEvent.click(button));
    act(() => vi.advanceTimersByTime(1200));
    expect(screen.getByText("Codex install copied")).toBeVisible();
    act(() => vi.advanceTimersByTime(1200));
    expect(screen.queryByText("Codex install copied")).not.toBeInTheDocument();
  });

  it("expires successful feedback after 2400 milliseconds", async () => {
    vi.useFakeTimers();
    render(<SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />);
    await act(async () => fireEvent.click(
      screen.getByRole("button", { name: "Copy for Codex" }),
    ));
    act(() => vi.advanceTimersByTime(2399));
    expect(screen.getByText("Codex install copied")).toBeVisible();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("Codex install copied")).not.toBeInTheDocument();
  });

  it("cancels pending feedback expiration when the shelf unmounts", async () => {
    vi.useFakeTimers();
    const { unmount } = render(
      <SkillShelf view={{ skills_source: "teamlandi_public", skills: [trustedSkill] }} />,
    );
    await act(async () => fireEvent.click(
      screen.getByRole("button", { name: "Copy for Codex" }),
    ));
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
