import type { Agent, BlastRadius } from "./api/types";

/**
 * Narrowing a register that has grown past reading in one sitting.
 *
 * Kept apart from the component that draws it because it is the part with
 * behaviour worth testing: which agents a filter hides is a decision about
 * what an operator sees, and it should be checkable without rendering
 * anything.
 *
 * There is deliberately no filter for confidence. Ordering by consequence is
 * the argument this product makes — what an agent could destroy matters more
 * than how sure we are it exists — and a confidence filter is the fastest way
 * to hide a destructive finding the classifier was only 0.8 sure about.
 */
export interface FilterState {
  radius: BlastRadius | "all";
  /**
   * An agent's status, or "all".
   *
   * Useful in both views. The register mixes four statuses once anything has
   * been retired, and even the unsanctioned list holds two — an agent someone
   * flagged for review is a different queue from one nobody has looked at.
   */
  status: string;
  query: string;
  unattributedOnly: boolean;
}

export const NO_FILTERS: FilterState = {
  radius: "all",
  status: "all",
  query: "",
  unattributedOnly: false,
};

export function isFiltered(f: FilterState): boolean {
  return (
    f.radius !== "all"
    || f.status !== "all"
    || f.query.trim() !== ""
    || f.unattributedOnly
  );
}

/**
 * Matches on the things a person would actually type: a role name, a team, or
 * something the agent reaches.
 *
 * Substring rather than fuzzy. An operator searching "billing" and being shown
 * `bill-ingest` has been given a worse answer than an empty result, because
 * they will assume the list is complete.
 */
export function matches(agent: Agent, f: FilterState): boolean {
  if (f.radius !== "all" && agent.blast_radius !== f.radius) return false;
  if (f.status !== "all" && agent.status !== f.status) return false;
  if (f.unattributedOnly && agent.attributed) return false;

  const query = f.query.trim().toLowerCase();
  if (!query) return true;
  return [
    agent.principal,
    agent.owner_team,
    agent.owner_human,
    agent.compute,
    ...agent.tools,
    ...agent.data_stores,
  ].some((field) => field.toLowerCase().includes(query));
}
