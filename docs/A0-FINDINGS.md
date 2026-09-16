# A0 findings

**Gate G0: PASS.** Separation margin 0.26, full recall, zero false positives,
identical at both flow log aggregation intervals.

Reproduce with `make experiment`. CI fails the build if this stops holding.

---

## What was asked

> Does the burst signature separate an agent from a chatbot using flow logs
> alone?

## What was actually tested

A strictly harder question, because the original one cannot be answered
honestly without it:

> Does an agent separate from a chatbot using flow logs alone, **after those
> flow logs have been degraded by aggregation and TLS connection reuse the way
> real VPC Flow Logs degrade them**?

The specification budgets two weeks and an AWS test environment for G0. This
answered the harder version in software first, which means the AWS environment
gets built to *confirm* a result rather than to search for one — and if the
result had been a fail, that would have been known in a day rather than a
fortnight.

## Result

| Configuration | Recall | Precision | Margin | Flow records |
|---|---|---|---|---|
| 60s aggregation, with ALB logs | 1.00 | 1.00 | +0.260 | 33,042 |
| 600s aggregation, with ALB logs | 1.00 | 1.00 | +0.260 | 16,240 |
| 60s aggregation, no ALB logs | 0.60 | 1.00 | +0.293 | 33,042 |
| 600s aggregation, no ALB logs | 0.60 | 1.00 | +0.290 | 16,240 |

Every agent scores above 0.95. Every clear negative scores below 0.31. The two
deliberately ambiguous workloads land at 0.52 and 0.69, inside the review band,
which is where SEC-17 requires them to be.

---

## Finding 1 — the specification's headline signal does not exist

The specification rates this **Strong**:

> Burst of sequential model calls, monotonically growing payload size,
> sub-second gaps — source: flow log timing + byte counts.

It is not implementable. Two facts compose to destroy it:

1. VPC Flow Logs aggregate per 5-tuple over a fixed interval — 60 seconds by
   default, 600 in the cheaper configuration a cost-conscious platform team
   will already have set.
2. Every model provider SDK pools TCP connections with a keep-alive around 90
   seconds.

So twenty sequential model calls over one pooled connection inside one
aggregation window produce **one** flow record with the byte counts summed and
the timing gone. Measured on the corpus's busiest agent at a 60-second
interval: **fewer than 0.5 flow records per model call.** The individual calls
are not in the data at any interval a customer will actually have configured.

This is the single most important thing A0 established, and it would have
consumed most of the two-week budget to discover by capture.

## Finding 2 — the signal that replaces it is better

Cumulative **egress-to-ingress asymmetry** against model endpoints.

An agent resends its entire accumulated transcript on every step. Over an
episode of n steps, bytes sent grow as O(n²) while bytes received grow as O(n).
A chatbot sends one prompt and receives one answer, so both grow linearly with
a roughly constant ratio.

Measured across the corpus, at both intervals:

| Workload | Ratio | Truth |
|---|---|---|
| autofix-coding-agent | 34.5 | agent |
| nightly-ops-agent | 15.8 | agent |
| support-triage-agent | 13.5 | agent |
| inventory-reconciler | 8.9 | agent |
| finance-close-agent | 7.9 | agent |
| nightly-doc-summariser | 6.5 | not agent |
| sales-copilot-web | 4.7 | not agent |
| ci-test-generator | 3.0 | not agent |
| docs-chat-backend | 1.7 | not agent |
| kb-assistant | 0.8 | not agent |
| search-embedder | 0.1 | not agent |

Clean separation at 7.9 against 6.5. Narrow enough that it is weighted
alongside other signals rather than used as a threshold, but it is a property
of **summed bytes**, so aggregation cannot touch it. That is why the result is
identical at 60s and 600s.

## Finding 3 — the classifier is invariant to aggregation interval

Identical recall, precision, and margin at 60 and 600 seconds.

This is a commercial result more than a technical one. If accuracy had depended
on 60-second aggregation, onboarding would include "reconfigure flow logs on
your production VPCs" — a change request against production, a week of delay
per customer, and a reason for a platform lead to say no. It does not.

## Finding 4 — what load balancer logs are worth, quantified

