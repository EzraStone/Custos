# ADR 0003 — What a wire byte is worth in tokens

Status: superseded by ADR 0004
Date: 2026-09-14

> **Superseded.** The decision below — infer the regime from packet sizes, per
> principal, and say that we did — still stands and is still how this works.
> What changed is where it is applied. ADR 0004 moves the correction from the
> spend estimate to the telemetry, because the classifier turned out to depend
> on the same conversion far more heavily than the dollar figure did. The
> alternatives weighed here were weighed against the wrong problem.

## Context

The dollar figure beside an agent is the field that gets a Custos report
forwarded to somebody with a budget. "This unregistered agent costs about
$1,400 a month" reaches a different reader than "this unregistered agent
exists", and that is the whole argument for having the figure at all.

Every one of them comes from the same conversion. A flow log carries bytes; a
price list is per million tokens; a constant bridges them. That constant has
been four bytes per token in both directions since A0 — the usual rule of thumb
for English serialised into a messages array — and `docs/STATUS.md` has flagged
it as a guess wrong in a direction nobody had measured for as long as spend
attribution has existed.

It is wrong by around forty-four times for any account whose model clients
stream, and it is wrong on the expensive side.

A streaming model API flushes after every token. That is not an implementation
detail, it is the entire reason to stream: the next token reaches the client
without waiting for the rest. So each token becomes its own Server-Sent Events
frame, its own TLS record, and its own TCP segment — four bytes of text inside
about 183 bytes of protocol. Output tokens are priced at five times input on
every provider in the table, so a report could carry a headline figure an order
of magnitude high for the agent whose figure is most likely to be acted on.

Nothing in a flow record says whether a response streamed.

## Decision

**Infer it from packet sizes, per principal, and say that we did.**

Subtract the acknowledgements the outbound segments imply — one per two, at 52
bytes each — and take the mean size of what is left arriving inbound. On the A0
corpus that is 1,444 to 2,740 bytes when responses arrive whole and 232 to 288
bytes when they stream, with nothing between. The threshold sits at 600, in
measured empty space, which is the same rule the classifier's own thresholds
follow.

Three parts of that decision were not obvious and are worth recording.

**Subtracting the acknowledgements is the decision, not an optimisation.** An
agent resends its accumulated transcript at every step, so it sends far more
segments than it receives responses, and the acknowledgements of its own
traffic drag its raw inbound average to 84 bytes. Without the correction every
agent reads as streaming whatever its responses actually did — and agents are
the rows this product exists to price.

**Per principal rather than per account.** A company whose chatbot backend
fronts a user interface and whose agents wait for a whole reply before acting
is two regimes in one account. A principal is the finest grain a flow log
supports, so it is where the decision is made.

**Not streamed when it cannot tell.** No packet counts, or nothing left after
the acknowledgements, reads as whole. That is the answer this product has
always given, and it is the one that overstates a cost: a figure that is too
high gets questioned, and one that is too low gets believed.

## Alternatives considered

**Keep one constant and widen the caveat.** Cheapest, and it was the status
quo. Rejected because the caveat would have had to say the figure might be
forty-four times high, which is not an estimate — it is a number with no
information in it, and a reader who understood the caveat would stop reading
the column.

**Show a range instead of a figure.** Honest, and it destroys what the figure
is for. "$120 to $5,300 a month" does not get forwarded to anybody, and
ranking two agents against each other — the one thing this number is genuinely
good for — becomes impossible when their ranges overlap.

**Ask the customer.** They know which of their clients stream, and this is the
same move as the model gateway declaration, which works. Rejected as the
primary answer for the reason the endpoint lookups in ADR 0002 were preferred
over declarations: asking somebody for an answer we can derive is a worse
product. It remains the escape hatch, and the report says the reading was ours
so they know to correct it.

**Count tokens properly.** Out of reach. It needs the payloads, and SEC-18
forbids collecting them — which is the constraint the whole product is built
inside rather than an obstacle to route around.

## Consequences

The spend figure for a streaming account falls by roughly an order of
magnitude, which is a correction rather than a regression, and no surface
renders the old number any more.

Two constants now live in `controlplane/custos/spend.py` that were measured in
`a0`, and `a0/tests/test_conversion.py` holds them against the corpus. That is
the pattern this repository keeps rediscovering: two places that have to agree
and nothing making them.

The classifier was measured under streaming as well, and the result changed the
gate. The separation margin goes *up* — the negatives lose more confidence than
the agents do — while every agent moves toward the reporting threshold. G0 now
requires headroom above that threshold as well as a margin between the classes,
because those two numbers moved in opposite directions in the same run.

What stays unmeasured is the mixture: how much real agent traffic streams is a
question about customers rather than about protocols. And a workload running
both regimes at once is read as neither — `kb-assistant` in the corpus mixes
streamed completions with whole embedding responses and lands between the two
constants, fitting neither. Nothing in a flow record separates two
conversations with the same peer below the level of a principal, so that is a
stated limit.
