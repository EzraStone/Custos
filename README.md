# Custos

**The register of every AI agent running in a company, and the checkpoint that
governs what those agents are permitted to do.**

Companies are running autonomous agents that hold credentials, call tools, and
write to production systems without a human reading each action. Identity
systems decide *which agents may authenticate*. Nobody decides *which actions
are permitted* — and at most companies, nobody can say which agents are running
at all.

Custos does three things, in this order:

1. **See.** Discover agents running in a cloud account, including the ones
   nobody enrolled, from network and identity metadata alone.
2. **Attribute.** Resolve each one to its owner, its credentials, and what it
   can reach.
3. **Stop.** Gate consequential actions against deterministic policy, with a
   signed receipt for every decision.

Only the first two are built. The third is deliberately not started until a
paying customer asks for it.

---

## Status

**Gate G0 passed.** The load-bearing assumption — that an agent is
distinguishable from a chatbot backend using metadata alone — is tested and
holds, with a 0.26 separation margin, full recall, and zero false positives.

The result and its limitations are in [docs/A0-FINDINGS.md](docs/A0-FINDINGS.md).
Two signals the original specification expected to carry the classifier were
measured and rejected; the finding that matters most is that the specification's
headline signal is not implementable at all, and what replaces it is better.

```
make setup       # virtualenv, both Python packages
make check       # lint and test everything
make experiment  # run A0, print the G0 verdict, write a sample scan report
```

## Layout

| Path | Language | What it is |
|---|---|---|
| `collector/` | Go | Runs in the customer's account under a read-only role. Apache-2.0. |
| `controlplane/` | Python | Classifier, register, attribution, reach, report. |
| `a0/` | Python | The G0 experiment: synthetic telemetry and evaluation. |
| `checkpoint/` | Go | Inline enforcement gateway. Not started. |
| `console/` | TypeScript | Operator UI. Not started. |

Language choices and their reasoning: [docs/adr/0001-language-choices.md](docs/adr/0001-language-choices.md).

The collector is open source and everything else is not. That is a commercial
decision rather than a philosophical one: it is the only component that runs
inside customer infrastructure, and its auditability is what clears security
review.

## The invariants

Five, all testable, none relaxable for a feature request. Each names the test
that enforces it in [docs/SECURITY-INVARIANTS.md](docs/SECURITY-INVARIANTS.md).

| | |
|---|---|
| **SEC-16** | Discovery is read-only. Enforced in the IAM policy *and* in code. |
| **SEC-17** | Discovered records confer no authority. One function can sanction, and it needs a human. |
| **SEC-18** | Metadata only. Enforced by wire types having no field that can hold a payload. |
| **SEC-19** | The collector is inert without an explicit destination. |
| **SEC-20** | A finding without an owner is segregated, never mixed into owned findings. |

SEC-18 is worth reading the code for. It is not a redaction step — a redaction
step is a filter, filters have bugs and configuration, and a reviewer is right
not to trust one. There is simply no field on any wire type capable of holding a
prompt, and `ship.Send` accepts nothing but those types. Two files establish the
entire privacy claim.

## Try the collector without giving it anything

```
cd collector && go build ./cmd/custos-collector
./custos-collector --explain          # what it reads, sends, and cannot do
./custos-collector                    # unconfigured: does nothing, exits clean
CUSTOS_DRY_RUN=1 CUSTOS_FLOW_LOGS=x \
  ./custos-collector --from-file logs # prints the literal batch, sends nothing
```

Dry run exists for security review. "Show me exactly what you would send" gets a
literal answer rather than a data flow diagram.

## What is deliberately not built

Multi-tenancy, RBAC, and SSO before a paying customer. Key management and
signing infrastructure. Policy authoring UI. Machine-learned classification
before three design partners have produced real traffic. Internet-wide scanning,
which is not possible without a privileged vantage point and inherits the entire
civil-liberties objection the in-account model avoids.

And anything a customer has not asked for by name.
