import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Agent } from "../api/types";
import { StatusDialog } from "./StatusDialog";

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agt_1",
    principal: "arn:aws:iam::1:role/finance-close",
    status: "discovered",
    confidence: 0.95,
    evidence: [],
    owner_team: "finance",
    owner_human: "",
    compute: "Lambda",
    attributed: true,
    first_seen: "2026-08-10T00:00:00+00:00",
    last_seen: "2026-08-11T00:00:00+00:00",
    blast_radius: "write",
    tools: [],
    data_stores: [],
    est_monthly_spend_usd: 12,
    unsanctioned: true,
    imprimatur: null,
    ...overrides,
  };
}

function open(props: Partial<Parameters<typeof StatusDialog>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  render(
    <StatusDialog
      agent={agent()}
      to="retired"
      operator="ezra@custos.dev"
      busy={false}
      error={null}
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onConfirm, onCancel };
}

const confirm = (name = /retire/i) => screen.getByRole("button", { name });

describe("changing an agent's status", () => {
  it("will not proceed without a reason", async () => {
    const { onConfirm } = open();
    expect(confirm()).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/why/i), "decommissioned in DEP-812");
    expect(confirm()).not.toBeDisabled();

    await userEvent.click(confirm());
    expect(onConfirm).toHaveBeenCalledWith("decommissioned in DEP-812");
  });

  it("does not accept whitespace as a reason", async () => {
    open();
    await userEvent.type(screen.getByLabelText(/why/i), "   ");
    expect(confirm()).toBeDisabled();
  });

  it("says what the transition will actually do", () => {
    open({ to: "retired" });
    expect(screen.getByText(/says this workload is gone/i)).toBeInTheDocument();
  });

  it("warns when retiring would revoke an approval", () => {
    // The operator retiring a sanctioned agent is undoing someone else's
    // deliberate grant. That should not be discovered afterwards.
    open({
      agent: agent({
        unsanctioned: false,
        status: "sanctioned",
        imprimatur: {
          granted_by: "priya@custos.dev",
          granted_at: "2026-03-14T09:30:00+00:00",
          approved_tools: [],
          approved_data: [],
        },
      }),
    });
    const notice = screen.getByText(/this revokes an approval/i);
    expect(notice).toBeInTheDocument();
    expect(screen.getByText(/priya@custos.dev/)).toBeInTheDocument();
  });

  it("does not warn about revocation when nothing was granted", () => {
    open();
    expect(screen.queryByText(/this revokes an approval/i)).not.toBeInTheDocument();
  });

  it("shows the server's refusal rather than a generic failure", () => {
    open({ error: "sanctioned -> discovered is not a permitted transition" });
    expect(screen.getByRole("alert")).toHaveTextContent(/not a permitted transition/i);
  });

  it("closes on escape", async () => {
    const { onCancel } = open();
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalled();
  });

  it("holds the register still while it is open", () => {
    open();
    expect(document.body.style.overflow).toBe("hidden");
  });
});
