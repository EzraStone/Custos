"""Negative cases: workloads that call models but are not agents.

Two of these are the reason the corpus is worth building.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from ..arrivals import jitter, poisson_arrivals
from ..endpoints import ALB, ANTHROPIC, BEDROCK, VECTOR_DB
from ..episode import tok
from ..trace import Call, CallKind, Label, Workload


def _inbound(w: Workload, at: datetime, req_id: str, req: int, resp: int) -> None:
    w.calls.append(
        Call(at=at, kind=CallKind.INBOUND, endpoint=ALB,
             req_bytes=req, resp_bytes=resp, request_id=req_id)
    )


def chatbot_simple(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="docs-chat-backend",
        principal="arn:aws:iam::447120043318:role/docs-chat",
        scenario="chatbot_simple",
        label=Label.NOT_AGENT,
        compute="ECS",
        note="One model call per inbound request. The easy negative.",
    )
    for at in poisson_arrivals(rng, start, end, 90.0):
        rid = f"req-{int(at.timestamp() * 1e6)}"
        _inbound(w, at, rid, 900, 2400)
        w.calls.append(
            Call(
                at=at + jitter(rng, timedelta(milliseconds=45), 0.5),
                kind=CallKind.MODEL,
                endpoint=ANTHROPIC,
                req_bytes=tok(700 + 500 * rng.random()),
                resp_bytes=tok(150 + 250 * rng.random()),
                request_id=rid,
            )
        )
    return w


def chatbot_rag(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="kb-assistant",
        principal="arn:aws:iam::447120043318:role/kb-assistant",
        scenario="chatbot_rag",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "PRECISION STRESS: embed, then vector search, then generate. "
            "Interleaves model calls with datastore calls exactly the way an "
            "agent's tool loop does. Defeats the tool-interleave signal alone."
        ),
    )
    for at in poisson_arrivals(rng, start, end, 55.0):
        rid = f"req-{int(at.timestamp() * 1e6)}"
        _inbound(w, at, rid, 850, 3100)
        t = at + jitter(rng, timedelta(milliseconds=40), 0.5)
        # Embedding: small request, large response. Inverts the usual ratio.
        w.calls.append(
            Call(at=t, kind=CallKind.MODEL, endpoint=BEDROCK,
                 req_bytes=tok(40 + 90 * rng.random()), resp_bytes=12_300,
                 request_id=rid, step=0)
        )
        t += jitter(rng, timedelta(milliseconds=90), 0.4)
        w.calls.append(
            Call(at=t, kind=CallKind.TOOL, endpoint=VECTOR_DB,
                 req_bytes=12_500, resp_bytes=tok(2200 + 900 * rng.random()),
                 request_id=rid, step=1)
        )
        t += jitter(rng, timedelta(milliseconds=130), 0.4)
        w.calls.append(
            Call(at=t, kind=CallKind.MODEL, endpoint=ANTHROPIC,
                 req_bytes=tok(2800 + 900 * rng.random()),
                 resp_bytes=tok(200 + 300 * rng.random()),
                 request_id=rid, step=2)
        )
    return w


def chatbot_multiturn(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="sales-copilot-web",
        principal="arn:aws:iam::447120043318:role/sales-copilot",
        scenario="chatbot_multiturn",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "THE HARDEST NEGATIVE. Multi-turn sessions accumulate transcript "
            "across turns, so cumulative egress grows super-linearly exactly "
            "like an agent's. The context-accumulation signal cannot separate "
            "this on its own; only inbound coupling can. If the classifier "
            "gets this one right it is not pattern-matching on volume."
        ),
    )
    for session_start in poisson_arrivals(rng, start, end, 12.0):
        turns = 3 + rng.randrange(9)
        accumulated = 0.0
        t = session_start
        sess = f"sess-{int(session_start.timestamp() * 1e6)}"
        for k in range(turns):
            if t >= end:
                break
            rid = f"{sess}-t{k}"
            _inbound(w, t, rid, 800, 2600)
            user_tok = 60 + 160 * rng.random()
            resp_tok = 220 + 320 * rng.random()
            accumulated += user_tok
            w.calls.append(
                Call(
                    at=t + jitter(rng, timedelta(milliseconds=50), 0.6),
                    kind=CallKind.MODEL,
                    endpoint=ANTHROPIC,
                    req_bytes=tok(600 + accumulated),
                    resp_bytes=tok(resp_tok),
                    request_id=rid,
                    step=k,
                )
            )
            accumulated += resp_tok
            # A human reads the answer and types the next question.
            t += jitter(rng, timedelta(seconds=24), 0.7)
    return w


def embedding_service(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="search-embedder",
        principal="arn:aws:iam::447120043318:role/search-embed",
        scenario="embedding_service",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "Very high volume, tightly inbound-coupled, no tools. Proves that "
            "call volume on its own carries no signal."
        ),
    )
    for at in poisson_arrivals(rng, start, end, 400.0):
        rid = f"req-{int(at.timestamp() * 1e6)}"
        _inbound(w, at, rid, 400, 900)
        w.calls.append(
            Call(
                at=at + jitter(rng, timedelta(milliseconds=20), 0.5),
                kind=CallKind.MODEL,
                endpoint=BEDROCK,
                req_bytes=tok(30 + 70 * rng.random()),
                resp_bytes=6_200,
                request_id=rid,
            )
        )
    return w
