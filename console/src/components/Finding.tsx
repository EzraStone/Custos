import { useState } from "react";

import {
  RADIUS_LABEL,
  type Agent,
  type AuditEntry,
  type TransitionableStatus,
} from "../api/types";
import { History } from "./History";

/**
 * One finding, and the decision attached to it.
 *
 * The evidence gate is the important part of this component. The grant control
 * stays disabled until the evidence has actually been opened, because a console
 * that lets someone sanction an agent they have not read about upholds SEC-17
 * in code while defeating it in practice — the register becomes a rubber stamp
 * and every downstream guarantee rests on a click nobody thought about.
 *
 * It is a small gate and deliberately not a hard one. Someone determined to
 * approve without reading can open the section and ignore it; the point is that
 * the default path goes through the evidence rather than around it.
 */

export interface FindingProps {
  agent: Agent;
  /** Null when the session has no operator identity, which disables granting. */
  operator: string | null;
  onGrant: (agent: Agent) => void;
  /** Fetches the audit trail. Omitted, the history section is not offered. */
  loadHistory?: (agentId: string) => Promise<AuditEntry[]>;
  onTransition: (agent: Agent, to: TransitionableStatus) => void;
  busy?: boolean;
  /** Spend figures are estimates whenever pricing is unverified. */
  spendIsEstimate?: boolean;
}

export function Finding({
  agent,
  operator,
  onGrant,
  onTransition,
  loadHistory,
  busy = false,
  spendIsEstimate = true,
}: FindingProps) {
  const [evidenceSeen, setEvidenceSeen] = useState(false);

  const owner = agent.owner_team || agent.owner_human || "unattributed";
  const reach = [...agent.tools, ...agent.data_stores].sort();
  const shortName = agent.principal.split("/").pop() ?? agent.principal;

  const classes = [
    "finding",
    agent.blast_radius,
    agent.unsanctioned ? "" : "sanctioned",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={classes} data-testid={`finding-${agent.id}`}>
      <header>
        <h3>{shortName}</h3>
        <span className="radius">{RADIUS_LABEL[agent.blast_radius]}</span>
      </header>

      <dl className="meta">
        <div>
          <dt>Owner</dt>
          <dd className={agent.attributed ? "" : "muted"}>{owner}</dd>
        </div>
        <div>
          <dt>Principal</dt>
          <dd className="mono">{agent.principal}</dd>
        </div>
        <div>
          <dt>Compute</dt>
          <dd>{agent.compute || "unknown"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{agent.confidence.toFixed(2)}</dd>
        </div>
        <div>
          <dt>Est. spend</dt>
          <dd>
            {formatSpend(agent.est_monthly_spend_usd)}/mo
            {spendIsEstimate ? <span className="muted"> (estimate)</span> : null}
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{agent.status}</dd>
        </div>
      </dl>

      <p className="reach">
        <span className="label">Reaches</span>
        {reach.length > 0 ? reach.join(", ") : "none observed"}
      </p>

      <details onToggle={(event) => {
        if ((event.currentTarget as HTMLDetailsElement).open) setEvidenceSeen(true);
      }}>
        <summary>Why this was flagged</summary>
        <ul className="evidence">
          {agent.evidence.length > 0 ? (
            agent.evidence.map((line) => <li key={line}>{line}</li>)
          ) : (
            <li className="muted">
              No evidence recorded. This finding predates evidence capture, or
              was entered by hand.
            </li>
          )}
        </ul>
      </details>

      {loadHistory ? <History agentId={agent.id} load={loadHistory} /> : null}

      <div className="actions">
        {agent.unsanctioned ? (
          <>
            <button
              className="primary"
              disabled={!operator || !evidenceSeen || busy}
              onClick={() => onGrant(agent)}
            >
              {busy ? "Granting…" : "Grant imprimatur"}
            </button>
            <span className="hint">{hint(operator, evidenceSeen)}</span>
          </>
        ) : (
          <span className="hint">{standing(agent)}</span>
        )}

        <span className="spacer" />

        {/*
          Retiring is not gated on the evidence. Reading why a workload was
          flagged is what an approval needs; saying "this is gone" is a fact
          about the world, and someone decommissioning forty dead roles should
          not have to open forty evidence sections to do it.
        */}
        {agent.status !== "pending_review" ? (
          <button
            className="link"
            disabled={!operator || busy}
            onClick={() => onTransition(agent, "pending_review")}
          >
            Mark for review
          </button>
        ) : (
          <button
            className="link"
            disabled={!operator || busy}
            onClick={() => onTransition(agent, "discovered")}
          >
            Clear review flag
          </button>
        )}
        <button
          className="link"
          disabled={!operator || busy}
          onClick={() => onTransition(agent, "retired")}
        >
          Retire
        </button>
      </div>
    </article>
  );
}

/**
 * What an agent that is not awaiting a decision is.
 *
 * Branching on `unsanctioned` alone said "Sanctioned by unknown" for a retired
 * agent: retiring clears the imprimatur and takes the agent out of the
 * unsanctioned set, so it fell into the sanctioned branch with nobody to name.
 * Telling an operator that a decommissioned workload was approved by an
 * unknown person is worse than saying nothing.
 */
function standing(agent: Agent): string {
  if (agent.status === "retired") {
    return "Retired. A later scan that sees this again will surface it as a new finding.";
  }
  if (!agent.imprimatur) {
    // Sanctioned with no imprimatur should not be reachable — the grant is the
    // only path to that status and it always records one — so say what is
    // true rather than inventing an approver.
    return "Sanctioned, but no grant is recorded against it.";
  }
  return `Sanctioned by ${agent.imprimatur.granted_by} on ${formatDate(
    agent.imprimatur.granted_at,
  )}`;
}

/**
 * Why the button is disabled, in the order the reader can act on.
 *
 * A disabled control with no explanation is a bug report. Naming the missing
 * thing turns it into an instruction.
 */
function hint(operator: string | null, evidenceSeen: boolean): string {
  if (!operator) return "Enter your name above before granting.";
  if (!evidenceSeen) return "Read why this was flagged first.";
  return `Recorded against ${operator}.`;
}

function formatSpend(amount: number): string {
  if (amount < 1) return "<$1";
  return `$${Math.round(amount).toLocaleString()}`;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(0, 10);
}
