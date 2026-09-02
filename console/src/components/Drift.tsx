import { useState } from "react";

import type { DriftResponse } from "../api/types";

/**
 * How this agent compares with itself.
 *
 * Separate from the evidence, which says why the workload is an agent at all.
 * This says what changed about one we already knew, which is a different
 * question with a different audience: the evidence is for arguing with the
 * classifier, and this is for asking the workload's owner.
 *
 * Nothing is shown unless the baseline is established. Drift measured against
 * two observations is noise, and rendering it under a heading would give that
 * noise the same standing as a finding.
 */
export function Drift({
  agentId,
  load,
}: {
  agentId: string;
  load: (agentId: string) => Promise<DriftResponse>;
}) {
  const [state, setState] = useState<"idle" | "loading" | "ready" | "failed">("idle");
  const [result, setResult] = useState<DriftResponse | null>(null);
  const [error, setError] = useState("");

  async function open() {
    if (state !== "idle") return;
    setState("loading");
    try {
      setResult(await load(agentId));
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
      <summary>Behaviour</summary>
      {state === "loading" ? <p className="muted">Loading…</p> : null}
      {state === "failed" ? (
        <p className="muted" role="alert">
          Could not load the baseline: {error}
        </p>
      ) : null}
      {result ? <Body result={result} /> : null}
    </details>
  );
}

function Body({ result }: { result: DriftResponse }) {
  if (!result.baseline.established) {
    return (
      <p className="muted">
        Not enough history yet — {result.observations}{" "}
        {result.observations === 1 ? "observation" : "observations"}. Drift
        measured against this would be noise.
      </p>
    );
  }

  if (result.drift.length === 0) {
    return (
      <p className="muted">
        Behaving as it has been, across {result.baseline.observations}{" "}
        observations.
      </p>
    );
  }

  return (
    <ul className="drift">
      {result.drift.map((item) => (
        <li key={`${item.kind}-${item.observed_at}`}>
          <span className="kind">{item.kind.replace(/_/g, " ")}</span>
          <span className="question">{item.question}</span>
        </li>
      ))}
    </ul>
  );
}
