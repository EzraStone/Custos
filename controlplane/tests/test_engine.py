"""Engine behaviour, tested on constructed features rather than on the corpus.

Corpus-level accuracy is A0's job. These tests pin the properties the engine
must have regardless of what the data says.
"""

import dataclasses

import pytest

from custos.classify.engine import (
    AGENT_THRESHOLD,
    REVIEW_THRESHOLD,
    Disposition,
    disposition_for,
    score,
)
from custos.classify.features import Features

BASE = Features(
    have_inbound_logs=True, model_windows=100,
    total_model_egress=1_000_000, total_model_ingress=1_000_000,
    egress_ratio=1.0, inbound_coupling=1.0,
    tool_interleave=0.0, distinct_tool_addresses=0, mcp_windows=0,
)


def f(**kw) -> Features:
    return dataclasses.replace(BASE, **kw)


def test_a_tightly_coupled_low_ratio_workload_is_not_an_agent():
    confidence, _, _ = score(BASE)
    assert disposition_for(confidence) is Disposition.NOT_AGENT


def test_the_agent_shape_scores_high():
    confidence, _, _ = score(
        f(egress_ratio=30.0, inbound_coupling=0.0, tool_interleave=1.0, mcp_windows=40)
    )
    assert disposition_for(confidence) is Disposition.AGENT


def test_decoupled_but_toolless_lands_in_review_not_in_the_register():
    """SEC-17's reason for existing.

    A batch job has no inbound requests and a moderate egress ratio. It is not
    an agent, and it must not be auto-registered as one.
    """
    confidence, _, _ = score(
        f(egress_ratio=6.5, inbound_coupling=0.0, tool_interleave=0.0)
    )
    assert disposition_for(confidence) is Disposition.REVIEW


def test_missing_inbound_logs_are_reported_not_assumed():
    """The bug this test exists to prevent: treating 'cannot see' as 'none'."""
    confidence, firings, unavailable = score(f(have_inbound_logs=False))
    assert "inbound_decoupling" in unavailable
    assert all(fi.id != "inbound_decoupling" for fi in firings)
    # And it must not have been silently scored as fully decoupled.
    coupled_confidence, _, _ = score(f(inbound_coupling=0.0))
    assert confidence < coupled_confidence


def test_degrading_without_logs_costs_recall_but_never_precision():
    """A chatbot must not become an agent just because logs went missing."""
    chatbot = f(egress_ratio=1.7, inbound_coupling=1.0, tool_interleave=0.0)
    confidence, _, _ = score(dataclasses.replace(chatbot, have_inbound_logs=False))
    assert disposition_for(confidence) is Disposition.NOT_AGENT


def test_confidence_is_bounded_and_monotonic_in_egress_ratio():
    previous = -1.0
    for ratio in (0.1, 1, 5, 10, 50, 1000):
        confidence, _, _ = score(f(egress_ratio=ratio))
        assert 0.0 <= confidence <= 1.0
        assert confidence > previous
        previous = confidence


def test_scoring_is_deterministic():
    assert score(BASE)[0] == score(BASE)[0]


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (1.0, Disposition.AGENT),
        (AGENT_THRESHOLD, Disposition.AGENT),
        (AGENT_THRESHOLD - 1e-9, Disposition.REVIEW),
        (REVIEW_THRESHOLD, Disposition.REVIEW),
        (REVIEW_THRESHOLD - 1e-9, Disposition.NOT_AGENT),
        (0.0, Disposition.NOT_AGENT),
    ],
)
def test_band_boundaries(confidence, expected):
    assert disposition_for(confidence) is expected


def test_evidence_is_ordered_by_contribution():
    from custos.classify.engine import classify_principal
    from custos.classify.episodes import PrincipalTelemetry

    t = PrincipalTelemetry(principal="role/x", inbound_logs_available=True)
    verdict = classify_principal(t)
    contributions = [
        fi.contribution
        for fi in sorted(verdict.firings, key=lambda x: x.contribution, reverse=True)
    ]
    assert contributions == sorted(contributions, reverse=True)


# --- signals over an empty set ------------------------------------------------


def _telemetry_without_model_traffic():
    """A workload that talks to internal services and never to a model
    endpoint we recognise. In a real account this is most of them."""
    from datetime import UTC, datetime, timedelta

    from custos.classify.episodes import sessionize
    from custos.telemetry import Direction, FlowRecord

    start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    records = []
    for minute in range(40):
        at = start + timedelta(minutes=minute)
        records.append(FlowRecord(
            account_id="1", interface_id="eni-1", srcaddr="10.0.1.5",
            dstaddr="10.0.5.11", srcport=41000 + minute, dstport=8931,
            protocol=6, packets=40, bytes=140_000,
            start=at, end=at + timedelta(seconds=30), direction=Direction.EGRESS,
        ))
        records.append(FlowRecord(
            account_id="1", interface_id="eni-1", srcaddr="10.0.5.11",
            dstaddr="10.0.1.5", srcport=8931, dstport=41000 + minute,
            protocol=6, packets=8, bytes=9_000,
            start=at, end=at + timedelta(seconds=30), direction=Direction.INGRESS,
        ))
    return sessionize(
        records, {"eni-1": "role/deploy"}, {}, {}, origin=start,
        interval=timedelta(seconds=60), inbound_logs_available=True,
    )