Without ALB access logs, recall falls from 100% to 60%. Precision stays at
100%, and both missed agents land in the review band rather than being dropped.

The two that degrade are the low-volume and short-episode agents — including
`finance-close-agent`, which runs three times a day and holds write access to
billing. **The agent that matters most is the one that degrades first.**

That gives the onboarding ask a precise justification: *"Also give us load
balancer access logs. Without them your most dangerous agent shows up as a
maybe instead of a finding."*

## Finding 5 — two expected signals were measured and rejected

Both are recorded in `controlplane/custos/classify/signals.py` under `REJECTED`
with the numbers that killed them, because both will be proposed again by
someone who has read the spec but not the data.

**Context growth** — the specification's "monotonically growing payload size".
Rejected twice over. It does not survive 600-second aggregation at all (every
workload measures ~1.0). And where it *is* measurable, at 60 seconds, it fires
hardest on the hardest negative: the multi-turn chatbot scores **5.67, above
every genuine agent**, because a multi-turn conversation accumulates context
exactly the way an agent trajectory does. Using it would have cost precision on
the workload type customers have most of.

**Episode persistence** — length of a run of model-active intervals. At 600
seconds it degenerates into a proxy for call volume: the embedding service runs
longer than every agent in the corpus. A signal that inverts under a
configuration the customer chooses is worse than no signal.

## Finding 6 — call volume carries no signal at all

The highest-volume workload in the corpus is a negative (13,606 model calls,
an embedding service). The lowest-volume agent makes 55. Any classifier that
ranks by activity finds the agents nobody was worried about and misses the one
they should be.

## Finding 7 — a harder corpus halves the margin

Added after the original result, because every workload in the base corpus is
either fully coupled or fully decoupled and real accounts are not that tidy.
Four workloads test that assumption directly:

| Workload | Truth | Verdict | Confidence |
|---|---|---|---|
| Agent that pauses for human approval | agent | agent | 0.83 |
| Agent running on a batch schedule | agent | agent | 0.96 |
| Chatbot with function calling | not agent | not agent | 0.34 |
| Agent behind a self-hosted gateway | agent | **not scored — missed** | 0.17 |

**Every verdict is correct once the gateway is declared, and there are no false
positives. The separation margin falls from 0.26 to 0.14.**

Three things follow.

**Partial coupling costs confidence, which is the signal working.** The
approval agent scores 0.83 rather than 0.99 because its human approval is a
real inbound request. It still clears the bar, but with a third of the headroom
— and that shape is common in exactly the workflows customers most want
governed.

**Schedule carries no signal, which was worth confirming.** An agentic batch
job and a non-agentic one run at the same hour with the same volume, and the
classifier separates them on what they do rather than when they do it.

**An unrecognised model endpoint is an invisible agent, and no amount of
classifier tuning fixes it.** The gateway agent is missed because its traffic
goes to a private address the catalogue does not know. Declaring the endpoint
recovers it completely. This is the strongest argument for asking a customer
directly whether they front their providers behind a gateway, because we cannot
infer it.

**That workload scored 0.77 until it was looked at closely, and the 0.77 was
made of nothing.** Four of the five signals are ratios and fractions over the
intervals containing model traffic. This workload has none, and over an empty
set they do not read as neutral: `1 - inbound_coupling` evaluates to 1.0, so
the heaviest signal in the system fired at full weight and printed "100% of the
intervals containing model traffic had no request arriving at the load
balancer" about a workload with no such intervals.

Signals with nothing to measure are now unavailable rather than zero, which was
already the rule for load balancer logs. The workload scores 0.17 and is not
scored in any meaningful sense. That is the correct answer: the entire evidence
base is model traffic, and a workload we cannot see making model calls is an
unanswered question rather than a low-confidence finding. It surfaces as a
question instead, in the report's Questions section, which names the undeclared
address and the workloads reaching it.

This is the third time a measurement here has been wrong in the flattering
direction. It will not be the last.

