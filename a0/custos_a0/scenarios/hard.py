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
                          declaring a model endpoint exists for.

    agent_via_privatelink an agent whose model calls go to Bedrock over an
                          interface VPC endpoint. Same blindness, arrived at
                          from the other direction — and the one case where
                          nobody has to be asked, because AWS knows what that
                          ENI is and will say so.

    agent_interactive_mcp the positive only the MCP fingerprint catches.
                          Inbound-coupled and short-trajectoried, so both of
                          the strong signals are weak, and it drives an MCP
                          server.

    agent_via_published_endpoint
                          the residue. Model calls go over PrivateLink to a
                          service another AWS account published, which AWS
                          names with an opaque id and will not explain. The
                          only one of the three that is still a question.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from ..arrivals import jitter, poisson_arrivals, uniform_arrivals
from ..endpoints import (
    ALB,
    ANTHROPIC,
    BEDROCK,
    BEDROCK_PRIVATELINK,
    BILLING_API,
    DEPLOY_API,
    MCP_FILES,
    MCP_GITHUB,
    ORDERS_DB,
    PROVIDER_PRIVATELINK,
    TICKET_API,
    VECTOR_DB,
    Endpoint,
    EndpointClass,
)
from ..episode import agent_episode, reply, tok
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
                            **reply(60 + 40 * rng.random()),
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
                            **reply(180 + 260 * rng.random()),
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
            "internal API. Invisible until someone declares the gateway, and "
            "this workload is why that function exists."
        ),
    )
    tools = [DEPLOY_API, MCP_GITHUB]

    for at in uniform_arrivals(rng, start, end, 2.5):
        agent_episode(w, rng, at, 6 + rng.randrange(8), GATEWAY, tools)

    return w


def agent_via_privatelink(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="claims-triage-agent",
        principal="arn:aws:iam::447120043318:role/claims-triage",
        scenario="agent_via_privatelink",
        label=Label.AGENT,
        compute="ECS",
        note=(
            "CATALOGUE STRESS, and the one nobody has to be asked about. Every "
            "model call goes to Bedrock over an interface VPC endpoint, so the "
            "destination is a private address in the customer's own subnet and "
            "the flow log's AWS service annotation — which is for AWS's "
            "published ranges — says nothing. Indistinguishable from an "
            "internal API on the wire, and AWS knows exactly what it is."
        ),
    )
    tools = [BILLING_API, ORDERS_DB]

    for at in uniform_arrivals(rng, start, end, 3.0):
        agent_episode(w, rng, at, 5 + rng.randrange(7), BEDROCK_PRIVATELINK, tools)

    return w


def agent_via_published_endpoint(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="underwriting-agent",
        principal="arn:aws:iam::447120043318:role/underwriting",
        scenario="agent_via_published_endpoint",
        label=Label.AGENT,
        compute="ECS",
        note=(
            "THE RESIDUE. Model calls go over PrivateLink to a service another "
            "AWS account published — a model provider selling into AWS — so the "
            "destination is a private address, the flow record says nothing, "
            "and AWS names the service com.amazonaws.vpce.<region>.vpce-svc-... "
            "and will not say what is behind it. Everything that can be looked "
            "up has been; this one is still a question for a human, and it is "
            "here to check that the question is actually asked."
        ),
    )
    tools = [BILLING_API, TICKET_API]

    for at in uniform_arrivals(rng, start, end, 3.0):
        agent_episode(w, rng, at, 5 + rng.randrange(7), PROVIDER_PRIVATELINK, tools)

    return w


def agent_interactive_mcp(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="ide-assistant-backend",
        principal="arn:aws:iam::447120043318:role/ide-assistant",
        scenario="agent_interactive_mcp",
        label=Label.AGENT,
        compute="ECS",
        note=(
            "THE POSITIVE THAT ONLY MCP CATCHES. A coding assistant behind an "
            "editor: every trajectory starts with a person typing, so the "
            "decoupling signal is nearly absent, and each one is two or three "
            "steps so the transcript barely accumulates and the asymmetry is "
            "weak. What it does do is drive an MCP server, which is what an "
            "agent is for and what nothing else in this corpus needed the "
            "fingerprint to see.\n\n"
            "Added because an ablation measured mcp_fingerprint as contributing "
            "0.000 of separation on both corpora — every workload reaching an "
            "MCP server was already scoring 0.995 on the other signals, so the "
            "signal had nothing left to do. That is a fact about the corpus, "
            "not about the signal, and the way to tell the difference is to "
            "put in the case it was carried for."
        ),
    )
    tools = [MCP_GITHUB, MCP_FILES]

    for at in poisson_arrivals(rng, start, end, 18.0):
        rid = f"req-{int(at.timestamp() * 1e6)}"
        _inbound(w, at, rid, 1_400, 4_200)
        t = at + jitter(rng, timedelta(milliseconds=60), 0.5)
        # Two or three steps. Short enough that cumulative egress never pulls
        # far ahead of what comes back.
        for step in range(2 + rng.randrange(2)):
            w.calls.append(Call(
                at=t, kind=CallKind.MODEL, endpoint=ANTHROPIC,
                req_bytes=tok(900 + 700 * step + 200 * rng.random()),
                **reply(260 + 200 * rng.random()),
                request_id=rid, step=step * 2,
            ))
            t += jitter(rng, timedelta(milliseconds=800), 0.3)
            tool = tools[rng.randrange(len(tools))]
            w.calls.append(Call(
                at=t, kind=CallKind.TOOL, endpoint=tool,
                req_bytes=tok(120 + 80 * rng.random()),
                resp_bytes=tok(600 + 900 * rng.random()),
                request_id=rid, step=step * 2 + 1,
            ))
            t += jitter(rng, timedelta(milliseconds=180), 0.4)
        if t >= end:
            break

    return w
