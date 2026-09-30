import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Reliance, Training } from "./App.jsx";

const view = () => ({reliance: {window_days: 28, headline: "Event shares do not measure working time.", facets: [
  {id: "grain", label: "Events per recorded handoff", line: "20 events per handoff.", delta: 4,
    delta_unit: "events per handoff", measures: "Event ratio.", why: "Tools vary."},
  {id: "engagement", label: "Composure", line: "75% got a moment's thought."},
  {id: "shaping", label: "Revision flags", line: "5% of tool calls.", delta: -2, delta_unit: "percentage points"},
]}, conditioning: [{id: "capacity", label: "Capacity", value: 214}], focus: {longest_block_min: 106}});

describe("interaction details", () => {
  it("folds diagnostics and states delta units", async () => {
    render(<Reliance view={view()} />);
    expect(screen.getByText("20 events per handoff.")).not.toBeVisible();
    await userEvent.setup().click(screen.getByText(/Interaction details/));
    expect(screen.getByText(/4 events per handoff higher/)).toBeVisible();
    expect(screen.getByText(/2 percentage points lower/)).toBeVisible();
  });
  it("does not infer ability or review from legacy timing values", () => {
    render(<Reliance view={view()} />);
    expect(screen.queryByText(/moment's thought|Composure|Capacity/)).not.toBeInTheDocument();
  });
  it("preserves a real zero delta", async () => {
    const v = view(); v.reliance.facets[0].delta = 0;
    render(<Reliance view={v} />);
    await userEvent.setup().click(screen.getByText(/Interaction details/));
    expect(screen.getByText(/Unchanged/)).toBeVisible();
  });
  it("withholds human-workstream claims when only legacy counters exist", () => {
    expect(render(<Training view={view()} />).container).toBeEmptyDOMElement();
  });
});
