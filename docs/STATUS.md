# Where this stands

A single page for someone picking this up cold — a design partner asking what
is real, or a second engineer arriving.

## What runs today

The full discovery loop, end to end.

```
Terraform apply (customer)  →  role ARN
custos-collector            →  batch.json          (dry run prints it first)
custos scan batch.json      →  report.html
custos diff                 →  what changed since last week
```

| Capability | State |
|---|---|
| Classify agents from flow log metadata | Works. G0 passed, margin 0.26 |
| Read flow logs from CloudWatch or S3 | Works |
| Read ALB access logs | Works. Four fields taken, the rest discarded at parse |
| Resolve interface → principal, EC2 | Works |
| Resolve interface → principal, Lambda / ECS | Works |
| Resolve interface → principal, EKS | Node level, or exact via CloudTrail for Bedrock traffic |
| Blast radius from IAM policy | Works |
| Attribution to a team | Works, four methods with stated confidence |
| Persistent register with SEC-17 state machine | Works |
| Scan comparison | Works |
| Behavioural baselines and drift | Works |
| HTTP API, container image, retention | Works |
| Delivery to Slack and SIEM | Works, with per-channel suppression |
| Scheduled collection | Works, cursor-tracked, no gaps on restart |
| Multi-account tokens | Works. One token covers a named set of accounts |
| Onboarding and preflight | Works. `custos onboard`, `custos-collector --check` |
| Enforcement checkpoint | **Not started.** §12: not before a paying customer |
| Operator console | Works. Read, filter, sanction, retire, and see what changed. Served by the control plane |
| Destination naming | Works where an ENI, an AWS description, or a port says what something is |
| Scope readability, measured | Works. Reported by `--check`, the report, the console, and `custos history` |

## The one number that matters

**G0 passed: 1.00 recall, 1.00 precision, 0.26 separation margin, identical at
60s and 600s flow log aggregation.**

Against a harder corpus added afterwards — agents that pause for human
approval, agents on batch schedules, chatbots with function calling — every
verdict is still correct and there are still no false positives, but the margin
falls to **0.14**. Quote that number, not the first one, wherever it would be
doing work.

Reproduce with `make experiment`. CI fails the build if it stops holding.

The finding underneath it is the interesting part: the signal the specification
leads with — burst timing and per-call payload growth — is not implementable,
because aggregation plus TLS connection reuse leaves under 0.5 flow records per
model call. What replaced it, cumulative egress-to-ingress asymmetry, is a
property of summed bytes and survives aggregation intact.

Full result and its limitations: `docs/A0-FINDINGS.md`.

## What is still unproven

**Headroom is thinner than the headline number.** 0.14 on the stress corpus
against a 0.15 durability bar. Every verdict is correct; there is simply less
room before one is not.

**A model endpoint we do not recognise is an agent we cannot see.** An agent
behind a self-hosted gateway is invisible until someone tells us the address.
`custos-a0 stress --hide-gateway` reproduces the miss on demand. This is a
question to ask every design partner directly, because it cannot be inferred —
and it is the single most likely reason a real scan comes back emptier than it
should.

**The byte ratios have only been measured against synthetic traffic.** The
weights were fitted on the A0 corpus. What A0 establishes is that a separating
signal exists and which features carry it — not that these weights generalise.
One real scan answers it, and the thresholds sit in measured empty space so
there is room to move them.

**Destination naming is partial, and how partial is unmeasured.** The scope
reads `billing-api 10.0.4.21`, `rds 10.0.9.45`, `s3` — from the ENI behind an
address, from AWS's own description, and from the flow log's service
annotation. What none of those cover stays a bare address. In the corpus that
is one endpoint out of seven; in a real account the ratio depends entirely on
whether the customer tags ENIs, and nobody has measured it. A scan whose scope
is mostly addresses is a scan whose approvals are mostly guesses, so the
collector reports the count and it belongs next to coverage in the first
design-partner conversation.

**The console was built ahead of the schedule the specification set.** §12
puts it after a paying customer, and that ordering was right: a UI built before
anyone has used the product is a guess about what an operator wants to see. It
exists because it was asked for.

Treat its layout as provisional. Four decisions in it are worth defending and
should be re-argued against a real operator rather than assumed: ordering by
consequence rather than confidence, the evidence gate on the grant control, a
filtered list that always shows the total, and `sanctioned` being absent from
the status control. Everything else about it is a guess.

Nothing depends on it. The CLI and the HTML report still do everything it does.

**Review-band candidates are counted, not kept.** SEC-17 says a workload the
classifier is unsure about is surfaced to an operator and never written as an
agent, which is right — but only the count survives a scan. The report lists
them at the time it is rendered and the console can only say how many there
were and point at it. An operator who wants to look at last week's maybes
cannot. Storing them properly means a table that is emphatically not the
register, and that is a design decision nobody has made yet.

**Nobody has run this against an account we did not build.** Tag hygiene,
unanticipated workload shapes, and provider endpoints outside our catalogue are
all real and all unmeasured.

**Spend figures use placeholder pricing.** `PRICES_REVISION` reads
`unverified-placeholder` and a test pins it there. Verify real provider pricing
before a dollar figure reaches a customer.

**A corpus that was more informative than production.** The A0 corpus
annotated both ends of every AWS conversation with the peer's service. Real
flow logs annotate one end — the destination of a request, the source of its
reply — and the collector was reading only the destination field, so the return
leg of every AWS conversation would have arrived unattributed in a customer's
account and did not here. Fixed in both places, and a test now asserts the
corpus emits what AWS emits. Worth stating plainly because it is the second
time the corpus has been wrong in the flattering direction, and there is no
reason to think it is the last: every signal measured against synthetic
traffic carries this risk until an account we did not build disagrees with it.

## The blocker

Design partner access. Everything technical that justified building first is
resolved; every remaining question needs an environment nobody here has seen.

The kill gate to take seriously is still the week-8 one: four or more scans
with nothing surprising in any means companies do know what they run, and
discovery is not a business.

## If you are picking this up

Read in this order:

1. `docs/A0-FINDINGS.md` — what was measured and what it means
2. `docs/SECURITY-INVARIANTS.md` — the five rules and the tests enforcing them
3. `CONTRIBUTING.md` — how not to break them
4. `docs/OPERATIONS.md` — how to actually run a scan

Then `make check`. If it passes, the invariants hold and the gate is still
green.
