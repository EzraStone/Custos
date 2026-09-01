"""The cases the current corpus does not cover.

Every workload in `agents.py` is either fully decoupled or fully coupled, and
every negative in `chatbots.py` is fully coupled. Real accounts are not that
tidy, and a classifier tuned on a clean split will find the clean cases and
miss the rest.

Four workloads, each aimed at a specific assumption:

    agent_human_in_loop   partially coupled. An agent that pauses for approval
                          has genuine inbound requests, so the decoupling
                          signal — the strongest one — is diluted rather than
                          absent. This is the hardest positive in the corpus.

    agent_batch           an agent that runs as a batch job. Looks like the
                          review-band batch summariser on volume and schedule,
                          and is a real agent doing a real tool loop.

    chatbot_function_call  a coupled chatbot that calls tools. The tool
                          interleave signal fires on it, and every request is
                          answered by a human's arrival.

    agent_via_gateway     an agent whose model traffic goes to a self-hosted
                          gateway on a private address. Invisible to the
                          built-in catalogue, which is the case
                          `catalog.extend` exists for.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from ..arrivals import jitter, poisson_arrivals, uniform_arrivals
from ..endpoints import (
    ALB,
    ANTHROPIC,
    BEDROCK,
    BILLING_API,
    DEPLOY_API,
    MCP_GITHUB,
    ORDERS_DB,
    TICKET_API,
    VECTOR_DB,
    Endpoint,
    EndpointClass,
)
from ..episode import agent_episode, tok
from ..trace import Call, CallKind, Label, Workload

# A self-hosted LLM gateway. Private address, ordinary HTTPS port: nothing
# distinguishes it from an internal API without being told.
GATEWAY = Endpoint("llm-gateway.svc.internal", "10.0.7.40", 443, EndpointClass.INTERNAL_API)


def _inbound(w: Workload, at: datetime, req_id: str, req: int = 800, resp: int = 2600) -> None:
    w.calls.append(
        Call(at=at, kind=CallKind.INBOUND, endpoint=ALB,
             req_bytes=req, resp_bytes=resp, request_id=req_id)
    )


def agent_human_in_loop(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="refund-approval-agent",
        principal="arn:aws:iam::447120043318:role/refund-approval",
        scenario="agent_human_in_loop",
        label=Label.AGENT,
        compute="ECS",
        note=(
            "THE HARDEST POSITIVE. Plans a refund across several steps, then "
            "pauses for a human to approve, then continues. The approval is a "
            "genuine inbound request, so the decoupling signal is diluted "
            "rather than absent — and this shape is common in exactly the "
            "workflows customers most want governed."
        ),
    )
    tools = [BILLING_API, ORDERS_DB, TICKET_API]

    for at in poisson_arrivals(rng, start, end, 4.0):
        # First leg: investigate, entirely on its own.
        resumed = agent_episode(w, rng, at, 4 + rng.randrange(4), ANTHROPIC, tools)
        if resumed >= end:
            break

        # A human approves. One inbound request for a whole trajectory.
        approval = resumed + jitter(rng, timedelta(seconds=45), 0.8)
        if approval >= end:
            break
        _inbound(w, approval, f"approval-{int(approval.timestamp() * 1e6)}")

        # Second leg: execute, again on its own.
        agent_episode(w, rng, approval + timedelta(milliseconds=120),
                      3 + rng.randrange(4), ANTHROPIC, tools)

    return w


def agent_batch(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="nightly-reconciliation-agent",
        principal="arn:aws:iam::447120043318:role/nightly-recon",
        scenario="agent_batch",
        label=Label.AGENT,
        compute="ECS",
        note=(
            "An agent that happens to run as a batch job. Same schedule and "
            "volume profile as nightly-doc-summariser, which sits in the "
            "review band — but this one runs a real tool loop per item. If the "
            "classifier separates on schedule it will get exactly one of these "
            "two wrong."
        ),
    )
    tools = [ORDERS_DB, BILLING_API]

    day = start
    while day < end:
        at = day + timedelta(hours=4) + jitter(rng, timedelta(minutes=8), 0.7)
        for _ in range(18 + rng.randrange(12)):
            if at >= end:
                break
            at = agent_episode(w, rng, at, 3 + rng.randrange(3), BEDROCK, tools)
            at += jitter(rng, timedelta(seconds=6), 0.5)
        day += timedelta(days=1)

    return w


def chatbot_function_call(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="ops-assistant-web",
        principal="arn:aws:iam::447120043318:role/ops-assistant",
        scenario="chatbot_function_call",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "PRECISION STRESS. A chatbot with function calling: one or two "
            "tool calls per request, every one of them answering a human who "
            "just asked. Trips tool interleave the way an agent does and is "
            "saved only by coupling."
        ),
    )
    tools = [VECTOR_DB, TICKET_API]

    for at in poisson_arrivals(rng, start, end, 40.0):
        rid = f"req-{int(at.timestamp() * 1e6)}"
        _inbound(w, at, rid)
        t = at + jitter(rng, timedelta(milliseconds=40), 0.4)

        w.calls.append(Call(at=t, kind=CallKind.MODEL, endpoint=ANTHROPIC,
                            req_bytes=tok(700 + 400 * rng.random()),
                            resp_bytes=tok(60 + 40 * rng.random()),
                            request_id=rid, step=0))
        t += jitter(rng, timedelta(milliseconds=110), 0.4)

        for step in range(1 + rng.randrange(2)):
            tool = tools[rng.randrange(len(tools))]
            w.calls.append(Call(at=t, kind=CallKind.TOOL, endpoint=tool,
                                req_bytes=tok(50 + 60 * rng.random()),
                                resp_bytes=tok(300 + 500 * rng.random()),
                                request_id=rid, step=step + 1))
            t += jitter(rng, timedelta(milliseconds=90), 0.4)

        w.calls.append(Call(at=t, kind=CallKind.MODEL, endpoint=ANTHROPIC,
                            req_bytes=tok(1400 + 700 * rng.random()),
                            resp_bytes=tok(180 + 260 * rng.random()),
                            request_id=rid, step=3))

    return w


def agent_via_gateway(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="deploy-remediation-agent",
        principal="arn:aws:iam::447120043318:role/deploy-remediation",
        scenario="agent_via_gateway",
        label=Label.AGENT,
        compute="EKS",
        note=(
            "CATALOGUE STRESS. Every model call goes to a self-hosted gateway "
            "on a private address, which the built-in catalogue reads as an "
            "internal API. Invisible until someone runs catalog.extend, and "
            "this workload is why that function exists."
        ),
    )
    tools = [DEPLOY_API, MCP_GITHUB]

    for at in uniform_arrivals(rng, start, end, 2.5):
        agent_episode(w, rng, at, 6 + rng.randrange(8), GATEWAY, tools)

    return w
