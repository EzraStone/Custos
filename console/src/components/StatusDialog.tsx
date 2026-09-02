import { useRef, useState } from "react";

import {
  TRANSITION_LABEL,
  TRANSITION_MEANING,
  type Agent,
  type TransitionableStatus,
} from "../api/types";
import { useDialog } from "../useDialog";

/**
 * Change what an agent is, short of sanctioning it.
 *
 * Retiring is the one that matters. An agent nobody has retired stays in the
 * register forever, so a decommissioned workload keeps appearing as an
 * unsanctioned finding and the queue slowly fills with things that no longer
 * exist. A queue full of noise is a queue nobody reads, and then the real
 * finding in the middle of it goes unread too.
 *
 * A reason is required, not optional. "Retired" with no explanation is a
 * decision nobody can review later — including the person who made it — and
 * this is the register's own history, not a form field.
 */
export function StatusDialog({
  agent,
  to,
  operator,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  agent: Agent;
  to: TransitionableStatus;
  operator: string;
  busy: boolean;
  error: string | null;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus starts in the reason field: it is required, and it is the thing the
  // operator has to supply before anything can happen. Escape, the scroll
  // lock, the focus trap, and returning focus on close live in useDialog.
  const dialogRef = useDialog<HTMLDivElement>(onCancel, inputRef);

  const name = agent.principal.split("/").pop() ?? agent.principal;
  const revokes = to === "retired" && !agent.unsanctioned;

  return (
    <div className="overlay" role="presentation" onClick={onCancel}>
      <div
        ref={dialogRef}
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="status-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="status-title">
          {TRANSITION_LABEL[to]}: {name}?
        </h2>
        <p className="lede">{TRANSITION_MEANING[to]}</p>

        {revokes ? (
          <div className="notice">
            <span className="tag">This revokes an approval</span>
            <p>
              {name} is sanctioned. Retiring it withdraws the imprimatur granted
              by {agent.imprimatur?.granted_by ?? "an operator"}, and it will
              come back as a new finding if a later scan sees it again.
            </p>
          </div>
        ) : null}

        <div className="field">
          <label htmlFor="status-reason">Why</label>
          <input
            id="status-reason"
            ref={inputRef}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="decommissioned in DEP-812"
          />
          <p className="hint">
            Recorded in the history against {operator}. Required — a decision
            with no reason is one nobody can review later.
          </p>
        </div>

        {error ? (
          <div className="notice" role="alert">
            <span className="tag">Refused</span>
            <p>{error}</p>
          </div>
        ) : null}

        <div className="actions">
          <button onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className="primary"
            onClick={() => onConfirm(reason.trim())}
            disabled={busy || reason.trim().length === 0}
          >
            {busy ? "Saving…" : TRANSITION_LABEL[to]}
          </button>
        </div>
      </div>
    </div>
  );
}
