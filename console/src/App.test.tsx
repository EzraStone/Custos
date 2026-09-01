import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { Agent } from "./api/types";
import * as session from "./session";

function agent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: "agt_1",
    principal: "arn:aws:iam::447120043318:role/finance-close",
    status: "discovered",
    confidence: 0.95,
    evidence: ["Sent 1.0MB and received 132.4KB, a ratio of 7.9:1."],
    owner_team: "finance",
    owner_human: "",
    compute: "Lambda",
    attributed: true,
    first_seen: "2026-08-10T00:00:00+00:00",
    last_seen: "2026-08-11T00:00:00+00:00",
    blast_radius: "write",
    tools: ["billing-api"],
    data_stores: [],
    est_monthly_spend_usd: 1420,
    unsanctioned: true,
    imprimatur: null,
    ...overrides,
  };
}

interface Backend {
  agents?: Agent[];
  coverage?: number;
  truncated?: boolean;
  registerStatus?: number;
  registerDetail?: string;
  grantStatus?: number;
  grantDetail?: string;
  pricesRevision?: string;
}

/** Stands in for the control plane, recording what the console asked for. */
function backend(config: Backend = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];

  const impl = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });

    const json = (status: number, body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      );

    if (url.startsWith("/healthz")) {
      return json(200, {
        status: "ok",
        version: "0.1.0",
        catalogue_revision: "2026-08-18",
        prices_revision: config.pricesRevision ?? "unverified-placeholder",
      });
    }
    if (url.startsWith("/v1/register")) {
      if (config.registerStatus && config.registerStatus !== 200) {
        return json(config.registerStatus, { detail: config.registerDetail ?? "no" });
      }
      return json(200, {
        account_id: "447120043318",
        catalogue_revision: "2026-08-18",
        agents: config.agents ?? [agent()],
      });
    }
    if (url.startsWith("/v1/scans")) {
      return json(200, {
        account_id: "447120043318",
        scans: [{
          id: 1,
          started_at: "2026-08-20T09:00:00+00:00",
          principals_seen: 11,
          agents_found: 5,
          review_candidates: 2,
          coverage: config.coverage ?? 1.0,
          truncated: config.truncated ?? false,
        }],
      });
    }
    if (url.includes("/imprimatur")) {
      if (config.grantStatus && config.grantStatus !== 200) {
        return json(config.grantStatus, { detail: config.grantDetail ?? "refused" });
      }
      return json(200, agent({ unsanctioned: false, status: "sanctioned" }));
    }
    return json(404, { detail: "not found" });
  });

  return { impl, calls };
}

function install(config: Backend = {}) {
  const stub = backend(config);
  vi.stubGlobal("fetch", stub.impl);
  return stub;
}

beforeEach(() => {
  session.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  session.clear();
});

describe("signing in", () => {
  it("asks for a credential before showing anything", () => {
    install();
    render(<App />);
    expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  });

  it("states what the console cannot do", () => {
    install();
    render(<App />);
    expect(screen.getByText(/cannot start a scan/i)).toBeInTheDocument();
  });

  it("loads the register once a token is entered", async () => {
    install();
    render(<App />);

    await userEvent.type(screen.getByLabelText(/control plane token/i), "tok-abc");
    await userEvent.type(screen.getByLabelText(/your name/i), "ezra@custos.dev");
    await userEvent.click(screen.getByRole("button", { name: /continue/i }));

    expect(await screen.findByText("finance-close")).toBeInTheDocument();
  });
});

describe("the register", () => {
  async function signedIn(config: Backend = {}, operator = "ezra@custos.dev") {
    const stub = install(config);
    session.save({ token: "tok-abc", operator });
    render(<App />);
    await screen.findByRole("heading", { name: /unsanctioned agents/i });
    return stub;
  }

  it("orders findings by what they could destroy", async () => {
    await signedIn({
      agents: [
        agent({ id: "read", principal: "role/reader", blast_radius: "read", confidence: 1 }),
        agent({ id: "destroy", principal: "role/destroyer", blast_radius: "destructive", confidence: 0.5 }),
      ],
    });

    const cards = await screen.findAllByRole("article");
    expect(cards[0]).toHaveAttribute("data-testid", "finding-destroy");
  });

  // The failure that looks like good news, and the console is the last place
  // it can be caught before someone acts on it.
  it("tells the reader to check coverage before trusting an empty list", async () => {
    await signedIn({ agents: [] });
    expect(
      await screen.findByText(/check the coverage of the last scan/i),
    ).toBeInTheDocument();
  });

  it("warns above the list when the last scan saw only part of the account", async () => {
    await signedIn({ coverage: 0.4 });
    expect(await screen.findByText(/parsed 40%/i)).toBeInTheDocument();
    expect(screen.getByText(/means less here than it usually would/i)).toBeInTheDocument();
  });

  it("does not warn when coverage was complete", async () => {
    await signedIn({ coverage: 1.0 });
    await screen.findByText("finance-close");
    expect(screen.queryByText(/incomplete coverage/i)).toBeNull();
  });

  it("labels spend as an estimate while pricing is unverified", async () => {
    await signedIn();
    expect(await screen.findByText(/\(estimate\)/)).toBeInTheDocument();
  });
});

