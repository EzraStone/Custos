import { useState } from "react";

import type { AuditEntry } from "../api/types";

/**
 * What has been done to this agent, and by whom.
 *
 * SEC-17 says authority is conferred only by a deliberate human act. That is
 * enforced in the store and recorded in the audit table, but a record nobody
 * can read is a record nobody can check. This is where an operator finds out
 * that the agent they are looking at was sanctioned last March by someone who
 * has since left.
 *
 * Loaded on open rather than with the register. Forty findings on a page would
 * otherwise be forty audit requests for history nobody asked to see, and the
 * history of an agent is not what the register is for.
 */
export function History({
  agentId,
  load,
}: {
  agentId: string;
  load: (agentId: string) => Promise<AuditEntry[]>;
}) {
  const [state, setState] = useState<"idle" | "loading" | "ready" | "failed">("idle");
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [error, setError] = useState("");

  async function open() {
    // Fetch once. Re-opening shows what was fetched; the refresh control at the
    // top of the page is how the operator asks for current data, and a section
    // that silently refetched on every toggle would be a different promise.
    if (state !== "idle") return;
    setState("loading");
    try {
      setEntries(await load(agentId));
      setState("ready");
    } catch (caught) {
      setError((caught as Error).message);
      setState("failed");
    }
  }

  return (
    <details
      onToggle={(event) => {
        if ((event.currentTarget as HTMLDetailsElement).open) void open();
      }}
    >
      <summary>History</summary>
      {state === "loading" ? <p className="muted">Loading…</p> : null}
      {state === "failed" ? (
        <p className="muted" role="alert">
          Could not load the history: {error}
        </p>
      ) : null}
      {state === "ready" && entries.length === 0 ? (
        <p className="muted">
          Nothing recorded. This agent has been discovered and not acted on.
        </p>
      ) : null}
      {entries.length > 0 ? (
        <ol className="history">
          {entries.map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <span className="when">{formatTime(entry.at)}</span>
              <span className="what">{entry.action}</span>
              <span className="who">{entry.actor || "—"}</span>
              {entry.detail ? <span className="why">{entry.detail}</span> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </details>
  );
}

function formatTime(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toISOString().slice(0, 16).replace("T", " ");
}
