"""The episode primitive must actually produce the signature we claim it does.

If these fail, the corpus is not testing what the specification says it is.
"""

from datetime import UTC, datetime
from random import Random

from custos_a0.endpoints import ANTHROPIC, BILLING_API, ORDERS_DB
from custos_a0.episode import agent_episode
from custos_a0.trace import CallKind, Label, Workload

START = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)


def _workload() -> Workload:
    return Workload(
        name="t", principal="p", scenario="s", label=Label.AGENT, compute="ECS", note=""
    )


def test_episode_alternates_model_and_tool_and_ends_on_model():
    w = _workload()
    agent_episode(w, Random(1), START, 6, ANTHROPIC, [BILLING_API])
    kinds = [c.kind for c in w.calls]
    assert kinds[0] is CallKind.MODEL
    assert kinds[-1] is CallKind.MODEL
    assert kinds == [
        CallKind.MODEL if i % 2 == 0 else CallKind.TOOL for i in range(len(kinds))
    ]
    assert w.model_calls == 6
    assert w.tool_calls == 5


def test_request_bytes_grow_monotonically_across_steps():
    w = _workload()
    agent_episode(w, Random(2), START, 12, ANTHROPIC, [ORDERS_DB])
    reqs = [c.req_bytes for c in w.calls if c.kind is CallKind.MODEL]
    assert all(b > a for a, b in zip(reqs, reqs[1:], strict=False)), reqs


def test_egress_is_superlinear_and_ingress_is_linear():
    """The load-bearing property: O(n^2) up, O(n) down.

    Compared against the same episode at half the length. Doubling the steps
    should roughly quadruple cumulative egress but only double ingress.
    """
    short, long = _workload(), _workload()
    agent_episode(short, Random(3), START, 10, ANTHROPIC, [ORDERS_DB])
    agent_episode(long, Random(3), START, 20, ANTHROPIC, [ORDERS_DB])

    def sums(w):
        m = [c for c in w.calls if c.kind is CallKind.MODEL]
        return sum(c.req_bytes for c in m), sum(c.resp_bytes for c in m)

    s_out, s_in = sums(short)
    l_out, l_in = sums(long)

    assert 3.0 < l_out / s_out < 5.0, l_out / s_out
    assert 1.7 < l_in / s_in < 2.4, l_in / s_in


def test_episode_is_deterministic_under_seed():
    a, b = _workload(), _workload()
    agent_episode(a, Random(9), START, 8, ANTHROPIC, [BILLING_API, ORDERS_DB])
    agent_episode(b, Random(9), START, 8, ANTHROPIC, [BILLING_API, ORDERS_DB])
    assert [(c.at, c.kind, c.req_bytes) for c in a.calls] == [
        (c.at, c.kind, c.req_bytes) for c in b.calls
    ]
