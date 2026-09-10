import { useState } from "react";

import type { GatewayCandidate } from "../api/types";

/**
 * The question the product cannot answer for itself.
 *
 * A workload whose model calls go through an internal gateway has no model
 * traffic as far as we can see, so it is not a finding, not a review
 * candidate, not anything. `docs/STATUS.md` has called that the single most
 * likely reason a real scan comes back emptier than it should since A0.
 *
 * This sits above the register rather than inside it, because it is not a
 * finding. It is a claim about our own blindness, and the honest place for
 * that is in front of the list it might be missing from — not in a settings
 * page somebody visits once.
 *
 * Answering "no" is not offered. A dismissal we stored would be a
 * configuration that suppresses a question, and the same reasoning that keeps
 * declarations additive applies: an operator who is sure this is an ordinary
 * internal API can simply not act, and next week's scan will ask again with
 * that week's numbers. Being asked twice about something harmless costs a
 * glance. Being asked never about a real gateway costs the whole account.
 */
export function Gateways({
  candidates,
  operator,
  busy,
  error,
  onDeclare,
}: {
  candidates: GatewayCandidate[];
  operator: string | null;
  busy: string | null;
  error: string | null;
  onDeclare: (candidate: GatewayCandidate, note: string) => void;
}) {
  if (candidates.length === 0) return null;

  return (
    <section className="gateways">
      <h2>Is one of these a model gateway?</h2>
      <p className="lede">
        {candidates.length === 1 ? "This address behaves" : "These addresses behave"}{" "}
        like a model endpoint: far more sent than received, by workloads that
        never reach a provider we recognise. If{" "}
        {candidates.length === 1 ? "it is" : "any of them are"} yours, saying so
        makes the agents behind{" "}
        {candidates.length === 1 ? "it" : "them"} visible on the next scan.
      </p>

      {error ? (
        <div className="notice" role="alert">
          <span className="tag">Could not record that</span>
          <p>{error}</p>
        </div>
      ) : null}

      <ul className="candidates">
        {candidates.map((candidate) => (
          <Candidate
            key={candidate.address}
            candidate={candidate}
            operator={operator}
            busy={busy === candidate.address}
            onDeclare={onDeclare}
          />
        ))}
      </ul>
    </section>
  );
}

function Candidate({
  candidate,
  operator,
  busy,
  onDeclare,
}: {
  candidate: GatewayCandidate;
  operator: string | null;
  busy: boolean;
  onDeclare: (candidate: GatewayCandidate, note: string) => void;
}) {
  const [note, setNote] = useState("");

  return (
    <li className="candidate">
      <p className="question">{candidate.question}</p>
      <p className="who">
        Reached by{" "}
        {candidate.blind_principals.map((p) => p.split("/").pop()).join(", ")}
        {/*
          Which region this is about, when the account is collected in more
          than one. Answering declares the address for that region only — the
          same address elsewhere is a different host — so the person clicking
          has to be able to see which one they are answering for.
        */}
        {candidate.region ? <span className="muted"> · {candidate.region}</span> : null}
      </p>
      <div className="actions">
        <label className="visually-hidden" htmlFor={`note-${candidate.address}`}>
          What this endpoint is called
        </label>
        <input
          id={`note-${candidate.address}`}
          value={note}
          placeholder="what you call it — llm-gateway, vllm, litellm"
          onChange={(event) => setNote(event.target.value)}
        />
        <button
          className="primary"
          disabled={!operator || busy}
          onClick={() => onDeclare(candidate, note.trim())}
        >
          {busy ? "Recording…" : "Yes, it is ours"}
        </button>
        <span className="hint">
          {operator
            ? `Recorded against ${operator}. Takes effect on the next scan.`
            : "Enter your name above to answer."}
        </span>
      </div>
    </li>
  );
}