describe("granting", () => {
  async function ready(config: Backend = {}) {
    const stub = install(config);
    session.save({ token: "tok-abc", operator: "ezra@custos.dev" });
    render(<App />);
    await screen.findByText("finance-close");
    await userEvent.click(
      screen.getByText(/why this was flagged/i, { selector: "summary" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /grant imprimatur/i }));
    return stub;
  }

  it("shows the scope before granting it", async () => {
    await ready();
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("billing-api");
    expect(dialog).toHaveTextContent("ezra@custos.dev");
  });

  it("does nothing on cancel", async () => {
    const stub = await ready();
    await userEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(stub.calls.some((call) => call.url.includes("/imprimatur"))).toBe(false);
  });

  it("sends the operator identity when confirmed", async () => {
    const stub = await ready();
    await userEvent.click(screen.getByRole("button", { name: /grant as ezra/i }));

    await waitFor(() => {
      const grant = stub.calls.find((call) => call.url.includes("/imprimatur"));
      expect(grant).toBeDefined();
      expect(JSON.parse(grant!.init!.body as string).operator).toBe("ezra@custos.dev");
    });
  });

  // A refusal the server explained must reach the person who can act on it.
  it("shows the server's refusal rather than closing silently", async () => {
    await ready({
      grantStatus: 409,
      grantDetail: "a retired agent must be reinstated before sanctioning",
    });
    await userEvent.click(screen.getByRole("button", { name: /grant as ezra/i }));

    expect(
      await screen.findByText(/must be reinstated before sanctioning/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("credentials that stop working", () => {
  // Leaving a console that shows the same error on every action makes the
  // operator debug the wrong thing.
  it("signs out on a 401 rather than showing an error forever", async () => {
    install({ registerStatus: 401, registerDetail: "invalid or missing credential" });
    session.save({ token: "stale", operator: "ezra" });
    render(<App />);

    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByText(/no longer valid/i)).toBeInTheDocument();
  });

  // A fleet token must say which account it means, and the server's message
  // explains exactly that.
  it("surfaces a fleet token's missing-account message", async () => {
    install({
      registerStatus: 400,
      registerDetail: "this credential covers several accounts; pass ?account=<id>",
    });
    session.save({ token: "tok-fleet", operator: "ezra" });
    render(<App />);

    expect(await screen.findByText(/pass \?account=/i)).toBeInTheDocument();
  });
});

describe("overlapping loads", () => {
  // Toggling the view issues a second register call while the first is still
  // in flight. If the slower one is allowed to write state when it lands, the
  // list shows one set of agents under a heading describing the other — in a
  // review queue that means sanctioned agents presented as awaiting approval.
  it("drops a response that a newer request has superseded", async () => {
    const unsanctionedOnly = agent({ id: "agt_slow", principal: "arn:aws:iam::1:role/slow-one" });
    const everything = agent({ id: "agt_fast", principal: "arn:aws:iam::1:role/fast-one" });

    // Seeded with a no-op rather than null: TypeScript does not track the
    // assignment inside the executor, so a nullable here narrows to never at
    // the call site below.
    let releaseFirst = () => {};
    const firstInFlight = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });

    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/v1/register")) {
          const filtered = url.includes("unsanctioned_only=true");
          // The filtered request is the one the console issues first. Hold it
          // open until the unfiltered one has already been answered.
          if (filtered) await firstInFlight;
          return json({
            account_id: "1",
            catalogue_revision: "2026-08-18",
            agents: [filtered ? unsanctionedOnly : everything],
          });
        }
        if (url.startsWith("/v1/scans")) return json({ account_id: "1", scans: [] });
        return json({
          status: "ok",
          version: "0.1.0",
          catalogue_revision: "2026-08-18",
          prices_revision: "unverified-placeholder",
        });
      }),
    );

    session.save({ token: "tok-abc", operator: "ezra@custos.dev" });
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: /show all/i }));
    expect(await screen.findByText("fast-one")).toBeInTheDocument();

    releaseFirst();

    // The superseded response lands now. Give it every chance to win.
    await waitFor(() => expect(screen.getByText("fast-one")).toBeInTheDocument());
    expect(screen.queryByText("slow-one")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /the register/i })).toBeInTheDocument();
  });
});
