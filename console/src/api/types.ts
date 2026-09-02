/**
 * The shapes the control plane returns.
 *
 * Hand-written rather than generated, because the API deliberately does not
 * serve an OpenAPI schema — a public schema browser on a security product is an
 * invitation nobody asked for. The cost is this file; `types.test.ts` asserts it
 * matches what the Python renderer actually emits, which is what makes the cost
 * acceptable.
 */

/** What an agent's credential permits, worst first. */
export type BlastRadius = "destructive" | "write" | "read";

/** Where an agent sits in the register's state machine. */
export type Status = "discovered" | "pending_review" | "sanctioned" | "retired";

export interface Imprimatur {
  granted_by: string;
  granted_at: string;
  approved_tools: string[];
  approved_data: string[];
}

export interface Agent {
  id: string;
  principal: string;
  status: Status;
  confidence: number;

  /**
   * The sentences the classifier produced.
   *
   * The most important field on this object. A finding without them is a
   * score, and a score is what the workload's owner argues with instead of the
   * facts.
   */
  evidence: string[];

  owner_team: string;
  owner_human: string;
  compute: string;
  attributed: boolean;

  first_seen: string;
  last_seen: string;

  blast_radius: BlastRadius;
  tools: string[];
  data_stores: string[];
  est_monthly_spend_usd: number;

  unsanctioned: boolean;
  imprimatur: Imprimatur | null;
}

/**
 * The statuses an operator can move an agent to from the console.
 *
 * `sanctioned` is deliberately absent. It is reachable only through the
 * imprimatur endpoint, which requires an approval scope and records who
 * granted it — SEC-17 is that there is one door, and a status dropdown
 * containing "sanctioned" would be a second one.
 */
export type TransitionableStatus = "discovered" | "pending_review" | "retired";

export const TRANSITION_LABEL: Record<TransitionableStatus, string> = {
  discovered: "Return to the queue",
  pending_review: "Mark for review",
  retired: "Retire",
};

/** What each transition means, shown before it is made. */
export const TRANSITION_MEANING: Record<TransitionableStatus, string> = {
  discovered:
    "Puts this back in the unsanctioned list as though nobody had looked at it.",
  pending_review:
    "Flags this as someone's open question. It stays unsanctioned and keeps appearing.",
  retired:
    "Says this workload is gone. Any imprimatur it held is revoked, and a later scan that sees it again will surface it as a new finding.",
};

/**
 * A change between two scans.
 *
 * `kind` is not narrowed to a union on purpose. The server owns the list, and
 * a console that threw away a change kind it had not been told about would
 * hide exactly the new thing someone had just added.
 */
export interface Change {
  kind: string;
  agent_id: string;
  principal: string;
  detail: string;
  owner_team: string;
  blast_radius: BlastRadius;
}

export interface DiffResponse {
  account_id: string;
  previous_scan_id: number | null;
  current_scan_id: number | null;
  headline: string;
  changes: Change[];
}

/** Changes worth colouring. Anything else renders plainly. */
export const CHANGE_TONE: Record<string, string> = {
  appeared: "new",
  blast_radius_increased: "escalation",
  returned: "new",
};

export const CHANGE_LABEL: Record<string, string> = {
  appeared: "new",
  blast_radius_increased: "can do more damage",
  reach_expanded: "reaching further",
  disappeared: "gone",
  returned: "back",
  volume_jumped: "busier",
};

export interface AccountsResponse {
  accounts: string[];
}

export interface RegisterResponse {
  account_id: string;
  catalogue_revision: string;
  agents: Agent[];
}

export interface Scan {
  id: number;
  started_at: string;
  principals_seen: number;
  agents_found: number;
  review_candidates: number;
  coverage: number;
  truncated: boolean;
  /**
   * How much of this scan's approval scope was a name rather than an address.
   *
   * Optional because a control plane older than the field simply omits it, and
   * a console that showed "0% readable" against an older server would be
   * inventing a problem.
   */
  scope_readable?: number;
  scope_named?: number;
  scope_total?: number;
}

export interface ScansResponse {
  account_id: string;
  scans: Scan[];
}

export interface AuditEntry {
  at: string;
  actor: string;
  action: string;
  detail: string;
}

export interface AuditResponse {
  agent_id: string;
  entries: AuditEntry[];
}

export interface Health {
  status: string;
  version: string;
  /**
   * The model endpoint catalogue's revision. A provider added after this date
   * is not recognised, and an agent using only that provider does not appear in
   * any finding — so the console shows it rather than leaving it in a log.
   */
  catalogue_revision: string;
  /**
   * Reads `unverified-placeholder` until someone verifies real provider
   * pricing. While it does, every spend figure is order-of-magnitude only and
   * the console labels it that way.
   */
  prices_revision: string;
}

/** Ordering used everywhere an agent list is shown. */
export const RADIUS_RANK: Record<BlastRadius, number> = {
  destructive: 0,
  write: 1,
  read: 2,
};

export const RADIUS_LABEL: Record<BlastRadius, string> = {
  destructive: "can destroy",
  write: "can write",
  read: "read only",
};

/**
 * Worst first: by what an agent could destroy, then by how much it can reach,
 * then by confidence.
 *
 * Confidence ranks last deliberately. An unsanctioned agent that can write to
 * production outranks a dozen read-only ones however sure we are about them,
 * and sorting by confidence would put the tidiest findings at the top rather
 * than the most dangerous.
 */
export function byConsequence(a: Agent, b: Agent): number {
  const radius = RADIUS_RANK[a.blast_radius] - RADIUS_RANK[b.blast_radius];
  if (radius !== 0) return radius;

  const reach =
    b.tools.length + b.data_stores.length - (a.tools.length + a.data_stores.length);
  if (reach !== 0) return reach;

  return b.confidence - a.confidence;
}
