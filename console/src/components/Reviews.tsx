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
 */
export function Reviews({ reviews }: { reviews: Review[] }) {
  if (reviews.length === 0) return null;

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

function short(principal: string): string {
  return principal.split("/").pop() ?? principal;
}
