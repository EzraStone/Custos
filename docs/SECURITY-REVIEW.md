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
  CloudTrail ─────┤                          │
  IAM (read) ─────┼──▶ collector ────────────┼──▶ control plane
  ALB logs ───────┘    read-only role        │      classifier
   (optional)          no host agent         │      register
                       no compute created    │      report
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