**The margin number is the one to carry into diligence.** It is measured with
the gateway declared, because in the undeclared case that agent is not scored
at all rather than mis-scored, and a margin computed across an invisible
workload measures nothing. 0.14 is below the 0.15
durability bar this experiment set for itself. G0 is not retroactively failed —
it was defined and measured against the base corpus — but the honest reading is
that headroom on realistic traffic is roughly half what the clean corpus
suggested, and the first real capture will eat into it.

The weights were not retuned to widen it. They were already fitted on synthetic
traffic; fitting them again on more synthetic traffic would improve the metric
and nothing else.

## Finding 8 — the gateway questions were measured and were worthless

The detector that asks a customer "is this internal address your model
gateway?" had no number attached to it, and the result quoted in its place —
that it asks nothing at all on the base corpus and finds the real gateway
first on the stress corpus — was a statement about the corpus. Neither corpus
contained a single internal destination that ordinary infrastructure floods,
so there was no opportunity to ask a bad question.

Seven were added: a log collector, a backup service, a metrics pushgateway, an
artifact registry, an event proxy, a thumbnailer and a document extractor.
Every one is a real thing a real account runs and every one has the shape the
detector looks for — private address, a great deal of egress, an
acknowledgement coming back.

**The detector asked nine questions, showed five, and none of the five was the
gateway. It ranked eighth.**

| | address | egress | ratio | answer |
|---|---|---|---|---|
| 1 | backup service | 4406MB | 57:1 | no |
| 2 | thumbnailer | 1604MB | 5.7:1 | no |
| 3 | artifact registry | 867MB | 53:1 | no |
| 4 | document extractor | 704MB | 7.8:1 | no |
| 5 | log collector | 548MB | 34:1 | no |
| … | | | | |
| 8 | **the gateway** | **54MB** | **4.3:1** | **yes** |

The ranking rule — most blind workloads first, then volume — was written
against a corpus where the gateway was the only internal destination with that
shape, so nothing could outrank it. That is not a property of gateways. It is
a property of a corpus with nothing else in it.

**Neither volume nor ratio separates them.** The gateway is the *smallest*
thing on the list, and its ratio sits between the thumbnailer's and the
document extractor's. Any threshold on either number that excludes a backup
service also excludes the gateway.

**What separates them is the loop.** An agent behind a gateway calls the model,
calls a tool, and calls the model again with the result. A log shipper, a
backup agent, a metrics pusher and an artifact publisher each talk to exactly
one thing, forever. Measured as the fraction of a blind workload's windows at a
destination that also reached something else, it is 0.00 for every bulk sender
and 0.98 for the real gateway — a threshold in empty space rather than between
two adjacent points.

It is not sufficient on its own, and the corpus contains the proof: a
thumbnailer fetches an object, transforms it and writes the result back, which
interleaves exactly like a tool loop. What excludes those is the ratio test, on
a window that fetches as much as it sends. Neither test alone is enough.

**With both: two questions, precision 0.50, the gateway first.** The second
question is the agent's own deploy API, which is reached in the same loop — and
nothing on the wire says which of two addresses in a loop is the model. That is
a fair question rather than a failure.

Three limitations, all of them real:

- **A gateway that also proxies its workload's tool calls is excluded by this.**
  It would be the only destination that workload reaches. The count of
  destinations declined for that reason is carried into the report, because a
  silent filter in front of the questions makes a clean-looking empty report
  easier to produce, which is the thing the questions exist to prevent.
- **A workload whose tool results are as large as its transcripts is masked**,
  because the ratio test runs on window totals rather than per destination. The
  wire does not carry bytes per destination. This is what removed the
  thumbnailer, and it would remove an agent with the same profile.
- **Seven synthetic services are not an account.** They were chosen to be the
  common cases, and the honest claim is that the detector now survives the
  obvious ones rather than that it survives a real estate.

One thing the noise is *not*: classifier stress. Every one of these workloads
scores at the floor — 0.039 against the agents' 0.95 and up — and they were
checked rather than assumed, because two of the three properties the
decoupling signal reads as agent-shaped are true of them: no inbound requests,
machine-triggered bursts. What saves it is that they make no model call at
all, so the signals that would carry them have nothing to measure. That is the
classifier being right for the right reason rather than by luck, and the
assertion is in `a0/tests/test_questions.py` so that if it stops being true the
number moves there first.

