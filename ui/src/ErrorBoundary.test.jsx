import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary, LAST_ERROR_KEY, recordClientError } from "./ErrorBoundary.jsx";

function Boom({ when }) {
  if (when) throw new Error("usage series is undefined");
  return <p>content</p>;
}

beforeEach(() => { localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("ErrorBoundary", () => {
  it("shows the error and a reload control instead of an empty page", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Boom when /></ErrorBoundary>);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("PracticeGraph hit an error");
    expect(alert).toHaveTextContent("usage series is undefined");
    expect(screen.getByRole("button", { name: "Reload the page" })).toBeInTheDocument();
  });

  it("keeps a record of the last error for the next report", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ErrorBoundary><Boom when /></ErrorBoundary>);
    const saved = JSON.parse(localStorage.getItem(LAST_ERROR_KEY));
    expect(saved.message).toBe("usage series is undefined");
    expect(saved.source).toBe("render");
    expect(typeof saved.at).toBe("string");
  });

  it("reloads on request", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const reload = vi.fn();
    render(<ErrorBoundary reload={reload}><Boom when /></ErrorBoundary>);
    await userEvent.setup().click(screen.getByRole("button", { name: "Reload the page" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("renders children untouched when nothing throws", () => {
    render(<ErrorBoundary><Boom when={false} /></ErrorBoundary>);
    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("records window errors without depending on storage being writable", () => {
    recordClientError({ source: "window", message: "boom", stack: "x" });
    expect(JSON.parse(localStorage.getItem(LAST_ERROR_KEY)).message).toBe("boom");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(() => recordClientError({ source: "window", message: "again" })).not.toThrow();
  });
});
