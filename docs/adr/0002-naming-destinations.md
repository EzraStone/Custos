# ADR 0002 — What a destination is called in an approval scope

Status: accepted
Date: 2026-09-02

## Context

Granting imprimatur is the only action in Custos that confers authority. The
operator doing it is shown the scope they are approving: the tools and data
stores the agent was observed reaching.

For the first several months that scope read:

```
approved_tools:      10.0.4.23, 10.0.5.11
approved_data:       52.216.10.7
```

Nobody can make a decision about an IP address. Every other part of the product
worked — the classifier was right, the reach was accurate, the evidence was
specific enough to argue with — and the one screen where a human confers
authority asked the question in terms the approver could not evaluate.

Flow logs carry no hostname and no SNI. Three sources of a name exist, in
descending order of how much they can be trusted:

1. **The ENI behind the address.** Its `Name` tag is what the customer calls
   the thing. AWS also writes structured descriptions for the interfaces its
   managed services create.
2. **The flow log's own `pkt-src/dst-aws-service` annotation.** Says `S3`,
   `DYNAMODB`, `BEDROCK` — the service, not the instance.
3. **The port.** A private address on 5432 is Postgres. Not a guess: it is the
   same table that decides the destination is a datastore at all.

## Decision

Name a destination from the strongest source available, and where none applies,
show the address unchanged.

### Whether the address survives depends on whether it is stable

This is the rule that is not obvious and will be questioned.

A public service edge **loses** its address: three S3 addresses collapse into a
single `s3` entry. Not for tidiness. AWS service addresses rotate, so an
approval recorded against `52.216.10.7` is stale within days and would have to
be re-granted for traffic that never changed. `s3` is a claim that stays true.

A private address **keeps** it, even when the service is known: `rds
10.0.9.44`, not `rds`. Two RDS instances are two things to approve, their
addresses are stable, and collapsing them would hide one behind the other —
the same mistake in the opposite direction.

### An unrecognised description is not forwarded

An ENI description is a free-text field a person typed into. What people
actually write there is "temp box for INC-4471, ask Sam before deleting".
Shipping that would widen what leaves a customer account from *what their
software is* to *what their engineers wrote down*. This became SEC-23.

### Naming is never a substitute for an address the pipeline uses

Labels are for people. Anything that re-classifies a destination keeps the
address it started with, and there is deliberately no way to turn a label back
into one. A guess about which host an approval covers is the wrong place to
have a guess.

The classifier is untouched: its features count distinct **addresses**, and
naming collapses some of those. Substituting labels there would have changed a
fitted feature to make a display nicer. G0 held at 0.26 through the change,
which is the evidence that it was additive.

## Consequences

**Some destinations stay addresses, and that is visible.** An internal HTTPS
service on an untagged ENI has no name from any source. It renders as
`10.0.4.23`, an honest answer, and the scan reports how much of its scope is in
that state — `custos-collector --check` before onboarding, a banner on the
report, a warning in the console, a column in `custos history`.

**The remedy is the customer's, not ours.** Tagging the ENIs behind their
services is what makes the scope readable, and nothing about the classifier
changes either way. That is a conversation to have during onboarding rather
than after someone is asked to approve `10.0.4.23`.

**The corpus models the failure.** One endpoint is deliberately unnameable, so
the bare-address path is exercised. A corpus where every lookup succeeds would
hide the case the product has to handle — a mistake this repository has now
made three times and caught three times.
