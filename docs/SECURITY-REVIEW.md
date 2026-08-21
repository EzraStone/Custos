# Security review pack

Pre-written answers to what a security team asks before approving a scan. The
specification lists this as a non-code blocker on the first sale; it is here
because the answers are properties of the code, and they should be written by
someone reading the code rather than reconstructed under time pressure on a
call.

Every claim below names the file or test that backs it. If a claim and the code
disagree, the code is right and this document is a bug.

---

## Data flow

```
YOUR AWS ACCOUNT                             │  CUSTOS
                                             │
  VPC Flow Logs ──┐                          │
  CloudTrail ─────┤                          │      classifier
  IAM (read) ─────┼──▶ collector ────────────┼──▶   register (SQLite)
  ALB logs ───────┘    read-only role        │      baselines
   (optional)          no host agent         │      report
                       no compute created    │
                                             │
                       ── metadata only ──▶  │
                       addresses, ports,     │
                       byte counts, timings, │
                       tags, policy actions  │
                                             │
                       ─── never leaves ───  │
                       prompts, completions, │
                       request bodies, URLs, │
                       user agents, client   │
                       addresses             │
```

**Egress:** one direction, HTTPS, TLS 1.2 minimum, to an endpoint you configure.
**Ingress:** none. Nothing connects into your account.
**Residency:** control plane region is contractual, not technical. Ask.

---

## The questions, answered

### What data leaves our account?

The structures in `collector/internal/wire/wire.go` and nothing else. Network
metadata (addresses, ports, byte and packet counts, timings, TCP flags),
identity metadata (principal ARNs, IAM paths, role tags, attached policy
actions), and optionally load balancer timing and size.

**Backed by:** `TestWireTypesCarryNoPayload` walks every wire type by reflection
against a field allowlist. Adding a field requires editing a test named after
the invariant it breaks.

### Do you read prompts or model responses?

No, and the collector is not capable of it. `ship.Send` takes a `wire.Batch` and
nothing else — no `io.Reader`, no `[]byte`, no generic payload — and no type in
`wire` has a field that could hold one.

This is deliberately not a redaction step. A redaction step is a filter, filters
have bugs and configuration, and you are right not to trust one. There is no
path from a payload byte to the network.

**Backed by:** `internal/wire/wire.go`, `internal/ship/ship.go`,
`TestNoPayloadShapedFieldNames`.

### Your load balancer logs contain URLs and user agents. What do you take?

Four fields per line: the timestamp, the target address, and the two byte
counts. Nothing else.

An ALB access log line carries the request URL and query string, the user
agent, the client IP and port, TLS details, and trace identifiers. All of it
describes the people using your system rather than the software, and none of it
reaches us — it is discarded at parse time and has nowhere to go afterwards,
because `wire.InboundRequest` has no field that could hold it.

We take these four because correlation needs exactly them: whether a burst of
model traffic was answering something a human asked for. Nothing more is
required, so nothing more is taken.

**Backed by:** `TestSEC18NothingSensitiveSurvivesParsing`, which parses a
realistic line whose query string contains an email address and an API key and
asserts that neither, nor the user agent, path, client address, or trace ID,
appears anywhere in the parsed record.

### Can the collector change anything in our account?

No, enforced in three places independently.

1. The IAM role grants no write permission.
2. A second policy explicitly denies every mutating action, including
   `logs:StartQuery`, which creates a billable resource.
3. `internal/awsread` refuses any operation whose verb is not on a read
   allowlist, before a request is constructed.

The third exists so the claim survives the first two being widened by mistake.

**Backed by:** `deploy/terraform/iam.tf`, `TestNoMutatingAPIs`.

### What if it runs somewhere it should not?

Nothing happens. With no endpoint and no credential configured it exits
immediately having read nothing and sent nothing.

**Backed by:** `TestZeroConfigIsInert`, `TestRunningByAccidentDoesNothing`.

### Can we see exactly what would be sent before approving it?

Yes. `CUSTOS_DRY_RUN=1` prints the literal batch — the actual bytes, not a
summary — and sends nothing. `--explain` prints what the binary reads, sends,
and structurally cannot do.

We recommend doing this first.

### Do you install an agent on our hosts?

No. There is no host agent, no sidecar, no kernel module, no eBPF. The collector
reads logs your account already produces, and it can run entirely outside your
infrastructure if you prefer.

### What is the blast radius if you are breached?

We would hold read-only metadata about your infrastructure: which principals
exist, what they talk to, and what their policies permit. No credentials, no
data, no payloads, and no ability to act in your account — the role requires an
external ID and grants nothing but reads.

The honest framing: this is inventory data about your software. It would be
useful to an attacker who was already inside, and it would not get them inside.
The collector cannot be pointed at an account whose operator lacks legitimate
access to it.

### How long do you keep our data?

Three windows, and the split is deliberate.

| | Retention | Why |
|---|---|---|
| Flow and request telemetry | Not stored | Classified on arrival; only the derived observation is kept |
| Observations | 90 days | They feed behavioural baselines, and a baseline built from year-old behaviour is not describing the workload that runs today |
| Scan history | 365 days | Answers "when did this start", which is the first question after any finding |
| The register and its audit trail | Kept | Deleting a register entry loses a sanction decision; deleting an audit entry loses the answer to why one was made |

Enforced by `custos prune`, which runs on a schedule. It cannot delete agents or
audit entries — that is a property of the code, not a policy — because it is
built to run unattended.

**Backed by:** `custos/store/retention.py`, `test_agents_are_never_pruned`,
`test_audit_entries_are_never_pruned`.

### What ends up in your logs?

The same rule as the wire: nothing that describes a person. Principals,
endpoints, byte counts, and agent identifiers are logged because they describe
software. URLs, user agents, client addresses, and anything credential-shaped
are dropped by key before a line is written.

Keys are filtered, never values. Inspecting values means deciding what a string
is, and the only reliable way to keep sensitive text out of a log is to have no
field that could hold it.

**Backed by:** `custos/logging.py`, `test_forbidden_keys_never_reach_a_log_line`.

### Who can access our data internally?

Ask us, and hold us to the answer in the MSA. This is a process question and
this document is about code; anyone who answers it with a code reference is
avoiding it.

### Are you SOC 2 compliant?

Not yet. Type I evidence collection needs three months of observation and is
planned to run in parallel with A3. It gates enterprise contracts, not pilots.

If SOC 2 is a hard requirement for a non-production read-only scan, say so now
rather than after two weeks of process — it is a reasonable requirement and we
would rather know.

### What happens when we want you gone?

`terraform destroy`. There is nothing else — no agent on any host, no resource
in any VPC, nothing left behind.

---

## What we do not claim

Stated here so it is never claimed on a call either.

- **We do not judge whether an action is safe.** The classifier decides whether
  a workload is an agent. It has no opinion about whether that is good.
- **We do not detect compromised agents.** Behavioural drift detection is a
  planned Register capability and is not built. Anything described as detection
  today would be a lie.
- **Blast radius is what a credential permits, not what the agent did.** It is
  read from IAM policy, and we label it that way.
- **Spend figures are estimates from byte counts.** Good for ranking agents
  against each other. Not reconcilable against an invoice.
- **Our endpoint catalogue goes stale.** Its revision is stamped in every
  report. An agent using only a provider we do not yet recognise will not
  appear.