G0 is unchanged by their existence: 0.260 on the base corpus and 0.142 on the
stress corpus, both still reproduced by `make experiment` and `make stress`.
The noise is off by default for exactly that reason — folding non-agents that
were never in question into the negative set would improve precision on paper
and nothing in reality.

Reproduce with `make questions`. CI prints it on every build and
`a0/tests/test_questions.py` fails the build if the gateway stops ranking
first.

---

## Finding 9 — the same blindness as a hidden gateway, and AWS knew the answer

An interface VPC endpoint puts an ENI in the customer's own subnet, so an
account reaching Bedrock through one sends every model call to a private
address inside its own VPC. The flow log's `pkt-dst-aws-service` annotation
covers AWS's published address ranges and a VPC endpoint ENI is not in one, so
the record says nothing at all. On the wire it is an internal API on 443.

A corpus workload was added for it. **It scored 0.039 — the floor. Completely
invisible**, and worse than the self-hosted-gateway agent at 0.168, which at
least has more tools to leave a trace with.

This matters more than the count suggests, for two reasons.

**It is the default for the customer this product is sold to.** PrivateLink is
what a security-conscious platform team turns on so that model traffic does not
cross the public internet. The account most likely to have it is the account
most likely to buy a tool for governing agents — and it is the account where a
scan finds nothing and says so confidently.

**Nobody has to be asked.** A self-hosted gateway is something only the
customer knows about, which is why the declaration mechanism exists. A VPC
endpoint for `com.amazonaws.<region>.bedrock-runtime` is something AWS knows
about and will state, in one read-only call, about an ENI the account's own
traffic already reached. Asking a customer to declare it is asking them for an
answer we could have looked up.

**After the lookup: 0.039 to 0.970, with no declaration.** The collector calls
DescribeVpcEndpoints for the endpoints its own traffic pointed at, ships the
service name alongside the address, and the classifier treats the last segment
— `bedrock-runtime`, `bedrock-agent-runtime`, `sagemaker-runtime` — as model
inference. Narrow on purpose, the same way `MODEL_RANGES` is: `bedrock` without
the suffix is the control plane and is not inference, and an endpoint for
`com.amazonaws.<region>.s3` is traffic to S3.

Three things came out of it that were not the point:

- **The question metric improved by asking less.** It was 2 questions and 1
  worth asking; adding the endpoint made it 3 and 2; resolving it made it 2 and
  1 again. The best outcome for a question is not having to ask it, and a test
  now asserts this one is not asked — so a regression that stops resolving it
  and starts asking instead scores as the near miss it is.
- **Bedrock traffic had no provider at all.** `provider_for` decides `bedrock`
  from the flow log's service annotation, and the one caller passed only the
  address, so three of eight corpus agents were attributed to nobody and priced
  at a fallback rate. The dollar figures do not move on the built-in table —
  it prices bedrock and unknown identically — but an account that supplied its
  own Bedrock rate was not getting it applied to the agents using Bedrock.
- **The report has to say which endpoints were resolved.** Without that line,
  an account with a resolved endpoint produces a report word for word identical
  to one about an account with a hidden gateway and nothing found.

What is still true: this is one endpoint service family. An account reaching a
model provider through a PrivateLink service somebody else published —
`com.amazonaws.vpce.<region>.vpce-svc-...` — is exactly as invisible as before,
and AWS will not say what is behind it either. That one is still a question for
a human.

---

## Finding 10 — most of "a question for a human" was three questions, and two had answers

Finding 9 closed with the sentence above, and it was the right sentence to a
question nobody had taken apart. Taking it apart, `com.amazonaws.vpce.<region>.
vpce-svc-0a1b2c3d` is not one case. It is three, and only one of them needs a
person.

**The customer named it.** A VPC endpoint is the customer's own resource, and
their Terraform very often calls it what it is. That tag comes back on the
DescribeVpcEndpoints response the collector was already making, in the same
object as the service name, and was being dropped — the resolver read one field
and discarded the rest of the answer. Every other naming source in the
collector puts a label the customer chose ahead of anything we look up, and
this one did not, because nobody had looked at the whole response.

