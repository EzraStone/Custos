import { useState } from "react";

import { RADIUS_LABEL, type Agent } from "../api/types";

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
  busy?: boolean;
  /** Spend figures are estimates whenever pricing is unverified. */
  spendIsEstimate?: boolean;
}

export function Finding({
  agent,
  operator,
  onGrant,
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

      {agent.unsanctioned ? (
        <div className="actions">
          <button
            className="primary"
            disabled={!operator || !evidenceSeen || busy}
            onClick={() => onGrant(agent)}
          >
            {busy ? "Granting…" : "Grant imprimatur"}
          </button>
          <span className="hint">{hint(operator, evidenceSeen)}</span>
        </div>
      ) : (
        <div className="actions">
          <span className="hint">
            Sanctioned by {agent.imprimatur?.granted_by ?? "unknown"}
            {agent.imprimatur ? ` on ${formatDate(agent.imprimatur.granted_at)}` : ""}
          </span>
        </div>
      )}
    </article>
  );
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
