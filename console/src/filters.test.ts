import { describe, expect, it } from "vitest";

import type { Agent } from "./api/types";
import { NO_FILTERS, isFiltered, matches } from "./filters";

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
    tools: ["billing-api 10.0.4.21"],
    data_stores: ["rds 10.0.9.45"],
    est_monthly_spend_usd: 12,
    unsanctioned: true,
    imprimatur: null,
    ...overrides,
  };
}

describe("no filters", () => {
  it("matches everything", () => {
    expect(matches(agent(), NO_FILTERS)).toBe(true);
    expect(matches(agent({ blast_radius: "read" }), NO_FILTERS)).toBe(true);
  });

  it("is not reported as filtering", () => {
    expect(isFiltered(NO_FILTERS)).toBe(false);
    expect(isFiltered({ ...NO_FILTERS, query: "   " })).toBe(false);
  });
});

describe("filtering by blast radius", () => {
  it("keeps only the matching radius", () => {
    const f = { ...NO_FILTERS, radius: "destructive" as const };
    expect(matches(agent({ blast_radius: "destructive" }), f)).toBe(true);
    expect(matches(agent({ blast_radius: "write" }), f)).toBe(false);
  });
});

describe("searching", () => {
  it("finds a role by its short name", () => {
    expect(matches(agent(), { ...NO_FILTERS, query: "finance-close" })).toBe(true);
  });

  it("finds an agent by its team", () => {
    expect(matches(agent(), { ...NO_FILTERS, query: "finance" })).toBe(true);
  });

  it("finds an agent by something it reaches", () => {
    // The most useful search in an incident: who talks to this database.
    expect(matches(agent(), { ...NO_FILTERS, query: "10.0.9.45" })).toBe(true);
    expect(matches(agent(), { ...NO_FILTERS, query: "billing-api" })).toBe(true);
  });

  it("ignores case and surrounding space", () => {
    expect(matches(agent(), { ...NO_FILTERS, query: "  FINANCE  " })).toBe(true);
  });

  it("does not match on a substring the operator did not ask for", () => {
    // Substring, not fuzzy. Someone searching "billing" and being shown
    // `bill-ingest` has been given a worse answer than an empty result,
    // because they will assume the list is complete.
    expect(matches(agent({ owner_team: "platform" }), { ...NO_FILTERS, query: "bling" })).toBe(
      false,
    );
  });
});

describe("unattributed only", () => {
  it("keeps the agents nobody owns", () => {
    const f = { ...NO_FILTERS, unattributedOnly: true };
    expect(matches(agent({ attributed: false }), f)).toBe(true);
    expect(matches(agent({ attributed: true }), f)).toBe(false);
  });
});

describe("combining filters", () => {
  it("requires all of them", () => {
    const f = { radius: "destructive" as const, query: "finance", unattributedOnly: false };
    expect(matches(agent({ blast_radius: "destructive" }), f)).toBe(true);
    expect(matches(agent({ blast_radius: "read" }), f)).toBe(false);
    expect(
      matches(
        agent({
          blast_radius: "destructive",
          owner_team: "platform",
          // The principal has to move too: the search looks at every field,
          // and "finance" lives in the role name as well as the team.
          principal: "arn:aws:iam::1:role/ops-automation",
          tools: [],
          data_stores: [],
        }),
        f,
      ),
    ).toBe(false);
  });
});