**The publisher named it.** A service published for other accounts to consume
carries a private DNS name its publisher configured, and for a model provider
selling into AWS that is their own API hostname. It costs one further read,
`DescribeVpcEndpointServices`, made only for published services the customer
has not already named — so an account whose endpoints are all AWS's own makes
no extra call at all.

**Nobody named it.** No tag, no private DNS name. This is the residue, and it
is a question for a human exactly as Finding 9 said.

The measured effect is on the question metric rather than on the classifier,
which is the right place for it. A corpus workload was added — an agent whose
model calls go over PrivateLink to a service another account published — and
the detector asks about it, ranks it above the self-hosted gateway, and
precision goes **0.50 to 0.67**. Both of the corpus's askable model endpoints
are now in the list a customer is shown, which is a property a precision figure
cannot see: a detector that asks one honest question and misses the other
scores 1.00.

Ranked first because of what it is rather than how loud it is. Every other
candidate is a machine inside the account and somebody can walk over and look
at it. This one is a door into another company's network, sold to the customer
as a service, carrying a transcript-shaped stream — and nothing else in an
account has that shape.

**The exception that was not made.** A published endpoint whose workloads reach
nothing else is declined like any other candidate. It is better evidence and it
is still not evidence: a workload with one destination has no tools to act
through and is not what this product means by an agent. Being sold by another
company does not change that, and an exception carved out here is how a
mechanism that must never classify anything starts classifying.

Two things fell out of it that were not the point:

- **The readability gate could not see a harder interface.** `CONTRIBUTING.md`
  says that when the estate reaches 100% the thing to do is add harder shapes.
  Doing that was a no-op against the gate as written, which counted successes:
  a nameable interface nothing could name left the success count where it was
  and moved only a percentage no assertion read. The gate counts the question
  now, and every nameable interface has to be named by name.
- **A field can drift between the two reports one row at a time.** The existing
  meta-test compares whole sections and the caveats on `Coverage`. It said
  nothing about the fields inside a row, which is the same failure one level
  down and fails just as silently.

---

---

## Finding 11 — the corpus modelled a streamed response as four bytes a token

Every dollar figure this product prints comes from one conversion: observed
wire bytes divided by a constant, priced per million tokens. The constant has
been four since A0, and `docs/STATUS.md` has called it a guess "wrong in a
direction nobody has measured" ever since spend attribution landed.

It is wrong by forty-four times, for any account whose model clients stream.

**The arithmetic.** A streaming model API flushes after every token — that is
the entire reason to stream, so the next token reaches the client without
waiting for the rest. Each token therefore becomes its own Server-Sent Events
frame, its own TLS record and its own TCP segment:

| | bytes |
|---|---|
| the token | 4 |
| SSE envelope | 114 |
| TLS record header | 29 |
| IP and TCP headers | 40 |
| **per output token** | **187** |

The envelope is counted from the smaller of the two shapes a provider actually
writes, Anthropic's `content_block_delta`. OpenAI's `chat.completion.chunk`
carries a request id, a model name, a system fingerprint and a choices array on
every token and comes to roughly double.

This is not a measurement of anything. It is arithmetic on a documented
protocol, and the only reason it had not been done is that nobody had asked
what a flow record's ingress bytes are made of.

**It lands on the expensive side.** Output tokens are priced at five times
input on every provider in the table. Dividing a streamed response by four
does not inflate a cost estimate slightly; it can put a report's headline
figure an order of magnitude high, for the agent whose figure is most likely to
be acted on.

**A flow record does not say which.** That is why this has been one constant
for as long as it has existed: two answers forty-four times apart with nothing
to choose on. It does carry packet counts, and those turn out to be enough.

Subtract the acknowledgements the outbound segments imply — one per two, at 52
bytes — and the mean inbound data packet is:

| | mean inbound data packet |
|---|---|
| responses arriving whole | 1,444 – 2,740 bytes |
| responses streamed | 232 – 288 bytes |

Nothing between. The gap is a property of the protocol rather than of the
corpus: a sender filling segments produces packets near the MSS, and a sender
flushing per token produces packets the size of one SSE frame. The threshold
sits at 600, in measured empty space, the same rule the classifier's own
thresholds follow. `make conversion` reproduces it, and the discriminator reads
every workload in both captures correctly.

