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
| Customer-supplied pricing | Works. Per account, dated, superseded rather than overwritten |
| Review band, kept and readable | Works. In the console, the CLI, and both reports, with evidence and recurrence |
| Model gateway declaration | Works. Detected as questions, declared per account, effective next scan |
| Fleet view across accounts | Works. One line per account, unscanned and destructive first |
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

**A model endpoint we do not recognise is an agent we cannot see** — but the
account can now be asked, and told. An agent behind a self-hosted gateway has
no model traffic we can observe, so it is not a low-confidence finding, it is
nothing. That is still true and still the mechanism.

What changed is that it is no longer silent. `custos-collector --check` names
internal addresses that send far more than they receive before a byte is sent;
every scan produces the same list as questions with the numbers behind them;
the console asks them above the register; and a declaration takes effect on the
next scan. The report says whether an account declared anything, because a
report with no findings and no declarations is the exact artefact a hidden
gateway produces.

`custos-a0 stress --hide-gateway` still reproduces the miss — recall 0.88 — and
now exercises the same per-account declaration the product ships rather than a
global one nobody can use.

**What is unmeasured is whether the questions are the right ones.** The
detector finds the real gateway first on the stress corpus and asks nothing at
all on the base corpus, which is the result that matters. Neither of those is
a real account. The failure to watch for is a customer who gets three questions
a week about ordinary internal APIs and stops reading them, which is how they
miss the one that matters.

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

**Nobody has run this against an account we did not build.** Tag hygiene,
unanticipated workload shapes, and provider endpoints outside our catalogue are
all real and all unmeasured.

**Spend figures are placeholders until an account says otherwise.** The
built-in table still reads `unverified-placeholder` and a test still pins it
there. What changed is that verifying it is no longer our job: an account can
supply the rates it actually pays — enterprise agreement, committed use,
provisioned throughput — and every surface then says the figures are theirs and
when they said so.

That is the honest resolution rather than the one this entry originally
anticipated. We were never going to be able to verify a customer's rate; they
have the contract and we do not. What we can do is stop presenting our guess as
though it were theirs, and make supplying the real number a two-minute job.

Still true whichever rates are used: the figures come from wire bytes, not
token counts. Good for ranking agents against each other, not for reconciling
against an invoice, and labelled that way everywhere.

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
