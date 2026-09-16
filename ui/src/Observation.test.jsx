import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Observation } from "./App.jsx";

// PRINCIPLES 3: the observation leads, and a claim never appears without its
// period and confidence. The caveat is what keeps it a work-pattern reading
// rather than a health assessment, so it must be on the page, not in a title.
function view(extra = {}) {
  return {
    observation: {
      id: "repeated-no-pause",
      title: "Long stretches without a pause are repeating",
      body: "A two-hour stretch without a 15-minute pause appeared on 9 of the last 28 days.",
      period: "last 28 days",
      confidence: "repeated pattern",
      caveat: "A work-pattern observation, not a health assessment.",
      ...extra,
    },
  };
}

afterEach(cleanup);

describe("Observation", () => {
  it("renders nothing when the key is absent", () => {
    const { container } = render(<Observation view={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the claim with its period, confidence and caveat", () => {
    render(<Observation view={view()} />);
    expect(screen.getByText(/Long stretches without a pause/)).toBeInTheDocument();
    expect(screen.getByText(/appeared on 9 of the last 28 days/)).toBeInTheDocument();
    // scoped to the period badge: the sentence above also contains this
    // substring ("...9 of the last 28 days."), so an unscoped query matches
    // both and getByText correctly refuses to pick one.
    expect(
      screen.getByText(/last 28 days/, { selector: ".obsperiod" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/repeated pattern/)).toBeInTheDocument();
    expect(
      screen.getByText(/not a health assessment/),
    ).toBeInTheDocument();
  });

  it("still explains itself when there is no pattern yet", () => {
    render(
      <Observation
        view={view({
          id: "insufficient",
          title: "No repeated pattern yet",
          body: "The available history has not cleared a repeated-pattern sample gate.",
          confidence: "insufficient",
        })}
      />,
    );
    // withheld is explained, never blank
    expect(screen.getByText(/has not cleared a repeated-pattern sample gate/))
      .toBeInTheDocument();
    expect(screen.getByText(/insufficient/)).toBeInTheDocument();
  });
});
