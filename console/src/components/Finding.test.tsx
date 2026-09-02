import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Agent } from "../api/types";
import { Finding } from "./Finding";

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agt_1",
    principal: "arn:aws:iam::447120043318:role/finance-close",
    status: "discovered",
    confidence: 0.95,
    evidence: [
      "Sent 1.0MB to model endpoints and received 132.4KB back, a ratio of 7.9:1.",
      "100% of the intervals containing model traffic had no request arriving at the load balancer.",
    ],
    owner_team: "finance",
    owner_human: "",
    compute: "Lambda",
    attributed: true,
    first_seen: "2026-08-10T00:00:00+00:00",
    last_seen: "2026-08-11T00:00:00+00:00",
    blast_radius: "write",
    tools: ["billing-api"],
    data_stores: ["billing-db"],
    est_monthly_spend_usd: 1420,
    unsanctioned: true,
    imprimatur: null,
    ...overrides,
  };
}

function grantButton() {
  return screen.getByRole("button", { name: /grant imprimatur/i });
}

/**
 * The evidence disclosure.
 *
 * Selected by its element rather than by text: a summary's text also belongs to
 * its parent details, so a plain text query matches both and fails as
 * ambiguous.
 */
function evidenceToggle() {
  return screen.getByText(/why this was flagged/i, { selector: "summary" });
}

describe("the evidence gate", () => {
  // A console that lets someone sanction an agent they have not read about
  // upholds SEC-17 in code while defeating it in practice.
  it("disables granting until the evidence has been opened", async () => {
    render(<Finding agent={agent()} operator="ezra@custos.dev" onGrant={vi.fn()} onTransition={vi.fn()} />);

    expect(grantButton()).toBeDisabled();
    expect(screen.getByText(/read why this was flagged first/i)).toBeTruthy();

    await userEvent.click(evidenceToggle());
    expect(grantButton()).not.toBeDisabled();
  });

  it("does not fire the grant callback while gated", async () => {
    const onGrant = vi.fn();
    render(<Finding agent={agent()} operator="ezra" onGrant={onGrant} onTransition={vi.fn()} />);

    await userEvent.click(grantButton()).catch(() => undefined);
    expect(onGrant).not.toHaveBeenCalled();
  });

  it("fires once the evidence is open", async () => {
    const onGrant = vi.fn();
    render(<Finding agent={agent()} operator="ezra" onGrant={onGrant} onTransition={vi.fn()} />);

    await userEvent.click(evidenceToggle());
    await userEvent.click(grantButton());
    expect(onGrant).toHaveBeenCalledWith(expect.objectContaining({ id: "agt_1" }));
  });
});

describe("the operator requirement", () => {
  // SEC-17 needs a person. A token is a machine.
  it("cannot grant without a name, even with the evidence open", async () => {
    render(<Finding agent={agent()} operator={null} onGrant={vi.fn()} onTransition={vi.fn()} />);

    await userEvent.click(evidenceToggle());
    expect(grantButton()).toBeDisabled();
    expect(screen.getByText(/enter your name above/i)).toBeTruthy();
  });

  // A disabled control with no explanation is a bug report.
  it("names the missing thing rather than leaving it disabled silently", () => {
    render(<Finding agent={agent()} operator={null} onGrant={vi.fn()} onTransition={vi.fn()} />);
    expect(screen.getByText(/enter your name above/i)).toBeTruthy();
  });

  it("names whose identity is recorded once it can grant", async () => {
    render(<Finding agent={agent()} operator="ezra@custos.dev" onGrant={vi.fn()} onTransition={vi.fn()} />);
    await userEvent.click(evidenceToggle());
    expect(screen.getByText(/recorded against ezra@custos.dev/i)).toBeTruthy();
  });
});

describe("what a finding shows", () => {
  it("shows the evidence sentences verbatim", async () => {
    render(<Finding agent={agent()} operator="ezra" onGrant={vi.fn()} onTransition={vi.fn()} />);
    await userEvent.click(evidenceToggle());

    expect(screen.getByText(/a ratio of 7\.9:1/)).toBeTruthy();
    expect(screen.getByText(/no request arriving at the load balancer/)).toBeTruthy();
  });

  it("says so when a finding has no evidence rather than showing nothing", async () => {
    render(<Finding agent={agent({ evidence: [] })} operator="ezra" onGrant={vi.fn()} onTransition={vi.fn()} />);
    await userEvent.click(evidenceToggle());
    expect(screen.getByText(/no evidence recorded/i)).toBeTruthy();
  });

  it("marks an unattributed finding rather than showing a blank owner", () => {
    render(
      <Finding
        agent={agent({ owner_team: "", owner_human: "", attributed: false })}
        operator="ezra"
        onGrant={vi.fn()} onTransition={vi.fn()}
      />,
    );
    expect(screen.getByText("unattributed")).toBeTruthy();
  });

  it("lists reach, or says none was observed", () => {
    const { unmount } = render(
      <Finding agent={agent()} operator="ezra" onGrant={vi.fn()} onTransition={vi.fn()} />,
    );
    expect(screen.getByText(/billing-api, billing-db/)).toBeTruthy();
    unmount();

    render(
      <Finding
        agent={agent({ tools: [], data_stores: [] })}
        operator="ezra"
        onGrant={vi.fn()} onTransition={vi.fn()}
      />,
    );
    expect(screen.getByText(/none observed/i)).toBeTruthy();
  });

  // A placeholder price read as a fact is worse than no price at all.
  it("labels spend as an estimate when pricing is unverified", () => {
    render(
      <Finding agent={agent()} operator="ezra" onGrant={vi.fn()} onTransition={vi.fn()} spendIsEstimate />,
    );
    expect(screen.getByText(/\(estimate\)/)).toBeTruthy();
  });
});

describe("a sanctioned agent", () => {
  it("offers no grant control and names who approved it", () => {
    render(
      <Finding
        agent={agent({
          unsanctioned: false,
          status: "sanctioned",
          imprimatur: {
            granted_by: "ezra@custos.dev",
            granted_at: "2026-08-20T09:00:00+00:00",
            approved_tools: ["billing-api"],
            approved_data: [],
          },
        })}
        operator="ezra"
        onGrant={vi.fn()} onTransition={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /grant imprimatur/i })).toBeNull();
    expect(screen.getByText(/sanctioned by ezra@custos.dev/i)).toBeTruthy();
  });
});