Subtracting the ACKs is the part that makes it work, and it is not an
optimisation. An agent resends its accumulated transcript, so it sends far more
segments than it receives responses; enough acknowledgements of its own traffic
drag its raw inbound average to 84 bytes, and it reads as streaming whatever
its responses actually did.

**What it does to the classifier is the more interesting half.** Streaming
inflates the response side of every model call, which is the denominator of the
signal the whole product reads. The separation margin goes *up*, 0.260 to
0.371, because the negatives lose more confidence than the agents do.

That result is the reason this arc grew a second gate. The margin is a gap
between two classes and says nothing about where the gap sits, and in the same
run the weakest agent went from 0.151 above the reporting threshold to 0.054
above it. A shift that widened the margin and pushed an agent under 0.80 would
read as an improvement in every number recorded here and would be a row missing
from a customer's report. G0 now requires both.

**What is unmeasured.** The mixture. How much real agent traffic streams is a
question about customers rather than about protocols — a framework that acts on
a whole reply has no reason to stream, one behind a user interface does — so
the corpus can be built either way and neither is asserted.

And a workload doing both is read as neither. `kb-assistant` mixes streamed
completions with whole embedding responses and lands at 24.5 bytes per output
token, between the two constants and fitting neither. Nothing in a flow record
separates two conversations with the same peer below the level of a principal,
so that one is a stated limit rather than a bug.

The tokenisation itself is still four bytes a token, still right for English
JSON, still wrong for code, and still unmeasured. That part needs a tokeniser
and a corpus of real prompts, which is a different afternoon.

**Superseded in part by Finding 12.** Everything above is what was measured and
it still holds, but the conclusion drawn from it was too small. The correction
was applied to the spend estimate, on the reasoning that the dollar figure was
what depended on the conversion. The next thing anybody asked was whether the
*classifier* depended on it, and it did — far more.

---

## Finding 12 — the signal that carries the product was measuring the protocol

Finding 3 established that the classifier is invariant to the flow log
aggregation interval, and that is the property that made A0 a result rather
than a demo: a customer on 600-second aggregation gets the same verdicts as one
on 60. Streaming is the same kind of question about a customer's configuration,
and nobody had asked it.

**Four of five agents invert.** On a streamed capture of the same corpus, the
egress-to-ingress ratio of a confirmed agent falls from 15.8:1 to 0.76:1. Below
one, which is the shape of a chatbot answering questions — and that sentence is
printed as the evidence beside the finding, in the one section of the report a
workload's owner is invited to argue with.

| workload | whole | streamed |
|---|---|---|
| autofix-coding-agent | 34.5:1 | 2.19:1 |
| nightly-ops-agent | 15.8:1 | 0.76:1 |
| support-triage-agent | 13.5:1 | 0.67:1 |
| inventory-reconciler | 8.9:1 | 0.48:1 |

The verdicts survived, because four other signals carry them. That is the
uncomfortable part rather than the reassuring one: the signal the specification
was rewritten around, the one that replaced the burst timing that does not
exist, was doing almost nothing on a capture from an account that streams — and
every number recorded in this document would have looked fine.

**What was in the byte count.** Four things occupy the inbound side of a model
conversation: the payload, one acknowledgement per two outbound segments at 52
bytes each, a TLS server hello and certificate chain per connection, and — when
the response streams — an SSE frame, a TLS record header and a packet header
per token. Remove the first three and the inbound payload rate is 4.1 to 4.2
bytes per token; the fourth multiplies it by 41.9.

The outbound side needed correcting too, which is easy to miss. Streaming
multiplies the inbound packet count by forty-two and every two of those packets
are answered by an acknowledgement travelling *outbound*, so the numerator of
the ratio grows as well — by 6% on a workload that sends a great deal and 57%
on one that does not. That error points the same way as the thing being
measured, which is what makes it the kind that survives review.

**The fix is where the wire is decoded, not where it is used.** Model byte
counts become payload when the telemetry is built. The classifier reads a fact
about the conversation; the spend estimate reads the same numbers and its
per-regime constant, its framing haircut and its discriminator all deleted
themselves — four bytes a token was never wrong, it was being applied to wire
bytes.

