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
| Resolve interface → principal, EKS | Node level only, labelled as such |
| Blast radius from IAM policy | Works |
| Attribution to a team | Works, four methods with stated confidence |
| Persistent register with SEC-17 state machine | Works |
| Scan comparison | Works |
| Behavioural baselines and drift | Works |
| HTTP API, container image, retention | Works |
| Enforcement checkpoint | **Not started.** §12: not before a paying customer |
| Operator console | **Not started.** Same reason |

## The one number that matters

**G0 passed: 1.00 recall, 1.00 precision, 0.26 separation margin, identical at
60s and 600s flow log aggregation.**

Reproduce with `make experiment`. CI fails the build if it stops holding.

The finding underneath it is the interesting part: the signal the specification
leads with — burst timing and per-call payload growth — is not implementable,
because aggregation plus TLS connection reuse leaves under 0.5 flow records per
model call. What replaced it, cumulative egress-to-ingress asymmetry, is a
property of summed bytes and survives aggregation intact.

Full result and its limitations: `docs/A0-FINDINGS.md`.

## What is still unproven

**The byte ratios have only been measured against synthetic traffic.** The
weights were fitted on the A0 corpus. What A0 establishes is that a separating
signal exists and which features carry it — not that these weights generalise.
One real scan answers it, and the thresholds sit in measured empty space so
there is room to move them.

**Nobody has run this against an account we did not build.** Tag hygiene,
unanticipated workload shapes, and provider endpoints outside our catalogue are
all real and all unmeasured.

**Spend figures use placeholder pricing.** `PRICES_REVISION` reads
`unverified-placeholder` and a test pins it there. Verify real provider pricing
before a dollar figure reaches a customer.

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
