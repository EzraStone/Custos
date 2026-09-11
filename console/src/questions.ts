/**
 * What identifies one gateway question.
 *
 * The address is not enough. 10.0.7.40 in us-east-1 and 10.0.7.40 in eu-west-1
 * are two different hosts, two separate questions, and two separate answers —
 * which is the whole reason declarations are scoped to a region. Keying on the
 * address alone gave the two rows the same React key and put both into
 * "Recording…" when one of them was answered.
 *
 * Private addresses are exactly the ones this matters for, and exactly the ones
 * this mechanism exists to ask about: 10.0.7.40 is a plausible address in every
 * region an account runs in, so the collision is likely rather than contrived.
 *
 * Its own module rather than an export from the component file, because a file
 * that exports both a component and a helper breaks fast refresh.
 */
import type { GatewayCandidate } from "./api/types";

export function questionKey(candidate: GatewayCandidate): string {
  return `${candidate.address} ${candidate.region}`;
}
