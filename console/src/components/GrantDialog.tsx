import { useEffect, useRef } from "react";

import type { Agent } from "../api/types";

/**
 * The confirmation before the one action that confers authority.
 *
 * It exists to show the scope. An operator approving an agent is approving what
 * it was seen doing, and "what it was seen doing" is a specific list of tools
 * and data stores that the person clicking should have read before the click,
 * not discovered afterwards in an audit trail.
 *
 * It also shows whose name goes on the record. The audit entry is permanent and
 * has no retention window, and someone sanctioning under a shared login should
 * find that out here rather than in a quarterly review.
 */

export interface GrantDialogProps {
  agent: Agent;
  operator: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function GrantDialog({
  agent,
  operator,
  busy,
  error,
  onConfirm,
  onCancel,
}: GrantDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  // Focus lands on cancel, not confirm. A dialog that opens with the
  // irreversible action under the return key is a dialog that gets past people
  // rather than in front of them.
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const tools = [...agent.tools].sort();
  const data = [...agent.data_stores].sort();
  const name = agent.principal.split("/").pop() ?? agent.principal;

  return (
    <div className="overlay" role="presentation" onClick={onCancel}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="grant-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="grant-title">Grant imprimatur to {name}?</h2>

        <p className="lede">
          This is the only action in Custos that confers authority. After it,
          this agent is a sanctioned entry rather than a finding, and the
          approval below is what it is sanctioned for.
        </p>

        <dl className="meta">
          <div>
            <dt>Approving as</dt>
            <dd className="mono">{operator}</dd>
          </div>
          <div>
            <dt>Blast radius</dt>
            <dd>{agent.blast_radius}</dd>
          </div>
        </dl>

        <div className="scope">
          <h3>Approved tools</h3>
          {tools.length > 0 ? (
            <ul>{tools.map((tool) => <li key={tool}>{tool}</li>)}</ul>
          ) : (
            <p className="empty">None observed.</p>
          )}

          <h3>Approved data stores</h3>
          {data.length > 0 ? (
            <ul>{data.map((store) => <li key={store}>{store}</li>)}</ul>
          ) : (
            <p className="empty">None observed.</p>
          )}
        </div>

        <p className="hint">
          The scope is what this agent was observed reaching. Widening it later
          is a separate, deliberate act.
        </p>

        {error ? (
          <div className="notice" role="alert">
            <span className="tag">Not granted</span>
            <p>{error}</p>
          </div>
        ) : null}

        <div className="actions">
          <button ref={cancelRef} onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="primary" onClick={onConfirm} disabled={busy}>
            {busy ? "Granting…" : `Grant as ${operator}`}
          </button>
        </div>
      </div>
    </div>
  );
}
