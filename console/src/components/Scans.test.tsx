import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Scan } from "../api/types";
import { Scans } from "./Scans";

function scan(overrides: Partial<Scan> = {}): Scan {
  return {
    id: 1,
    started_at: "2026-08-20T09:00:00+00:00",
    principals_seen: 11,
    agents_found: 5,
    review_candidates: 2,
    coverage: 1,
    truncated: false,
    ...overrides,
  };
}

describe("recent scans", () => {
  it("says nothing until there is a comparison to make", () => {
    // One scan is a fact, not a trend, and a one-row table implies otherwise.
    const { container } = render(<Scans scans={[scan()]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows coverage and scope side by side", () => {
    render(
      <Scans
        scans={[
          scan({ id: 2, coverage: 1, scope_readable: 0.2, scope_named: 1, scope_total: 5 }),
          scan({ id: 1, coverage: 1, scope_readable: 0.8, scope_named: 4, scope_total: 5 }),
        ]}
      />,
    );
    // The direction is the point: a customer who tagged their ENIs should be
    // able to see whether it worked.
    expect(screen.getByText(/20% \(1 of 5\)/)).toBeInTheDocument();
    expect(screen.getByText(/80% \(4 of 5\)/)).toBeInTheDocument();
  });

  it("shows a dash rather than zero when nothing internal was reached", () => {
    render(<Scans scans={[scan({ id: 2 }), scan({ id: 1 })]} />);
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("shows a dash against a control plane that does not report scope", () => {
    // An older server omits the field entirely. A zero would invent a problem.
    render(<Scans scans={[scan({ id: 2 }), scan({ id: 1 })]} />);
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("marks a truncated scan as truncated", () => {
    render(<Scans scans={[scan({ id: 2, truncated: true }), scan({ id: 1 })]} />);
    expect(screen.getByText(/truncated/)).toBeInTheDocument();
  });
});


describe("which region a scan covered", () => {
  it("names it when the history holds more than one", () => {
    // Two regions taking turns look like one account whose agent count halves
    // and doubles every other week, which is the shape of a real problem.
    render(
      <Scans
        scans={[
          scan({ id: 2, regions: ["eu-west-1"], agents_found: 2 }),
          scan({ id: 1, regions: ["us-east-1"], agents_found: 5 }),
        ]}
      />,
    );
    expect(screen.getByRole("columnheader", { name: "Region" })).toBeInTheDocument();
    expect(screen.getByText("eu-west-1")).toBeInTheDocument();
  });

  it("says nothing when every scan covered the same region", () => {
    render(
      <Scans
        scans={[
          scan({ id: 2, regions: ["us-east-1"] }),
          scan({ id: 1, regions: ["us-east-1"] }),
        ]}
      />,
    );
    expect(screen.queryByRole("columnheader", { name: "Region" })).toBeNull();
  });

  it("says nothing when the control plane sent no regions at all", () => {
    render(<Scans scans={[scan({ id: 2 }), scan({ id: 1 })]} />);
    expect(screen.queryByRole("columnheader", { name: "Region" })).toBeNull();
  });
});
