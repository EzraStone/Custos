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
    egress_ratio=1.0, inbound_coupling=1.0, egress_per_inbound_request=1000.0,
    tool_interleave=0.0, distinct_tool_addresses=0, mcp_windows=0,
    median_episode_windows=2.0, p90_episode_windows=3.0, ratio_growth=1.0,
    offhours_egress_fraction=0.0, episodes=10,
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