def test_signals_over_no_model_traffic_are_unavailable_not_zero():
    """`1 - inbound_coupling` over zero model intervals is 1.0, so the
    strongest signal in the system used to fire at full weight on a workload
    with no model traffic at all."""
    from custos.classify import classify_all

    verdict = classify_all(_telemetry_without_model_traffic())[0]

    assert verdict.features.model_windows == 0
    assert "inbound_decoupling" in verdict.unavailable
    assert "egress_asymmetry" in verdict.unavailable
    assert all(f.id != "inbound_decoupling" for f in verdict.firings)


def test_a_workload_with_no_model_traffic_cannot_be_called_an_agent():
    """Our entire evidence base is model traffic. A workload we cannot see
    making model calls is an unanswered question, not a finding."""
    from custos.classify import Disposition, classify_all

    verdict = classify_all(_telemetry_without_model_traffic())[0]
    assert verdict.disposition is Disposition.NOT_AGENT
    assert verdict.confidence < 0.3


def test_no_evidence_sentence_describes_a_measurement_that_was_not_made():
    """The sentence that was reaching customer reports: "100% of the intervals
    containing model traffic had no request arriving at the load balancer",
    for a workload with no such intervals."""
    from custos.classify import classify_all

    verdict = classify_all(_telemetry_without_model_traffic())[0]
    for line in verdict.evidence:
        assert "intervals containing model traffic" not in line
        assert "to model endpoints and received" not in line


def test_the_asymmetry_evidence_says_the_figures_are_payload():
    """A customer reading this sentence has their own dashboards open, and
    those show wire bytes. The figure here is smaller — acknowledgements, TLS
    handshakes and streaming framing are out of it — so the sentence has to say
    which it is or it reads as a discrepancy."""
    from custos.classify.signals import SIGNALS

    asymmetry = next(s for s in SIGNALS if s.id == "egress_asymmetry")
    sentence = asymmetry.describe(BASE)
    assert "message payload" in sentence, sentence


def test_asymmetry_is_unavailable_when_the_inbound_payload_cannot_be_read():
    """A ratio computed against a payload that clamped to zero is not evidence.

    Taking the protocol out of a byte count can leave nothing behind. A
    principal that sent a great deal to a model endpoint and received only
    acknowledgements — every response failed, or the inbound half of the window
    was lost — has its inbound payload clamp to zero, and the ratio then hits
    its cap: one million to one, the strongest possible activation of the
    heaviest signal, on a conversation nobody could read.

    The same defect as the empty-set one this codebase already carries, one
    layer down. It was found by reading the new code rather than by a
    screenshot this time, which is the only improvement.

    Unavailable is the right answer and not zero, for the reason the module
    header gives: "we could not read the response" and "the response was empty"
    are different claims.
    """
    from custos.classify.engine import score
    from custos.classify.features import Features

    unreadable = dataclasses.replace(
        BASE, total_model_egress=50_000_000, total_model_ingress=0,
        egress_ratio=1e6,
    )
    assert isinstance(unreadable, Features)
    _, firings, unavailable = score(unreadable)

    assert "egress_asymmetry" in unavailable
    assert not any(f.id == "egress_asymmetry" for f in firings)


def test_asymmetry_is_available_whenever_there_is_something_to_read():
    from custos.classify.engine import score

    _, firings, unavailable = score(BASE)
    assert "egress_asymmetry" not in unavailable
    assert any(f.id == "egress_asymmetry" for f in firings)


def test_a_window_that_only_acknowledged_still_counts_its_bytes():
    """The tail of a response spilling into the next interval.

    `has_model` asks whether the workload called a model in this window, and
    answers on outbound payload — which an acknowledgement-only window has
    none of. That is the right denominator for the coupling and interleave
    fractions and the wrong set to sum bytes over: those inbound bytes are real
    and dropping them from the ratio's denominator inflates the ratio, which
    points at false positives.

    Reachable only since byte counts became payload. Before that, the
    acknowledgements themselves counted as outbound model traffic and the
    window was never excluded.
    """
    from datetime import UTC, datetime

    from custos.classify.episodes import PeerTraffic, PrincipalTelemetry, Window, _to_payload
    from custos.classify.features import extract

    t0 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    called = Window(start=t0)
    called.model_peers["160.79.104.10"] = PeerTraffic(
        egress=4_000_000, ingress=200_000, egress_packets=3_000, ingress_packets=400,
    )
    tail = Window(start=t0)
    tail.model_peers["160.79.104.10"] = PeerTraffic(
        egress=40 * 52, ingress=600_000, egress_packets=40, ingress_packets=800,
    )

    t = PrincipalTelemetry(principal="arn:aws:iam::1:role/x", windows=[called, tail])
    _to_payload(t.windows)
    assert not tail.has_model, "the tail is not a call"

    f = extract(t)
    assert f.total_model_ingress > 700_000, f.total_model_ingress
    assert f.model_windows == 1


def test_a_candidate_signal_is_not_a_shipped_one():
    """CANDIDATES records what was measured and not adopted. Nothing in it may
    be wired into the classifier, or the distinction it exists to draw — the
    difference between an idea with numbers behind it and one with a corpus
    behind it — has quietly collapsed."""
    from custos.classify.signals import CANDIDATES, SIGNALS

    shipped = {s.id for s in SIGNALS}
    assert not (shipped & set(CANDIDATES)), shipped & set(CANDIDATES)


def test_candidates_and_rejections_do_not_overlap():
    """A signal is measured-and-wrong or measured-and-unproven, not both."""
    from custos.classify.signals import CANDIDATES, REJECTED

    assert not (set(CANDIDATES) & set(REJECTED))
