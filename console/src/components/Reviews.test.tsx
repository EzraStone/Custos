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

  it("offers nothing to click", () => {
    // Promoting a maybe by hand is what the register is not for, and a
    // dismissal would be configuration that suppresses a classification.
    render(<Reviews reviews={[review()]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("workloads nothing could decide about", () => {
  // The sentence is assembled from several JSX expressions, so it is several
  // text nodes and `getByText` can only ever see one of them. Reading the
  // rendered text is what an operator does.
  function text(container: HTMLElement): string {
    return container.textContent?.replace(/\s+/g, " ") ?? "";
  }

  it("still says nothing when there is nothing to disclose", () => {
    const { container } = render(<Reviews reviews={[]} undecidable={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("speaks up when the review band is empty and the silence is not earned", () => {
    // The case the disclosure exists for. Without it this account renders as
    // "the classifier was sure about everything", which is true and useless.
    const { container } = render(<Reviews reviews={[]} undecidable={3} />);
    expect(text(container)).toContain("3 workloads answer inbound requests");
    expect(text(container)).toContain("neither here nor in the register");
  });

  it("appears alongside the maybes when there are some", () => {
    const { container } = render(<Reviews reviews={[review()]} undecidable={2} />);
    expect(screen.getByText("nightly-doc-summariser")).toBeInTheDocument();
    expect(text(container)).toContain("2 workloads answer inbound requests");
  });

  it("reads as English for one", () => {
    const { container } = render(<Reviews reviews={[]} undecidable={1} />);
    expect(text(container)).toContain("1 workload answers inbound requests");
    expect(text(container)).toContain("it is neither here nor in the register");
    expect(text(container)).toContain("If it runs a tool loop");
  });

  it("names nobody", () => {
    // Deliberate. The overwhelming majority of workloads with this shape are
    // ordinary chatbots, and a list would read as an accusation of each.
    const { container } = render(<Reviews reviews={[]} undecidable={4} />);
    expect(container.querySelector("li")).toBeNull();
  });
});
