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
| Agent behind a self-hosted gateway | agent | **review — missed** | 0.77 |

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
goes to a private address the catalogue does not know. `catalog.extend` recovers
it completely. This is the strongest argument for asking a customer directly
whether they front their providers behind a gateway, because we cannot infer it.

**The margin number is the one to carry into diligence.** 0.14 is below the 0.15
durability bar this experiment set for itself. G0 is not retroactively failed —
it was defined and measured against the base corpus — but the honest reading is
that headroom on realistic traffic is roughly half what the clean corpus
suggested, and the first real capture will eat into it.

The weights were not retuned to widen it. They were already fitted on synthetic
traffic; fitting them again on more synthetic traffic would improve the metric
and nothing else.

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
