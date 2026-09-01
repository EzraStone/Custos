import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AuditEntry } from "../api/types";
import { History } from "./History";

function entry(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    at: "2026-03-14T09:30:00+00:00",
    actor: "ezra@custos.dev",
    action: "sanctioned",
    detail: "approved_tools=billing-api",
    ...overrides,
  };
}

const toggle = () => screen.getByText(/history/i, { selector: "summary" });

describe("the history section", () => {
  it("asks for nothing until it is opened", () => {
    const load = vi.fn(async () => [entry()]);
    render(<History agentId="agt_1" load={load} />);
    // Forty findings on a page would otherwise be forty audit requests for
    // history nobody asked to see.
    expect(load).not.toHaveBeenCalled();
  });

  it("loads on open and shows who did what", async () => {
    const load = vi.fn(async () => [entry()]);
    render(<History agentId="agt_1" load={load} />);

    await userEvent.click(toggle());

    expect(await screen.findByText("sanctioned")).toBeInTheDocument();
    expect(screen.getByText("ezra@custos.dev")).toBeInTheDocument();
    expect(load).toHaveBeenCalledWith("agt_1");
  });

  it("fetches once, however often it is toggled", async () => {
    const load = vi.fn(async () => [entry()]);
    render(<History agentId="agt_1" load={load} />);

    await userEvent.click(toggle());
    await screen.findByText("sanctioned");
    await userEvent.click(toggle());
    await userEvent.click(toggle());

    await waitFor(() => expect(load).toHaveBeenCalledTimes(1));
  });

  it("distinguishes an empty history from a failed one", async () => {
    const load = vi.fn(async () => []);
    render(<History agentId="agt_1" load={load} />);

    await userEvent.click(toggle());

    // "Nothing recorded" is a fact about the agent. A silent blank section
    // would read the same whether the request succeeded or never happened.
    expect(await screen.findByText(/nothing recorded/i)).toBeInTheDocument();
  });

  it("says so when the history cannot be read", async () => {
    const load = vi.fn(async () => {
      throw new Error("no such agent");
    });
    render(<History agentId="agt_1" load={load} />);

    await userEvent.click(toggle());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not load the history/i);
    expect(alert).toHaveTextContent(/no such agent/i);
  });

  it("keeps entries in the order the server sent them", async () => {
    const load = vi.fn(async () => [
      entry({ at: "2026-01-02T00:00:00+00:00", action: "discovered", actor: "" }),
      entry({ at: "2026-03-14T09:30:00+00:00", action: "sanctioned" }),
    ]);
    render(<History agentId="agt_1" load={load} />);

    await userEvent.click(toggle());
    await screen.findByText("sanctioned");

    const actions = screen.getAllByText(/discovered|sanctioned/);
    expect(actions.map((node) => node.textContent)).toEqual([
      "discovered",
      "sanctioned",
    ]);
  });
});
