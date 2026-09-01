# Turning a finding into a ticket

A finding with no owner is noise, and noise gets the tool uninstalled. The
report goes to a security lead who forwards it to nobody, because there is
nobody to forward it to.

This is the least glamorous part of the product and the one that decides whether
it is a control or a curiosity.

## The problem

A flow record names a network interface. A person needs a team.

AWS makes the gap differently hard per compute type, and there is no single
answer:

| Compute | Path | State |
|---|---|---|
| EC2 | interface → instance → instance profile → role | Complete |
| Lambda | interface description → function name → execution role | Complete |
| ECS | interface → task → task definition → task role | Complete |
| EKS | interface belongs to the **node**, not the pod | Node level only |

EKS is the honest ceiling. Pod-level attribution needs eBPF or Kubernetes audit
logs, both far more invasive than a cross-account read role, and invasiveness is
exactly what the entry motion is built to avoid. A node-level finding is
reported as node-level and never dressed up as more.

## Four ways to reach an owner

Once a principal is resolved, the Attributor walks four sources. Each carries
its own confidence, because "the payments team owns this, per the resource tag"
and "the role name starts with pay-" are different claims and reporting them
identically is how a report loses credibility on the one finding someone checks.

| Source | Confidence | Notes |
|---|---|---|
| Resource tags | 0.95 | Strongest, and the customer already maintains them for cost allocation |
| Role tags | 0.90 | |
| IAM path | 0.65 | `/payments/service-role/` is real convention wherever there is IAM discipline |
| Name heuristic | 0.35 | A lead to chase, never presented as an answer |

An unresolved principal is reported as unattributed, in its own section. Never
guessed at, never mixed into the owned findings — a wrong owner is worse than no
owner, because it routes the finding to someone who correctly ignores it and
stops reading the next one.

## The fallback that does not need an interface

CloudTrail records the assumed role and the source address of a Bedrock call in
the same event. That maps a principal to an address directly: no interface
description to parse, no naming convention to rely on, and it works for compute
whose interfaces cannot be resolved at all — including EKS pods.

Three constraints keep it honest:

**It runs last.** An ENI attached to an instance profile is a stronger claim
than an address seen making a call, because addresses are recycled. CloudTrail
fills gaps; it never overrides.

**It is skipped when nothing needs filling.** Otherwise it costs a dozen API
calls every window forever on a well-tagged account that gains nothing from it.

**It is reported as weaker.** The scan summary says how many interfaces were
attributed this way, because a scan leaning on it heavily should look different
from one that did not need it.

It also covers only AWS-native model calls. An account calling Anthropic
directly gets nothing from this, which is most accounts.

## The customer's own escape hatch

A `custos:principal` tag on a network interface beats every inference above.
Their metadata about their own infrastructure is better than anything we can
derive, and an account with unusual topology should not be stuck with our
guesses.

## When attribution is mostly failing

That is a finding about the customer, not a bug in the resolver. If everything
resolves by name heuristic, or the unattributed section is large, their tag
hygiene is the problem to solve first — and saying so is more useful than
sending a list they cannot route.
