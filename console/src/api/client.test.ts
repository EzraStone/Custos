import { describe, expect, it, vi } from "vitest";

import { ApiError, Client } from "./client";
import { byConsequence, type Agent } from "./types";

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientWith(fetchImpl: typeof globalThis.fetch, baseUrl = "") {
  return new Client({ token: "tok-abc", baseUrl, fetch: fetchImpl });
}

describe("client", () => {
  it("sends the token as a bearer credential", async () => {
    const fetchImpl = vi.fn(async () => respond(200, { status: "ok" }));
    await clientWith(fetchImpl as never).health();

    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-abc");
  });

  it("asks for only the unsanctioned set when told to", async () => {
    const fetchImpl = vi.fn(async () => respond(200, { agents: [] }));
    await clientWith(fetchImpl as never).register("447120043318", true);

    const [url] = fetchImpl.mock.calls[0] as [string];
    expect(url).toContain("account=447120043318");
    expect(url).toContain("unsanctioned_only=true");
  });

  it("omits the query entirely for a single-account token", async () => {
    const fetchImpl = vi.fn(async () => respond(200, { agents: [] }));
    await clientWith(fetchImpl as never).register();

    const [url] = fetchImpl.mock.calls[0] as [string];
    expect(url).toBe("/v1/register");
  });

  it("escapes an agent id rather than pasting it into a path", async () => {
    const fetchImpl = vi.fn(async () => respond(200, { entries: [] }));
    await clientWith(fetchImpl as never).audit("agt/../../etc");

    const [url] = fetchImpl.mock.calls[0] as [string];
    expect(url).not.toContain("../");
  });

  // Scope defaults to what was observed. An operator approving an agent is
  // approving what it was seen doing.
  it("sends nulls for an unspecified grant scope", async () => {
    const fetchImpl = vi.fn(async () => respond(200, { id: "agt_1" }));
    await clientWith(fetchImpl as never).grant("agt_1", "ezra@custos.dev");

    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      operator: "ezra@custos.dev",
      approved_tools: null,
      approved_data: null,
    });
  });

  it("sends an explicit scope when one is given", async () => {
    const fetchImpl = vi.fn(async () => respond(200, { id: "agt_1" }));
    await clientWith(fetchImpl as never).grant("agt_1", "ezra", {
      tools: ["billing-api"],
      data: [],
    });

    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string).approved_tools).toEqual(["billing-api"]);
  });
});

// A console that says "request failed" when the server said "this credential
// covers several accounts; pass ?account=" has moved the work to the person
// reading it.
describe("errors", () => {
  it("carries the server's own message", async () => {
    const fetchImpl = vi.fn(async () =>
      respond(400, { detail: "this credential covers several accounts; pass ?account=<id>" }),
    );

    await expect(clientWith(fetchImpl as never).register()).rejects.toThrow(
      /pass \?account=/,
    );
  });

  it("flattens FastAPI validation arrays into something readable", async () => {
    const fetchImpl = vi.fn(async () =>
      respond(422, {
        detail: [{ loc: ["body", "operator"], msg: "String should have at least 1 character" }],
      }),
    );

    await expect(
      clientWith(fetchImpl as never).grant("agt_1", ""),
    ).rejects.toThrow(/body\.operator: String should have at least 1 character/);
  });

  it("survives a non-JSON body from a proxy", async () => {
    const fetchImpl = vi.fn(
      async () => new Response("<html>502 Bad Gateway</html>", { status: 502 }),
    );

    await expect(clientWith(fetchImpl as never).health()).rejects.toThrow(/HTTP 502/);
  });

  it("marks an unauthenticated failure so the caller can sign out", async () => {
    const fetchImpl = vi.fn(async () =>
      respond(401, { detail: "invalid or missing credential" }),
    );

    await clientWith(fetchImpl as never)
      .register()
      .catch((error: ApiError) => {
        expect(error.unauthenticated).toBe(true);
        expect(error.needsAccount).toBe(false);
      });
  });

  it("marks a missing-account failure distinctly from an auth failure", async () => {
    const fetchImpl = vi.fn(async () =>
      respond(400, { detail: "covers several accounts; pass ?account=<id>" }),
    );

    await clientWith(fetchImpl as never)
      .register()
      .catch((error: ApiError) => {
        expect(error.needsAccount).toBe(true);
        expect(error.unauthenticated).toBe(false);
      });
  });
});

// Confidence ranks last deliberately: sorting by it puts the tidiest findings
// at the top rather than the most dangerous.
describe("ordering", () => {
  function agent(overrides: Partial<Agent>): Agent {
    return {
      id: "a", principal: "role/x", status: "discovered", confidence: 0.9,
      evidence: [], owner_team: "", owner_human: "", compute: "", attributed: false,
      first_seen: "", last_seen: "", blast_radius: "read", tools: [], data_stores: [],
      est_monthly_spend_usd: 0, unsanctioned: true, imprimatur: null,
      ...overrides,
    };
  }

  it("ranks blast radius above everything else", () => {
    const sorted = [
      agent({ id: "read", blast_radius: "read", confidence: 1.0 }),
      agent({ id: "destroy", blast_radius: "destructive", confidence: 0.5 }),
      agent({ id: "write", blast_radius: "write", confidence: 0.99 }),
    ].sort(byConsequence);

    expect(sorted.map((a) => a.id)).toEqual(["destroy", "write", "read"]);
  });

  it("ranks reach above confidence within a radius", () => {
    const sorted = [
      agent({ id: "narrow", tools: [], confidence: 1.0 }),
      agent({ id: "wide", tools: ["a", "b"], data_stores: ["c"], confidence: 0.81 }),
    ].sort(byConsequence);

    expect(sorted.map((a) => a.id)).toEqual(["wide", "narrow"]);
  });
});
