import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DriftResponse } from "../api/types";
import { Drift } from "./Drift";

function response(overrides: Partial<DriftResponse> = {}): DriftResponse {
  return {
    agent_id: "agt_1",
    observations: 14,
    drift: [],
    baseline: { tools: ["billing-api 10.0.4.21"], observations: 13, established: true },
    ...overrides,
  };
}

const toggle = () => screen.getByText(/behaviour/i, { selector: "summary" });

describe("the behaviour section", () => {
  it("asks for nothing until it is opened", () => {
    const load = vi.fn(async () => response());
    render(<Drift agentId="agt_1" load={load} />);
    expect(load).not.toHaveBeenCalled();
  });

  it("puts drift as a question, not an accusation", async () => {
    const load = vi.fn(async () =>
      response({
        drift: [{
          kind: "new_tool",
          observed_at: "2026-08-20T09:00:00+00:00",
          question: "finance-close reached rds 10.0.9.45 for the first time. Is that expected?",
          detail: "finance-close reached rds 10.0.9.45 for the first time",
        }],
      }),
    );
    render(<Drift agentId="agt_1" load={load} />);
    await userEvent.click(toggle());

    // A question gets answered; an accusation gets argued with.
    expect(await screen.findByText(/is that expected\?/i)).toBeInTheDocument();
    expect(screen.getByText("new tool")).toBeInTheDocument();
  });

  it("shows nothing when the baseline is not established", async () => {
    // Drift measured against two observations is noise, and rendering it under
    // a heading would give that noise the same standing as a finding.
    const load = vi.fn(async () =>
      response({
        observations: 2,
        baseline: { tools: [], observations: 1, established: false },
        drift: [{
          kind: "volume_spike",
          observed_at: "2026-08-20T09:00:00+00:00",
          question: "busier than usual. Is that expected?",
          detail: "busier than usual",
        }],
      }),
    );
    render(<Drift agentId="agt_1" load={load} />);
    await userEvent.click(toggle());

    expect(await screen.findByText(/not enough history yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/is that expected/i)).toBeNull();
  });

  it("says an agent is behaving normally rather than showing a blank", async () => {
    const load = vi.fn(async () => response());
    render(<Drift agentId="agt_1" load={load} />);
    await userEvent.click(toggle());

    expect(await screen.findByText(/behaving as it has been/i)).toBeInTheDocument();
    expect(screen.getByText(/13 observations/)).toBeInTheDocument();
  });

  it("says so when the baseline cannot be read", async () => {
    const load = vi.fn(async () => {
      throw new Error("no such agent");
    });
    render(<Drift agentId="agt_1" load={load} />);
    await userEvent.click(toggle());

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load the baseline/i);
  });
});
