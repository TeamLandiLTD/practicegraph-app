import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
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
});
