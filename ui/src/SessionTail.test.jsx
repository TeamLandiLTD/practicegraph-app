import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SessionTail } from "./App.jsx";

// The card renders exclusively from geometry the composer ships — the tests
// pin that nothing is re-derived and that the honest-denominator trio
// (per day, active hours, per active hour) all reach the page.
function view(extra = {}) {
  return {
    session_tail: {
      eyebrow: "Where the day ends",
      title: "The session tail",
      intro: "The local clock time of the last prompt of each practice day.",
      headline:
        "Across 12 weeks, the median last prompt of your day moved from "
        + "18:18 to 00:24.",
      drift_line:
        "Last 7 active days: median last prompt 00:24. The prior 28: 00:18.",
      cross_title: "Concurrent sessions and where the day ends",
      cross_line:
        "Your 3+ sessions days carried 7.0 times the tool calls and 2.1 "
        + "times the active hours of your 1 session days, and ended "
        + "6h 58m later.",
      note: "Interactive events only, practice day starting 05:00.",
      active_days: 84,
      weeks: [
        { start_day: "2026-05-09", minutes: 798, clock: "18:18", days: 7 },
        { start_day: "2026-06-13", minutes: 1058, clock: "22:38", days: 7 },
        { start_day: "2026-08-14", minutes: 1164, clock: "00:24", days: 7 },
      ],
      buckets: [
        { id: "solo", label: "1 session", days: 24, clock: "17:23",
          tools_per_day: 69, active_hours_tenths: 42,
          tools_per_active_hour: 16 },
        { id: "multi", label: "3+ sessions", days: 36, clock: "00:21",
          tools_per_day: 488, active_hours_tenths: 89,
          tools_per_active_hour: 55 },
      ],
      labels: { tail: "median last prompt", tools: "tool calls / day",
                hours: "active hours / day", density: "calls / active hour" },
      chart: { axis_lo: 720, axis_hi: 1260, quiet_start: 1020,
               grid: [[720, "17:00"], [840, "19:00"], [960, "21:00"],
                      [1080, "23:00"], [1200, "01:00"]] },
      ...extra,
    },
  };
}

afterEach(cleanup);

describe("SessionTail", () => {
  it("renders nothing without the reading", () => {
    const { container } = render(<SessionTail view={{ session_tail: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the headline, the drift line and the cross sentence", () => {
    render(<SessionTail view={view()} />);
    expect(screen.getByText(/moved from 18:18 to 00:24/)).toBeInTheDocument();
    expect(screen.getByText(/The prior 28: 00:18/)).toBeInTheDocument();
    expect(
      screen.queryByText(/7\.0 times the tool calls and 2\.1 times the active/),
    ).not.toBeInTheDocument();
  });

  it("omits tool-volume comparisons from the clock reading", () => {
    render(<SessionTail view={view()} />);
    expect(screen.queryByText(/488 tool calls \/ day/)).not.toBeInTheDocument();
    expect(screen.queryByText(/8\.9 active hours \/ day/)).not.toBeInTheDocument();
    expect(screen.queryByText(/55 calls \/ active hour/)).not.toBeInTheDocument();
  });

  it("labels the grid from shipped geometry, never re-derived", () => {
    const { container } = render(<SessionTail view={view()} />);
    const labels = [...container.querySelectorAll("text")].map(
      (n) => n.textContent,
    );
    for (const clock of ["17:00", "19:00", "21:00", "23:00", "01:00"]) {
      expect(labels).toContain(clock);
    }
  });

  it("leads with the latest shipped clock and where the period started", () => {
    render(<SessionTail view={view()} />);
    const hero = screen.getByTestId("tail-hero");
    expect(hero).toHaveTextContent("00:24");
    expect(hero).toHaveTextContent("Latest weekly median");
    expect(hero).toHaveTextContent(/from 18:18 in the week of May 9/);
  });

  it("draws a smooth line over a filled area and dates the weeks", () => {
    const { container } = render(<SessionTail view={view()} />);
    expect(container.querySelector("path.tail-line").getAttribute("d")).toMatch(/^M[\d. ,-]+C/);
    expect(container.querySelector("path.tail-area")).not.toBeNull();
    const labels = [...container.querySelectorAll("text.tailtick")].map((n) => n.textContent);
    expect(labels).toEqual(["May 9", "Jun 13", "Aug 14"]);
  });

  it("names the quiet-hours band only when the composer ships its edge", () => {
    const { container, unmount } = render(<SessionTail view={view()} />);
    expect(container.querySelector("rect.tail-quiet")).not.toBeNull();
    // The threshold itself is a line at the shipped quiet-band edge; without a
    // shipped schedule clock the label stays wordless about the time.
    const edge = container.querySelector("line.tail-quiet-line");
    expect(edge).not.toBeNull();
    expect(edge.getAttribute("y1")).toBe(edge.getAttribute("y2"));
    expect(screen.getByText("quiet hours begin")).toBeInTheDocument();
    unmount();
    const timed = view();
    timed.schedule = { quiet_start: "22:00" };
    const withClock = render(<SessionTail view={timed} />);
    expect(screen.getByText("quiet hours begin · 22:00")).toBeInTheDocument();
    // Weeks at or past the edge are marked; earlier weeks are not.
    const dots = [...withClock.container.querySelectorAll("circle.tail-dot")];
    expect(dots.map((d) => d.classList.contains("late"))).toEqual([false, true, true]);
    withClock.unmount();
    const bare = view();
    bare.session_tail.chart.quiet_start = null;
    const again = render(<SessionTail view={bare} />);
    expect(again.container.querySelector("rect.tail-quiet")).toBeNull();
    expect(again.container.querySelector("line.tail-quiet-line")).toBeNull();
    expect(again.container.querySelector("circle.tail-dot.late")).toBeNull();
  });

  it("shows a week's reading on hover and on keyboard focus", () => {
    const { container } = render(<SessionTail view={view()} />);
    const tipOf = () => container.querySelector("svg.tailchart [role=tooltip]");
    expect(tipOf()).toBeNull();
    const hits = container.querySelectorAll("rect.tail-hit");
    expect(hits).toHaveLength(3);
    fireEvent.mouseEnter(hits[1]);
    expect(tipOf()).toHaveTextContent("wk of Jun 13 · 22:38");
    fireEvent.mouseLeave(container.querySelector("svg.tailchart"));
    expect(tipOf()).toBeNull();
    fireEvent.focus(hits[2]);
    expect(tipOf()).toHaveTextContent("wk of Aug 14 · 00:24");
    expect(hits[2]).toHaveAttribute("aria-label", "Week of Aug 14: median last prompt 00:24");
  });
});
