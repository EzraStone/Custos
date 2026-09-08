import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Review } from "../api/types";
import { Reviews } from "./Reviews";

function review(overrides: Partial<Review> = {}): Review {
  return {
    principal: "arn:aws:iam::1:role/nightly-doc-summariser",
    confidence: 0.69,
    evidence: ["Sent 2.1MB and received 890.0KB, a ratio of 2.4:1."],
    unavailable: [],
    scan_id: 12,
    seen_in_scans: 1,
    sends_to: [],
    ...overrides,
  };
}

describe("the review band", () => {
  it("says nothing when the scan was sure about everything", () => {
    const { container } = render(<Reviews reviews={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows what is known about a maybe", () => {
    render(<Reviews reviews={[review()]} />);
    expect(screen.getByText("nightly-doc-summariser")).toBeInTheDocument();
    expect(screen.getByText(/ratio of 2.4:1/)).toBeInTheDocument();
    expect(screen.getByText("0.69")).toBeInTheDocument();
  });

  it("marks a workload that keeps coming back", () => {
    // One uncertain window is noise. The same workload uncertain in eleven
    // scans is a question the classifier has been asking for a fortnight.
    render(<Reviews reviews={[review({ seen_in_scans: 11 })]} />);
    expect(screen.getByText(/in 11 scans/i)).toBeInTheDocument();
  });

  it("does not mark a one-off", () => {
    render(<Reviews reviews={[review({ seen_in_scans: 1 })]} />);
    expect(screen.queryByText(/in 1 scans/i)).toBeNull();
  });

  it("separates uncertain-for-want-of-input from genuinely ambiguous", () => {
    // Only one of those is fixed by sending us more data.
    render(<Reviews reviews={[review({ unavailable: ["decoupling"] })]} />);
    expect(screen.getByText(/for want of input/i)).toBeInTheDocument();
    expect(screen.getByText(/decoupling/)).toBeInTheDocument();
  });

  it("says why a maybe with no evidence is here", () => {
    render(<Reviews reviews={[review({ evidence: [] })]} />);
    expect(screen.getByText(/nothing scored high enough/i)).toBeInTheDocument();
  });

  it("names the undeclared address a maybe reaches", () => {
    // The strongest thing this list can say: a workload that resembles an
    // agent, reaches no provider we recognise, and sends a transcript-shaped
    // stream at an address nobody has declared is an agent behind a gateway.
    render(<Reviews reviews={[review({ sends_to: ["10.0.7.40"] })]} />);
    expect(screen.getByText("10.0.7.40")).toBeInTheDocument();
    expect(screen.getByText(/if that is a model gateway/i)).toBeInTheDocument();
  });

  it("says nothing about gateways when there is nothing to say", () => {
    // A note on every row is a note nobody reads.
    render(<Reviews reviews={[review()]} />);
    expect(screen.queryByText(/if that is a model gateway/i)).toBeNull();
  });

  it("keeps the order the API sent, which puts the correlated one first", () => {
    // The correlated one deliberately scores lower, so a component that
    // re-sorted by confidence — the obvious thing to reach for — would put it
    // second and fail here.
    render(
      <Reviews
        reviews={[
          review({
            principal: "role/deploy-remediation",
            confidence: 0.41,
            sends_to: ["10.0.7.40"],
          }),
          review({ principal: "role/ci-runner", confidence: 0.68 }),
        ]}
      />,
    );
    const names = screen.getAllByText(/deploy-remediation|ci-runner/);
    expect(names[0]).toHaveTextContent("deploy-remediation");
  });

  it("offers nothing to click", () => {
    // Promoting a maybe by hand is what the register is not for, and a
    // dismissal would be configuration that suppresses a classification.
    render(<Reviews reviews={[review()]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