**It was not only about streaming.** The acknowledgements and the certificate
chains were in the ratio of every account ever scanned, and they scale with how
a client is configured rather than with what a workload said. Taking them out
moved the numbers G0 rests on:

| | before | after |
|---|---|---|
| base corpus margin | 0.260 | **0.415** |
| stress corpus margin | 0.142 | **0.291** |
| headroom above the reporting threshold | 0.151 | 0.168 |
| streamed capture margin | 0.371 | 0.418 |
| streamed capture headroom | 0.054 | 0.168 |

Recall and precision are 1.00 throughout and no workload changed side. The
midpoint of the ratio signal moves from 7.0 to 11.5 because the feature is on a
different scale — agents above 15.3 and negatives below 8.7, where it was 7.9
and 6.5. The gap between the classes goes from 1.2x to 2x.

That gap being narrow was the stated reason this signal is weighted alongside
others rather than used as a threshold. The reason still holds for a different
cause: eleven workloads do not justify a threshold however wide the gap looks.

**What is still unmeasured.** How much real agent traffic streams — a question
about customers, not protocols, so the corpus is built both ways and neither is
asserted.

**And the limit that was not one.** This finding first recorded that a workload
running both regimes is read as neither: `kb-assistant` embeds a query whole
and streams the answer, and the decision was being made per principal, so it
got one answer for both halves.

They are not the same conversation. The embedding goes to one endpoint and the
completion to another, a flow log is keyed on the 5-tuple, and the peer address
is the finest grain the data supports — finer than the principal. Deciding
there costs nothing and removes the limit: `kb-assistant` reads Bedrock whole
at 1,739 bytes per data packet and Anthropic streamed at 241, and the
conversion measurement is one row per conversation with nothing excluded from
its range.

Worth recording that the limit was stated confidently and was wrong. "A flow
log cannot separate two conversations with the same peer" is true and was not
the situation.

---

## Why this result should be believed, and where it should not

**The corpus is adversarial by construction.** Four negatives exist
specifically to defeat naive signals: a multi-turn chatbot that accumulates
context like an agent, a RAG assistant that interleaves tool calls like an
agent, a batch job with no inbound requests, and a CI pipeline producing exactly
the "burst of sequential model calls" the specification leads with. Tests in
`a0/tests/test_corpus.py` assert those properties hold, so the corpus cannot
quietly become easy.

**The classifier cannot cheat.** Feature extraction is structurally forbidden
from reading principal names — enforced by parsing the module source, not by
convention. Real IAM roles are often named `role/something-agent`, and a
classifier reading that would score well here while learning nothing.

**The weights were fitted on this corpus.** That is the honest limitation. What
A0 establishes is that *a separating signal exists and survives aggregation*,
and which features carry it. It does not establish that these specific weights
generalise. The first real capture will move them, and the thresholds sit in
measured empty space rather than at round numbers so there is room for that.

**The margin is thinner than the headline suggests.** 0.26 on the base corpus,
0.14 once workloads with partial coupling are included. Quote the second number
in any conversation where the first would be doing work.

**Synthetic traffic embeds assumptions.** The byte model assumes ~4 bytes per
token, standard MTU, and typical SDK pooling. Those are defensible and they are
still assumptions. The AWS test environment is still worth building — its job is
now to confirm the byte ratios hold on real provider endpoints, which is a
one-week task against a stated prediction rather than a two-week search.

---

## What this changes in the specification

| § | Change |
|---|---|
| 4.1 | Signal table rewritten. Burst timing drops from Strong to not implementable. Egress asymmetry added as the primary signal. |
| 4.1 | G0 reframed: the AWS environment confirms a prediction rather than searching for a signal. Budget drops from two weeks to about one. |
| 4.3 | ALB access log ingestion moves from optional to strongly recommended, with the recall number to justify it. |
| 8 | A0 shortens. A1 starts with a working classifier and a labelled regression corpus rather than from nothing. |
| 11 | The end-of-A0 kill gate is resolved. Gateway-log fallback is not needed as a primary path. |
