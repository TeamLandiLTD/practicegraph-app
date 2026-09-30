import React from "react";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ContentStatus } from "./ContentStatus.jsx";

// The edition line is one short chip (COPY_RULES rule 4): the label and the
// date on the face, the curator behind a title, the source as the link.

it("says plainly when no edition has been downloaded yet", () => {
  render(<ContentStatus label="Model guidance" />);
  expect(screen.getByText("Model guidance · no edition downloaded yet")).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});

it("renders one short edition line that links to the catalog source", () => {
  render(<ContentStatus label="Model guidance" status={{
    version: "models-2026-09-08", edition_date: "2026-09-08", state: "current",
    source_label: "Curated by TeamLandi", source_url: "https://example.com/models.json",
  }} />);
  const line = screen.getByRole("link", { name: "Model guidance edition 2026-09-08" });
  expect(line).toHaveAttribute("href", "https://example.com/models.json");
  expect(line).toHaveAttribute("title", "Curated by TeamLandi");
  expect(screen.queryByText(/Curated by/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Catalog source/)).not.toBeInTheDocument();
});

it("keeps an older edition's date and its warning on the same short line", () => {
  render(<ContentStatus status={{
    version: "models-2026-08-01", edition_date: "2026-08-01", state: "stale",
    source_label: "Curated by TeamLandi", source_url: "https://example.com/models.json",
    checked_at: "2026-09-05T12:00:00Z",
  }} />);
  expect(screen.getByRole("link", { name: "Curated reading edition 2026-08-01 · older — check its sources" }))
    .toHaveAttribute("href", "https://example.com/models.json");
});

it("says when the edition date could not be verified and stays a span without a source", () => {
  render(<ContentStatus label="News" status={{ version: "x", state: "date_unknown" }} />);
  expect(screen.getByText("News · edition date unavailable")).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
