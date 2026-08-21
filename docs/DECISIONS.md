# Open decisions

The specification lists seven decisions that cannot be resolved from inside the
document. Three are now resolved by evidence rather than by opinion. The rest
still need a human answer, and the ones that block work are marked.

| # | Decision | Status |
|---|---|---|
| 1 | Team size — solo or with a co-founder engineer | **Open. Largest single variable.** |
| 2 | Cloud target for v1 | **Resolved: AWS.** |
| 3 | Design partner access | **Open, and now the critical path.** |
| 4 | Kubernetes in scope for v1 | **Resolved: no, with a caveat.** |
| 5 | Open-source the collector | **Resolved: yes. Done.** |
| 6 | Time commitment | Open. |
| 7 | Agent 365 scope boundary | Open. Needs independent verification. |

---

## 2 — Cloud target: AWS

Resolved by the A0 result rather than by preference. The signal that carries the
classifier is cumulative egress-to-ingress asymmetry against model endpoints,
plus inbound decoupling from load balancer logs. Both are available from VPC
Flow Logs and ALB access logs.

The same two signals are available on GCP (VPC Flow Logs, Cloud Load Balancing
logs) with different field names, so a GCP port is a parsing exercise rather
than a rethink — perhaps two weeks once the AWS path is real. Azure NSG flow
logs carry byte counts but the identity correlation is materially weaker, which
is a research problem rather than a port.

**Consequence:** target profile stays AWS-first. Do not accept a GCP design
partner before the AWS path has produced one real scan.

## 4 — Kubernetes: not in v1, and the caveat is now implemented

Pod-level attribution needs eBPF or Kubernetes audit logs, both materially more
invasive than a cross-account role, and invasiveness is exactly what the entry
motion is built to avoid.

The caveat that changes the answer: agents on EKS **are** visible today at node
level. The classifier sees the traffic and attributes it to the node's instance
profile rather than to the pod. That is a degraded finding — "something on this
node is an agent" — but it is not nothing, and for a customer with one agent
workload per node it is exact.

**Consequence:** report EKS findings at node level, labelled as node-level, and
do not claim pod attribution. Revisit when a design partner's agents are
predominantly on Kubernetes.

**Now built.** The resolver recognises EKS node interfaces and reports them
with the reason attribution stopped there, in report language. Lambda and ECS
resolve fully — Lambda through the function's execution role, ECS through the
task definition's task role, not its execution role. EC2 was already complete.

## 5 — Open-source the collector: done

Apache-2.0, in `collector/`. Everything else proprietary.

This was recommended in the specification and it is now a real and irreversible
commitment. The reasoning held up while building it: the collector is the only
component that runs in customer infrastructure, and three of the five security
invariants are demonstrable only by reading its source. A closed collector would
require a reviewer to trust a data flow diagram, and the ones worth selling to
do not.

---

## 1 — Team size: one engineer, one non-technical co-founder

Resolved in practice rather than by decision.

The consequence for the codebase is that it is optimised for one person's
throughput: two languages rather than four, no service mesh, no Kubernetes, one
SQLite file instead of a database to operate, and invariants enforced by tests
that fail loudly rather than by review discipline that requires a second pair
of eyes.

The consequence for the split is that the non-technical half owns distribution,
and three documents exist for that purpose rather than for engineers:
`docs/SECURITY-REVIEW.md` answers a security questionnaire from the code,
`collector/README.md` gets a platform engineer to a role ARN without a meeting,
and `docs/A0-FINDINGS.md` is the technical credibility artifact.

What is genuinely at risk: A2's breadth — console, auth, tenant isolation,
scheduled re-scans — is work that does not benefit from being thought about
harder, only from more hands. That is the point where a second engineer stops
being optional.

## 3 — Design partners: one or two soft leads, and still the critical path

This was one blocker among several. It is now **the** blocker, and the position
has improved in one specific way: when a lead converts, the collector works.
Terraform to role ARN to report is a path that runs today rather than one that
needs a week of building first.

The technical risk that justified building first is resolved: G0 passed and the
scanner runs end to end. Every remaining question is answerable only by pointing
this at an environment nobody in this repository has seen. Specifically:

- Do the byte ratios hold against real provider endpoints? This is the only
  remaining technical unknown, and one real scan answers it.
- Is tag hygiene good enough that attribution resolves? A0 assumed uneven
  hygiene deliberately, and reality may be worse.
- Does a real account contain workloads whose shape the corpus does not
  anticipate? It certainly does; the question is how many.

The kill gate to take seriously remains the week-8 one: four or more scans with
nothing surprising in any means companies do know what they run, and discovery
is not a business. That gate cannot be reached without design partners, so
sourcing them is the primary activity now, not a background one.

## 7 — Agent 365 scope boundary (open, needs verification)

The specification's claim is that Agent 365 sees agents in the Microsoft tenant
while Custos sees agents in cloud infrastructure — services in AWS accounts
making model API calls under IAM roles.

A0 sharpens what the distinction rests on, which makes it easier to verify. The
Custos signal comes from VPC Flow Logs and IAM policy inside a customer's AWS
account. A tenant registry has no vantage point on either. For Agent 365 to
close that gap it would need to ingest AWS flow logs, which is a different
product with a different sales motion.

**Action:** verify independently before the next customer call, as the
specification says. But the position is more defensible than it was, because the
answer is now "here is the specific telemetry we read that a tenant registry
cannot see" rather than a scope assertion.
