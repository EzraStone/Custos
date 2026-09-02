# Changelog

Notable changes, newest first. Dates are when the work landed on `main`.

## Unreleased

### The approval is readable

**The operator console.** The register in a browser, served by the control
plane at `/`: read the findings, read the evidence, sanction an agent, retire
one that no longer exists. Ordered by what each agent could destroy rather than
by how confident the classifier is. The grant control stays disabled until the
evidence has been opened — a console that makes approving easier than reading
upholds SEC-17 in code while defeating it in practice.

Built ahead of the schedule §12 sets, which put it after a paying customer.
That ordering was right and the deviation is recorded in `docs/STATUS.md`.

**Destinations have names.** The approval scope read `10.0.4.23`,
`52.216.10.7`. Nobody can make a decision about an IP address, and that was the
one screen in the product where a human confers authority. It now reads
`billing-api 10.0.4.21`, `rds 10.0.9.45`, `s3` — from the ENI behind an
address, from AWS's own description of a managed service, or from the flow
log's service annotation. What none of those cover stays an address, honestly,
and every surface reports how much of a scan is in that state.

Three things this uncovered. The tools/data-stores split was inferred from what
a whole window saw, so an internal API could be filed as a data store on the
strength of unrelated traffic in the same minute. The collector read only
`pkt-dst-aws-service`, so the return leg of every AWS conversation arrived
unattributed in production — hidden here by a corpus that annotated both ends,
which is not what AWS emits. And a column added to the schema after a database
was created never reached it, because the schema is applied with `CREATE TABLE
IF NOT EXISTS`.

**SEC-23.** Only names matching a shape AWS writes leave the account. An ENI
description is free text a person typed into, and what people write there is
"temp box for INC-4471, ask Sam before deleting".

**What changed since the last scan**, over HTTP and in the console. The CLI has
answered this since the register existed; nothing else could, so the console
showed a register with no sense of time — which is the difference between a
subscription and an audit.

### Continuous, and harder to fool

**Delivery.** Findings reach Slack and a SIEM without anyone opening a report.
The design is mostly restraint: a first scan sends one summary rather than
forty alerts, later scans send only what changed, and suppression is per channel
with repeat windows set by severity. The failure mode of a security channel is
not missing an alert — it is sending so many that the channel gets muted, after
which every alert is missed.

**Scheduled collection.** The collector runs as a service, tracking a cursor so
a crash, a deploy, or a throttled hour costs latency rather than data. A window
that exceeds its record limit is now **shortened** rather than truncated, which
closes a silent data-loss path: a truncated window advanced the cursor past
records that were never read, and the next scan simply reported fewer agents.

**Attribution reaches further.** Lambda and ECS execution roles resolve fully.
CloudTrail fills the remaining gaps by mapping a source address to the role that
used it — the one path that needs no network interface, and the only one that
reaches EKS pods.

**Multi-account tokens.** One customer in the target profile runs five to fifty
AWS accounts. A token now covers a named set rather than one, without weakening
the boundary: it still cannot reach an account it was not issued for, and must
say which account a read applies to rather than being allowed to guess.

**Onboarding.** `custos onboard` generates a customer's credentials, tfvars, and
the paragraph to paste into a ticket. `custos-collector --check` names which
onboarding failure occurred, because every one of them produces the same
symptom: a report with no findings.

### A harder corpus, and a smaller margin

Four workloads were added that break the clean coupled/decoupled split the
original corpus had: an agent that pauses for human approval, an agent on a
batch schedule, a chatbot with function calling, and an agent behind a
self-hosted gateway.

**Every verdict is still correct and there are still no false positives. The
separation margin falls from 0.26 to 0.14.** That is the number to quote
wherever the first would be doing work, and `make stress` prints it.

The weights were not retuned to widen it. They were already fitted on synthetic
traffic; fitting them again on more synthetic traffic would improve the metric
and nothing else.

### Two new invariants

Both from building rather than planning. SEC-21 came from finding a third-party
HTTP client logging full request URLs at INFO, routing query strings into a
customer's SIEM by a path the application's own middleware never touched.
SEC-22 came from realising a truncated collection window is silent data loss.


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
