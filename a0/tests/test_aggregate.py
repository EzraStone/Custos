"""What aggregation destroys, measured.

These tests are the technical heart of A0. They are written as assertions on
information loss rather than on correctness, because the loss is the finding:
it is what tells us which classifier signals can exist at all.
"""

from datetime import timedelta

import pytest

from custos_a0 import corpus
from custos_a0.trace import CallKind
from custos_a0.wire import AggregationConfig, aggregate
from custos_a0.wire.record import SYN, Direction

CORPUS = corpus.build()


def _cap(seconds: int, **kw):
    return aggregate(CORPUS, AggregationConfig(interval=timedelta(seconds=seconds), **kw))


def test_capture_carries_no_ground_truth():
    """The classifier's input must not leak the answer.

    A capture may carry an ENI, a principal ARN, and an address, because a real
    collector genuinely resolves all three. It must never carry a scenario or a
    label.

    Note what is deliberately NOT asserted: that principal names fail to
    resemble workload names. Real IAM role names do resemble the service they
    belong to, and the Attributor depends on exactly that. The protection
    against the classifier cheating on role-name substrings is structural
    instead — feature extraction is never handed the principal string, which
    `test_leakage.py` enforces.
    """
    cap = _cap(60)
    forbidden = {w.scenario for w in CORPUS.workloads} | {"agent", "not_agent"}
    blob = " ".join(r.to_line() for r in cap.records[:5000])
    blob += " ".join(cap.principal_by_eni.values())
    leaked = {f for f in forbidden if f in blob}
    assert not leaked, leaked


@pytest.mark.parametrize("interval", [60, 600])
def test_aggregation_collapses_records(interval):
    fine, coarse = len(_cap(1).records), len(_cap(interval).records)
    assert coarse < fine * 0.6, (fine, coarse)


def test_busy_agent_loses_per_call_resolution_entirely():
    """The specification's headline signal, tested directly.

    'Sub-second gaps' and 'monotonically growing payload size' require one flow
    record per model call. For the corpus's busiest agent at a 60s interval,
    measure how many records actually exist per model call.
    """
    w = CORPUS.by_name("inventory-reconciler")
    model_calls = sum(
        1 for c in w.calls if c.kind is CallKind.MODEL
    )
    cap = _cap(60)
    egress_to_model = [
        r for r in cap.records
        if r.interface_id == w.eni and r.direction is Direction.EGRESS and r.dstport == 443
    ]
    records_per_call = len(egress_to_model) / model_calls
    # Far below one record per call: the individual calls are unrecoverable.
    assert records_per_call < 0.5, records_per_call


def test_first_record_on_a_connection_carries_syn():
    cap = _cap(60)
    assert any(r.tcp_flags & SYN for r in cap.records)


def test_egress_ingress_asymmetry_survives_aggregation():
    """The signal that replaces the one aggregation destroyed.

    Summing bytes over the whole window per direction, an agent's ratio of
    egress to ingress against model endpoints must stay well above a chatbot's.
    """
    cap = _cap(60)
    model_ips = {"160.79.104.10", "104.18.7.192", "52.94.236.10"}

    def ratio(eni: str) -> float:
        out = sum(
            r.bytes for r in cap.records
            if r.interface_id == eni and r.direction is Direction.EGRESS and r.dstaddr in model_ips
        )
        inn = sum(
            r.bytes for r in cap.records
            if r.interface_id == eni and r.direction is Direction.INGRESS and r.srcaddr in model_ips
        )
        return out / max(inn, 1)

    agent = ratio(CORPUS.by_name("support-triage-agent").eni)
    chatbot = ratio(CORPUS.by_name("docs-chat-backend").eni)
    assert agent > chatbot * 2, (agent, chatbot)


def test_alb_logs_can_be_absent():
    """Some customers will not hand over load balancer logs. A0 must be able to
    measure what the classifier loses without them."""
    assert _cap(60, have_alb_logs=False).requests == {}


def test_records_are_time_ordered():
    recs = _cap(60).records
    assert all(a.start <= b.start for a, b in zip(recs, recs[1:], strict=False))
