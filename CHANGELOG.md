# Changelog

Notable changes, newest first. Dates are when the work landed on `main`.

## Unreleased

### The loop closed

The repository runs end to end: a collector reads a real AWS account, ships
metadata, the control plane classifies and persists it, and a report comes out
that says what changed since last week.

**Collector**
- Reads VPC Flow Logs from CloudWatch Logs or S3 — whichever the customer
  already uses, because asking a platform team to change where flow logs are
  delivered is a production change request the free-scan motion cannot survive.
- Resolves network interfaces to principals across EC2, Lambda, and ECS. EKS
  resolves to node level and says so rather than claiming pod attribution.
- Enumerates IAM policy, so a finding says "can write to your billing tables"
  rather than "talked to your billing API".
- Reads ALB access logs, taking four fields and discarding the URL, query
  string, user agent, client address, and trace ID at parse time.
- Ships collection statistics, so the control plane can tell "this account is
  clean" from "this scan read a third of the traffic".

**Control plane**
- HTTP API: ship a batch, read the register, sanction an agent, serve the
  report. Idempotent on the collection window.
- SQLite-backed register holding the same SEC-17 state machine as the in-memory
  one, and held to the same tests.
- Scan comparison. The second scan now says something the first did not, which
  is the difference between a subscription and an audit engagement.
- Per-agent baselines and drift, phrased as questions to the workload's owner.
- Retention with a mechanism, so "how long do you keep our data" has a number
  behind it.
- `custos` CLI: scan, register, history, diff, grant, prune. A first customer
  scan needs no server at all.

**Invariants**
- SEC-19 and SEC-20 added alongside the specification's SEC-16 through SEC-18.
- The cross-language wire contract is now tested: the Python schema and the Go
  wire types are compared field for field, in both directions.
- CI runs the invariants and the contract as their own jobs, so a failure reads
  as "an invariant broke" rather than as one line inside two hundred tests.

### Gate G0 — passed

The load-bearing assumption holds: an agent separates from a chatbot backend on
metadata alone. Separation margin 0.26, full recall, zero false positives,
identical at 60s and 600s flow log aggregation.

Two signals the specification expected to carry the classifier were measured
and rejected, and the signal it leads with — burst timing — turned out not to
be implementable at all. Full result and its limitations in
`docs/A0-FINDINGS.md`.
