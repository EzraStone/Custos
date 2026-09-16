import type { Review } from "../api/types";

/**
 * The maybes.
 *
 * SEC-17 keeps these out of the register, which is right — the classifier
 * saying "this might be an agent and I am not confident enough to say so" is
 * not a claim anything should act on. But a console that never showed them let
 * an operator believe the register was everything the scan had to say.
 *
 * There is nothing to click. No promote, no dismiss. Promoting a maybe by hand
 * is what the register is not for, and a dismissal would be configuration that
 * suppresses a classification. What an operator does with one of these is look
 * at the workload, and the useful thing this can give them is which one to
 * look at first.
 *
 * That is what `seen_in_scans` is for. One uncertain window is noise; the same
 * workload uncertain in eleven scans is a question the classifier has been
 * asking for a fortnight.
 *
 * A workload with no model traffic at all is not here and should not be: its
 * model-traffic signals are unavailable rather than zero, so it is not scored.
 * What surfaces those is the gateway question above the register, which names
 * the workloads reaching each undeclared address.
 *
 * `undecidable` is the third category and the one with no list at all. These
 * are workloads the classifier was *not* unsure about — it dismissed them,
 * confidently, and the confidence is not warranted. An assistant behind a chat
 * box and a retrieval-augmented chatbot produce the same flow log, so
 * dismissing them is the right default and being silent about having done it
 * is not.
 */
export function Reviews({
  reviews,
  undecidable = 0,
}: {
  reviews: Review[];
  /**
   * Workloads dismissed as chatbots that this scan could not tell apart from
   * an interactive agent.
   *
   * Rendered even when there are no reviews, which is the case it exists for.
   * "The classifier was sure about everything" and "the classifier was sure
   * about everything and three of those answers are not worth much" are
   * different claims, and a console that draws both as nothing at all makes
   * the second one unreadable.
   */
  undecidable?: number;
}) {
  if (reviews.length === 0) {
    return undecidable > 0 ? (
      <section className="reviews">
        <h2>Nothing the classifier was unsure about</h2>
        <Undecidable count={undecidable} />
      </section>
    ) : null;
  }

  return (
    <details className="reviews">
      <summary>
        {reviews.length} workload{reviews.length === 1 ? "" : "s"} the classifier
        was unsure about
      </summary>
      <p className="lede">
        Not confident enough to register as agents, not clearly ordinary either.
        They are never written to the register — this is the whole of what is
        known about them.
      </p>
      {undecidable > 0 ? <Undecidable count={undecidable} /> : null}
      <ul className="review-list">
        {reviews.map((review) => (
          <li key={review.principal}>
            <div className="head">
              <span className="name">{short(review.principal)}</span>
              <span className="confidence">{review.confidence.toFixed(2)}</span>
              {review.seen_in_scans > 1 ? (
                <span className="recurring">
                  in {review.seen_in_scans} scans
                </span>
              ) : null}
            </div>
            {review.unavailable.length > 0 ? (
              <p className="degraded">
                {/*
                  Worth saying rather than folding into the confidence. A
                  workload uncertain because we lacked input is a different
                  problem from one that is genuinely ambiguous, and only one
                  of them is fixed by sending us more data.
                */}
                Could not evaluate {review.unavailable.join(", ")} — this may be
                low confidence for want of input rather than because the
                workload is ambiguous.
              </p>
            ) : null}
            <ul className="evidence">
              {review.evidence.length > 0 ? (
                review.evidence.map((line) => <li key={line}>{line}</li>)
              ) : (
                <li className="muted">
                  Nothing scored high enough to state. That is why it is here.
                </li>
              )}
            </ul>
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * The workloads with no entry anywhere, and why there is no list.
 *
 * Naming them would be the obvious improvement and is the wrong one. The count
 * is a property of the scan; the names would read as an accusation, and the
 * overwhelming majority of workloads with this shape are ordinary chatbots.
 * What an operator can act on is knowing the shape exists in their account.
 */
function Undecidable({ count }: { count: number }) {
  return (
    <p className="muted">
      {count} {count === 1 ? "workload answers" : "workloads answer"} inbound
      requests and {count === 1 ? "calls" : "call"} internal services between
      model calls — the shape of an assistant behind a chat box or an editor.
      An agent of that kind and a retrieval-augmented chatbot are identical in
      everything a flow log shows, so {count === 1 ? "it is" : "they are"}{" "}
      neither here nor in the register. If{" "}
      {count === 1 ? "it runs" : "any of them runs"} a tool loop on its own
      judgement, {count === 1 ? "it is" : "that one is"} an agent and nothing
      above says so.
    </p>
  );
}

function short(principal: string): string {
  return principal.split("/").pop() ?? principal;
}
