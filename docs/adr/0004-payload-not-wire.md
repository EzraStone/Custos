# ADR 0004 — The classifier reads payload, not wire bytes

Status: accepted
Date: 2026-09-16

## Context

ADR 0003 decided how to tell a streamed model response from a whole one and
applied the answer to the spend estimate, on the reasoning that the dollar
figure was the thing the conversion decided.

That was the wrong scope, and one measurement showed it. The signal this whole
product rests on is `egress_asymmetry`: an agent resends its accumulated
transcript at every step, so it sends far more than it receives. On a streamed
capture of the same corpus, four of five confirmed agents come back with a
ratio below 1:1.

Below one means the workload received more than it sent, which is the shape of
a chatbot answering questions. That sentence is printed as the evidence beside
the finding, in the one section of the report a workload's owner is invited to
argue with.

The verdicts survived because four other signals carry them. That is worse than
it sounds rather than better: the signal the specification was rewritten
around — the one that replaced the burst timing that turned out not to be
implementable — was contributing almost nothing on a capture from an account
that streams, and nothing in any recorded number would have shown it.

Whether a client streams is a line in the customer's code. It is not a logging
choice onboarding can ask them to change, and it is not something the workload
means.

## Decision

**Model byte counts become payload bytes at the point the telemetry is built.**

The acknowledgements, the TLS handshakes and the streaming framing come out;
the wire figures are kept alongside for anything that quotes bytes back to a
customer. Everything downstream — the classifier, the spend estimate, the
baselines — reads a fact about the conversation.

Four parts of this were not obvious.

**The correction belongs where the wire is decoded.** Applying it in `spend.py`
meant every other consumer of the same numbers silently got the uncorrected
version. Moving it to `sessionize` deleted the per-regime constant, the framing
haircut and the discriminator from the spend module entirely: four bytes a
token was never wrong, it was being applied to the wrong input.

**Both directions.** Streaming multiplies the inbound packet count by forty-two
and every two of those packets are answered by an acknowledgement travelling
outbound, so the numerator of the ratio grows too — 6% on a workload that sends
a great deal, 57% on one that does not. That error points the same way as the
thing being measured, which is what makes it the kind that survives review.

**The regime is decided once, per principal, from the totals.** The first
version re-decided inside every window and left alone the ones that did not
look streamed on a handful of packets, which under-corrected an agent
threefold.

**The certificate chain had to come out too.** It does not scale with anything
the conversation says, so on a workload that opens a connection per call it is
most of the inbound byte count. Leaving it in made the payload rate look like a
constant that varied between 4.2 and 9.2 bytes per token. It is 4.1 to 4.2.

## Alternatives considered

**Normalise streamed captures back onto the non-streaming wire scale.** Tried
first, and the appeal was real: every threshold in the classifier was fitted
against wire bytes, so this changes no calibration and cannot disturb G0. It
was rejected after it worked, because preserving a scale whose units are
"payload plus however much protocol this customer's client happens to add" is
preserving the defect in a form that no longer announces itself.

**Leave the classifier alone and widen the caveat.** The verdicts were correct
on both captures, so there was a defensible case for recording the sensitivity
and moving on. Rejected because the verdicts being correct was luck about
weights: a corpus where the ratio mattered slightly more would have missed
agents, and the caveat would have been "this may not work on accounts that
stream", which is not a caveat anyone can act on.

**Make streaming a declared fact, like a model gateway.** The customer knows
which of their clients stream. Rejected for the reason ADR 0002 preferred
looking an endpoint up to asking about it: an answer we can derive should not
be a question. It remains the escape hatch, and every surface says the reading
was ours.

## Consequences

G0 moves, and this is a change to a business decision rather than a refactor.
The base corpus margin goes from 0.260 to 0.415 and the stress margin from
0.142 to 0.291, with recall and precision unchanged at 1.00 and no workload
changing side. The ratio midpoint moves from 7.0 to 11.5 because the feature is
on a different scale.

The gap between the classes widens from 1.2x to 2x, and that is not a streaming
result — the acknowledgements and certificate chains were in the ratio of every
account ever scanned.

Spend figures fall for every existing customer, by roughly the protocol
overhead on their traffic. The figures were too high before; the reports say
which reading produced them.

The regime is decided per destination address. It was per principal for one
commit, with a workload running both regimes written down as a limit of the
data — which it was not. The two halves go to different endpoints and a flow
log is keyed on the 5-tuple, so the peer address is the finest grain available
and it is finer than the principal.
