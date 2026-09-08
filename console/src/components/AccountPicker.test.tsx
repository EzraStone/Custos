import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FleetRow } from "../api/types";
import { AccountPicker } from "./AccountPicker";

describe("the account picker", () => {
  it("says how many accounts the credential covers", () => {
    render(
      <AccountPicker
        fleet={[]}
        accounts={["111111111111", "222222222222", "333333333333"]}
        current=""
        onChoose={() => {}}
      />,
    );
    // The count is the point: a customer who believes they run four accounts
    // and is shown eleven has learned something before clicking anything.
    expect(screen.getByText(/covers 3 accounts/i)).toBeInTheDocument();
  });

  it("lists every account, not a truncated sample", () => {
    const accounts = ["111111111111", "222222222222", "333333333333"];
    render(<AccountPicker fleet={[]} accounts={accounts} current="" onChoose={() => {}} />);
    for (const account of accounts) {
      expect(screen.getByRole("button", { name: account })).toBeInTheDocument();
    }
  });

  it("reports the choice", async () => {
    const onChoose = vi.fn();
    render(
      <AccountPicker
        fleet={[]}
        accounts={["111111111111", "222222222222"]}
        current=""
        onChoose={onChoose}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "222222222222" }));
    expect(onChoose).toHaveBeenCalledWith("222222222222");
  });

  it("marks the account already in use", () => {
    render(
      <AccountPicker
        fleet={[]}
        accounts={["111111111111", "222222222222"]}
        current="222222222222"
        onChoose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "222222222222" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByRole("button", { name: "111111111111" })).not.toHaveAttribute(
      "aria-current",
    );
  });
});

describe("choosing between accounts", () => {
  function row(overrides: Partial<FleetRow> = {}): FleetRow {
    return {
      account_id: "111111111111",
      agents: 12, unsanctioned: 5, destructive: 1,
      last_scan: "2026-09-08T09:00:00+00:00",
      coverage: 1, scope_readable: 0.75,
      reviews: 2, gateway_questions: 0, rates_verified: true,
      ...overrides,
    };
  }

  it("puts an unscanned account above everything", () => {
    // "We have never looked here" outranks any finding. A finding is
    // something somebody knows; an unscanned account is something nobody does.
    render(
      <AccountPicker
        fleet={[
          row({ account_id: "111111111111", destructive: 9 }),
          row({ account_id: "222222222222", last_scan: null, agents: 0,
                unsanctioned: 0, destructive: 0, coverage: null }),
        ]}
        accounts={[]}
        current=""
        onChoose={() => {}}
      />,
    );
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveTextContent("222222222222");
    expect(buttons[0]).toHaveTextContent(/never scanned/i);
  });

  it("says nothing else about an account nobody has scanned", () => {
    // Listing zeroes beside it would read as "we looked and it is clean".
    render(
      <AccountPicker
        fleet={[row({ last_scan: null, agents: 0, unsanctioned: 0, destructive: 0 })]}
        accounts={[]}
        current=""
        onChoose={() => {}}
      />,
    );
    const button = screen.getByRole("button");
    expect(button).toHaveTextContent(/never scanned/i);
    expect(button).not.toHaveTextContent(/unsanctioned/i);
  });

  it("leads with what can destroy things", () => {
    render(
      <AccountPicker fleet={[row({ destructive: 3 })]} accounts={[]} current="" onChoose={() => {}} />,
    );
    expect(screen.getByRole("button")).toHaveTextContent(/3 that can destroy/);
  });

  it("orders by destructive before unsanctioned", () => {
    render(
      <AccountPicker
        fleet={[
          row({ account_id: "111111111111", destructive: 0, unsanctioned: 40 }),
          row({ account_id: "222222222222", destructive: 1, unsanctioned: 2 }),
        ]}
        accounts={[]}
        current=""
        onChoose={() => {}}
      />,
    );
    expect(screen.getAllByRole("button")[0]).toHaveTextContent("222222222222");
  });

  it("says an account is clean rather than showing a zero", () => {
    render(
      <AccountPicker
        fleet={[row({ unsanctioned: 0, destructive: 0 })]}
        accounts={[]} current="" onChoose={() => {}}
      />,
    );
    expect(screen.getByRole("button")).toHaveTextContent(/nothing unsanctioned/i);
  });

  it("falls back to bare account numbers against an older control plane", () => {
    render(
      <AccountPicker
        fleet={[]} accounts={["111111111111", "222222222222"]}
        current="" onChoose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "111111111111" })).toBeInTheDocument();
  });
});
