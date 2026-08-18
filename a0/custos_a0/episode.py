"""The agent episode primitive.

An agent trajectory has a specific physical shape, and it is worth stating
precisely because the whole classifier rests on it.

At each step the agent sends the model everything it has accumulated so far: the
system prompt, every prior assistant message, and every prior tool result. The
model replies with one message. A tool is called, and its result is appended to
the transcript. Then the next step resends the whole thing.

The consequence is asymmetric growth. Over an episode of n steps:

    bytes sent to the model     grows as O(n^2)
    bytes received from it      grows as O(n)

A chatbot backend that answers one request per call has both growing as O(n),
with a roughly constant ratio between them.

That quadratic-versus-linear divergence is the signal. It matters because it
survives the thing that destroys the specification's stated signal: it is a
property of *cumulative bytes over an episode*, not of per-call timing, so
60-second flow log aggregation preserves it almost intact.

`tok()` converts model tokens to approximate JSON payload bytes. Four bytes per
token is the usual rule of thumb for English serialised into a messages array.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from .arrivals import jitter
from .endpoints import Endpoint
from .trace import Call, CallKind, Workload


def tok(n: float) -> int:
    """Model tokens to approximate wire payload bytes."""
    return int(n * 4)


def agent_episode(
    w: Workload,
    rng: Random,
    start: datetime,
    steps: int,
    model: Endpoint,
    tools: list[Endpoint],
    request_id: str = "",
) -> datetime:
    """Append one agent trajectory to `w`. Returns the time it finished.

    Emits an alternating chain: model, tool, model, tool, ..., model. Each model
    request carries the accumulated transcript, so request size grows with step
    index while response size does not.
    """
    t = start
    system_tok = 900 + 600 * rng.random()
    accumulated = 0.0

    for step in range(steps):
        req_tok = system_tok + accumulated + 40 * rng.random()
        resp_tok = 180 + 260 * rng.random()

        w.calls.append(
            Call(
                at=t,
                kind=CallKind.MODEL,
                endpoint=model,
                req_bytes=tok(req_tok),
                resp_bytes=tok(resp_tok),
                request_id=request_id,
                step=step,
            )
        )
        # Time to first token scales with context length; this is why long
        # agent episodes occupy many consecutive aggregation windows.
        t += jitter(rng, timedelta(milliseconds=700 + req_tok * 0.55), 0.25)
        accumulated += resp_tok

        if step == steps - 1:
            break

        tool = tools[rng.randrange(len(tools))]
        tool_result_tok = 350 + 1400 * rng.random()
        w.calls.append(
            Call(
                at=t,
                kind=CallKind.TOOL,
                endpoint=tool,
                req_bytes=tok(60 + 180 * rng.random()),
                resp_bytes=tok(tool_result_tok),
                request_id=request_id,
                step=step,
            )
        )
        t += jitter(rng, timedelta(milliseconds=120 + 900 * rng.random()), 0.4)
        # The tool result is appended to the transcript, which is what makes the
        # next model request larger. This single line is the whole signal.
        accumulated += tool_result_tok

    return t
