import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Change, DiffResponse } from "../api/types";
import { Changes } from "./Changes";

function change(overrides: Partial<Change> = {}): Change {
  return {
    kind: "appeared",
    agent_id: "agt_1",
    principal: "arn:aws:iam::1:role/finance-close",
    detail: "finance-close was not present in the previous scan",
    owner_team: "finance",
    blast_radius: "write",
    ...overrides,
  };
}

function diff(overrides: Partial<DiffResponse> = {}): DiffResponse {
  return {
    account_id: "1",
    previous_scan_id: 11,
    current_scan_id: 12,
    headline: "1 new agent since the last scan.",
    changes: [change()],
    ...overrides,
  };
}

describe("what changed since the last scan", () => {
  it("leads with the headline", () => {
    render(<Changes diff={diff()} onFocus={() => {}} />);
    expect(screen.getByText(/1 new agent since the last scan/)).toBeInTheDocument();
  });

  it("says nothing has been compared yet on a first scan", () => {
    // Not an empty list. An empty list reads as "nothing changed", which is a
    // different and much more reassuring claim than "there is no comparison".
    render(
      <Changes
        diff={diff({
          previous_scan_id: null,
          headline: "Nothing to compare yet — this account has one scan.",
          changes: [],
        })}
        onFocus={() => {}}
      />,
    );
    expect(screen.getByText(/nothing to compare yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /since the last scan/i })).toBeNull();
  });

  it("points at the agent that moved", async () => {
    const onFocus = vi.fn();
    render(<Changes diff={diff()} onFocus={onFocus} />);
    await userEvent.click(screen.getByRole("button", { name: "finance-close" }));
    expect(onFocus).toHaveBeenCalledWith("agt_1");
  });

  it("renders a change kind it has never heard of", () => {
    // The server owns the list of kinds. A console that dropped an unknown one
    // would hide exactly the new thing someone had just added.
    render(
      <Changes
        diff={diff({ changes: [change({ kind: "credential_rotated" })] })}
        onFocus={() => {}}
      />,
    );
    expect(screen.getByText("credential_rotated")).toBeInTheDocument();
  });

  it("marks an escalation differently from an appearance", () => {
    const { container } = render(
      <Changes
        diff={diff({
          changes: [
            change({ kind: "blast_radius_increased", agent_id: "agt_2" }),
            change({ kind: "appeared", agent_id: "agt_1" }),
          ],
        })}
        onFocus={() => {}}
      />,
    );
    expect(container.querySelector(".change.escalation")).not.toBeNull();
    expect(container.querySelector(".change.new")).not.toBeNull();
  });

  it("survives a response missing its changes", () => {
    // The types are hand-written; the API serves no schema. A shape the
    // console did not expect should cost the list, not the page.
    const malformed = { ...diff(), changes: undefined } as unknown as DiffResponse;
    render(<Changes diff={malformed} onFocus={() => {}} />);
    expect(screen.getByText(/1 new agent/)).toBeInTheDocument();
  });
});
