"""Deliberately ambiguous cases.

Neither of these is an agent: no tool loop, no context accumulation, no planning.
But both are machine-driven with no inbound request, which is one of the two
signals the specification rates as strong.

The correct classifier outcome for both is the review band — not a register
entry, not a silent drop. SEC-17 exists precisely because auto-registering
things that merely look machine-driven turns the register into a self-service
credential issuer. These two workloads are how that requirement gets tested
rather than asserted.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from ..arrivals import jitter, uniform_arrivals
from ..endpoints import BEDROCK, OPENAI
from ..episode import tok
from ..trace import Call, CallKind, Label, Workload


def batch_summariser(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="nightly-doc-summariser",
        principal="arn:aws:iam::447120043318:role/doc-batch",
        scenario="batch_summariser",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "REVIEW-BAND CASE. No inbound requests and high model volume, so "
            "it trips the decoupling signal — but every call is independent, "
            "with flat request sizes and no tools. Should land in review."
        ),
    )
    day = start
    while day < end:
        t = day + timedelta(hours=3)
        for i in range(280 + rng.randrange(220)):
            if t >= end:
                break
            w.calls.append(
                Call(at=t, kind=CallKind.MODEL, endpoint=BEDROCK,
                     req_bytes=tok(1500 + 1400 * rng.random()),
                     resp_bytes=tok(180 + 220 * rng.random()), step=i)
            )
            t += jitter(rng, timedelta(milliseconds=1900), 0.35)
        day += timedelta(days=1)
    return w


def ci_codegen(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="ci-test-generator",
        principal="arn:aws:iam::447120043318:role/ci-runner",
        scenario="ci_codegen",
        label=Label.NOT_AGENT,
        compute="EC2",
        note=(
            "REVIEW-BAND CASE. Bursts of independent model calls on each "
            "commit. No inbound, no growth, no tools. This is the shape the "
            "specification's 'burst of sequential model calls' signal matches "
            "on, and it is not an agent. Separating it is the real job."
        ),
    )
    for at in uniform_arrivals(rng, start, end, 3.2):
        t = at
        for i in range(10 + rng.randrange(14)):
            if t >= end:
                break
            w.calls.append(
                Call(at=t, kind=CallKind.MODEL, endpoint=OPENAI,
                     req_bytes=tok(1800 + 900 * rng.random()),
                     resp_bytes=tok(400 + 500 * rng.random()), step=i)
            )
            t += jitter(rng, timedelta(milliseconds=2600), 0.4)
    return w
