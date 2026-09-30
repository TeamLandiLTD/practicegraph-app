import React from "react";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { PrivacyStatus } from "./PrivacyStatus.jsx";

it("distinguishes sharing, historical sends and optional provider use", () => {
  render(<PrivacyStatus privacy={{
    sharing_enabled: true, endpoint_configured: false, sent_count: 11,
    queued_count: 12, wording_provider: "claude",
  }} />);
  expect(screen.getByText("Aggregate sharing enabled")).toBeInTheDocument();
  expect(screen.getByText(/11 aggregate records were sent previously/)).toBeInTheDocument();
  expect(screen.getByText(/may be sent to that provider/)).toBeInTheDocument();
  expect(screen.getByText(/Destination not configured/)).toBeInTheDocument();
  expect(screen.queryByText(/nothing sent/)).not.toBeInTheDocument();
});

it("does not claim privacy state is known when an older server omits it", () => {
  render(<PrivacyStatus />);
  expect(screen.getByText("Privacy status unavailable")).toBeInTheDocument();
});
