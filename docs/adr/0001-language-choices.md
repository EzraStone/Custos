# ADR 0001 — Language per deployment unit

Status: accepted
Date: 2026-08-18

## Context

Custos has four deployment units with materially different constraints. Picking
one language for all of them optimises for the wrong thing.

## Decision

### Collector — Go

This is the one with a genuinely correct answer. It compiles to a single static
binary with no runtime dependency, which matters enormously when we are asking a
security team to run our code in their account. "Here is a 12MB binary, here is
the source, no interpreter, no dependency tree" clears review far faster than a
Python app with forty transitive dependencies — every one of which is a supply
chain question the reviewer now has to ask.

Go is also the lingua franca of cloud infrastructure. The AWS SDK is excellent,
and a platform engineer reading our open-source collector expects Go and trusts
it more for that.

### Classifier and control plane — Python

The classifier rules will change constantly through A0 and A1. We will be
hand-tuning heuristics against real traffic weekly, and Python iterates fastest
for that. It also has the data tooling we want during the signal test, and
FastAPI is entirely adequate for the API.

The performance argument against Python does not apply here. We process log
batches on a schedule; we do not serve a hot path.

### Checkpoint gateway — Go

Different reasoning from the collector. This one sits inline in a customer's
critical path with a latency budget and fail-closed semantics. Go's concurrency
model, predictable latency, and strong crypto standard library all fit.

Python would work at pilot volume, but we would rewrite it later — and rewriting
the security-critical component during a Series A is the worst possible time to
be doing it.

### Console — TypeScript and React

No interesting decision here. Plain, boring, hireable.

## Consequence

Two languages in anger plus one for the UI. The cost is a second toolchain in
CI. The benefit is that no component is written in a language that will force a
rewrite at the point where a rewrite is most expensive.
