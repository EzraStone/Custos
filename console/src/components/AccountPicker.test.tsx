import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountPicker } from "./AccountPicker";

describe("the account picker", () => {
  it("says how many accounts the credential covers", () => {
    render(
      <AccountPicker
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
    render(<AccountPicker accounts={accounts} current="" onChoose={() => {}} />);
    for (const account of accounts) {
      expect(screen.getByRole("button", { name: account })).toBeInTheDocument();
    }
  });

  it("reports the choice", async () => {
    const onChoose = vi.fn();
    render(
      <AccountPicker
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
