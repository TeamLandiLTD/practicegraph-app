import React from "react";
import {render, screen, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {beforeEach, describe, expect, it, vi} from "vitest";
import NewsPanel, {NewsUrgency} from "./NewsPanel.jsx";
import {changeNews, fetchNews} from "./api.js";

vi.mock("./api.js", () => ({fetchNews: vi.fn(), changeNews: vi.fn()}));
const attention = () => ({urgency: "urgent", reason: "The endpoint closes tomorrow.",
  starts_at: new Date(Date.now() - 60000).toISOString(), expires_at: new Date(Date.now() + 3600000).toISOString()});
const fixture = () => ({unread_count: 1, quiet_hours: false,
  settings: {mode: "important", urgent_popup: true, snoozed_until: 0},
  items: [{id: "endpoint-update", title: "Review the endpoint migration", hook: "A configuration change is needed.",
    summary: "The maintainer describes the supported configuration.", why: "Review this before deploying.",
    source: "Maintainer", url: "https://example.org/update", unread: true,
    attention_active: true, attention: attention()}]});

beforeEach(() => {vi.clearAllMocks(); fetchNews.mockResolvedValue(fixture()); changeNews.mockResolvedValue(fixture());});

describe("news attention", () => {
  it("shows a headline and editorial reason without marking it read just for appearing", async () => {
    render(<NewsPanel />);
    expect(await screen.findByRole("heading", {name: "Review the endpoint migration"})).toBeInTheDocument();
    expect(screen.getByText("Urgent")).toBeInTheDocument();
    expect(screen.getByText("The endpoint closes tomorrow.")).toBeInTheDocument();
    expect(changeNews).not.toHaveBeenCalled();
  });

  it("records deliberate reading and updates the unread count", async () => {
    const user = userEvent.setup();
    const updated = fixture(); updated.unread_count = 0; updated.items[0].unread = false;
    changeNews.mockResolvedValue(updated);
    render(<NewsPanel />);
    await user.click(await screen.findByRole("button", {name: "Mark read"}));
    expect(changeNews).toHaveBeenCalledWith({action: "read", id: "endpoint-update"});
    expect(await screen.findByText("0 unread")).toBeInTheDocument();
  });

  it("offers mute, popup control, and snooze", async () => {
    const user = userEvent.setup(); render(<NewsPanel />);
    await screen.findByText("1 unread");
    await user.click(screen.getByText("Notification settings"));
    await user.selectOptions(screen.getByRole("combobox", {name: "Notify me about"}), "off");
    expect(changeNews).toHaveBeenCalledWith({action: "settings", mode: "off", urgent_popup: true});
    await user.click(screen.getByRole("button", {name: "Snooze 1 hour"}));
    expect(changeNews).toHaveBeenCalledWith({action: "snooze", minutes: 60});
  });

  it("does not show an urgent label after expiry or before the start", () => {
    const a = attention(); a.expires_at = new Date(Date.now() - 1000).toISOString();
    const {rerender} = render(<NewsUrgency attention={a} />);
    expect(screen.queryByText("Urgent")).not.toBeInTheDocument();
    a.starts_at = new Date(Date.now() + 100000).toISOString();
    a.expires_at = new Date(Date.now() + 200000).toISOString();
    rerender(<NewsUrgency attention={a} />);
    expect(screen.queryByText("Urgent")).not.toBeInTheDocument();
  });

  it("recovers after a failed read and refuses non-HTTPS source links", async () => {
    const user = userEvent.setup(); fetchNews.mockRejectedValueOnce(new Error("offline"));
    render(<NewsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("News could not be loaded");
    const data = fixture(); data.items[0].url = "javascript:alert(1)";
    fetchNews.mockResolvedValue(data);
    await user.click(screen.getByRole("button", {name: "Retry"}));
    await waitFor(() => expect(screen.getByText("1 unread")).toBeInTheDocument());
    expect(screen.queryByRole("link", {name: /Open source/})).not.toBeInTheDocument();
  });
});
