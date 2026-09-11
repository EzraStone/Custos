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
  /**
   * Every region this agent has been seen in.
   *
   * `tools`, `data_stores` and `est_monthly_spend_usd` above cover all of
   * them. `blast_radius` comes from IAM, which is account-wide by nature: a
   * role that can delete a bucket can delete it from anywhere it runs.
   */
  regions: string[];

  /**
   * What was resolved in each region, which is not the union above divided up.
   *
   * A region with empty lists is one where a scan saw this agent and resolved
   * no destinations at all — usually a flow log with no port field. Rendering
   * that the same as a region the agent genuinely touches nothing in would
   * report a gap in the log as a fact about the workload.
   *
   * Optional: a control plane older than this field sends none, and every
   * agent discovered before it exists has an empty one.
   */
  region_reach?: Record<string, { tools: string[]; data_stores: string[] }>;

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

export interface DriftItem {
  kind: string;
  observed_at: string;
  /** The phrasing to show. A question gets answered; an accusation gets argued with. */
  question: string;
  detail: string;
  /**
   * Which region this is drift in.
   *
   * A baseline is per region, because an agent's behaviour in us-east-1 is a
   * trend and its observations in two regions interleaved are two trends
   * sampled alternately. Shown because "reached a new datastore" sends its
   * owner looking in all three deployments until it says which one did it.
   *
   * Optional: a control plane older than the field sends none, and so does an
   * observation recorded before regions existed.
   */
  region?: string;
}

export interface DriftResponse {
  agent_id: string;
  observations: number;
  drift: DriftItem[];
  baseline: {
    tools: string[];
    observations: number;
    /** Whether there is enough history for any of this to mean anything. */
    established: boolean;
  };
}

export interface Review {
  principal: string;
  confidence: number;
  evidence: string[];
  /** Signals that could not be evaluated, usually for want of access logs. */
  unavailable: string[];
  scan_id: number;
  /** How many of this account's scans put this workload in the review band. */
  seen_in_scans: number;
}

export interface ReviewsResponse {
  account_id: string;
  reviews: Review[];
}

export interface GatewayCandidate {
  address: string;
  egress: number;
  ingress: number;
  principals: string[];
  blind_principals: string[];
  /** The phrasing to show. It carries the numbers somebody would check. */
  question: string;
  /**
   * Region this question was asked about.
   *
   * Answering it declares the address for that region only — the same address
   * elsewhere is a different host, and declaring it everywhere would turn an
   * unrelated internal service into a model endpoint.
   */
  region: string;
  scan_id: number;
}

export interface CandidatesResponse {
  account_id: string;
  candidates: GatewayCandidate[];
  /**
   * Internal destinations that had a gateway's traffic shape and were not
   * asked about, because the workloads reaching them reach nothing else — the
   * shape a log collector or a backup service has.
   *
   * Shown because an empty question list means two different things. "We
   * looked and there is nothing" is reassuring; "we looked, found nine, and
   * ruled out all nine on a rule that can be wrong" is not, and a console that
   * renders both as blank space is the silence a hidden gateway produces.
   */
  declined?: number;
}

export interface DeclaredEndpoint {
  id: number;
  value: string;
  kind: string;
  note: string;
  declared_by: string;
  declared_at: string;
  active: boolean;
  withdrawn_by?: string;
  withdrawn_at?: string | null;
}

export interface EndpointsResponse {
  account_id: string;
  endpoints: DeclaredEndpoint[];
}

export interface FleetRow {
  account_id: string;
  agents: number;
  unsanctioned: number;
  /** The number to triage by. Read-only agents are a different afternoon. */
  destructive: number;
  last_scan: string | null;
  coverage: number | null;
  scope_readable: number | null;
  /**
   * Regions this account has ever been collected in.
   *
   * The column that tells a one-region view of a three-region account apart
   * from a clean account: every other number here would be identical.
   */
  regions: string[];
  reviews: number;
  gateway_questions: number;
  rates_verified: boolean;
}

export interface FleetResponse {
  accounts: FleetRow[];
}

/**
 * Worst first, by what somebody would act on.
 *
 * An account nobody has scanned sorts above every account that has been,
 * whatever it holds. "We have never looked" outranks any finding, because a
 * finding is a thing somebody knows and an unscanned account is a thing
 * nobody does.
 */
export function byUrgency(a: FleetRow, b: FleetRow): number {
  const unscanned = Number(b.last_scan === null) - Number(a.last_scan === null);
  if (unscanned !== 0) return unscanned;
  if (b.destructive !== a.destructive) return b.destructive - a.destructive;
  if (b.unsanctioned !== a.unsanctioned) return b.unsanctioned - a.unsanctioned;
  return a.account_id.localeCompare(b.account_id);
}

export interface AccountsResponse {
  accounts: string[];
}

export interface RegisterResponse {
  account_id: string;
  catalogue_revision: string;
  agents: Agent[];
  /**
   * Whose rates priced the spend figures below.
   *
   * Optional: a control plane older than the field omits it, and the console
   * falls back to what /healthz says — which is the process default rather
   * than this account's answer, and therefore the safer of the two to be
   * wrong about.
   */
  prices_revision?: string;
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
  /**
   * Which region this scan covered.
   *
   * A scan is one region's window, so a multi-region account's history is two
   * interleaved series. Read as one it looks like an account whose agent count
   * halves and doubles every other week — which is the shape of a real
   * problem, and here it is the shape of two regions taking turns.
   *
   * Optional: a control plane older than the field omits it.
   */
  regions?: string[];
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
