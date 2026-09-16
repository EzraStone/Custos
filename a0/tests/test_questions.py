"""The gateway detector's questions, scored against a corpus that can produce a
bad one.

The base corpus has no internal destination that ordinary infrastructure
floods, so "the detector asks nothing on it" was a statement about the corpus
rather than about the detector. This measures it against seven services that
each have the shape it looks for and none of which is a gateway.

The failure being guarded is not a wrong answer. It is a customer who gets a
handful of questions a week about their log collector, learns the answer is
always no, and stops reading — at which point the one question that matters is
indistinguishable from the rest.
"""

from __future__ import annotations

import pytest

from custos_a0.corpus import CorpusSpec
from custos_a0.questions import run


@pytest.fixture(scope="module")
def measured():
    return run()


def test_a_real_model_endpoint_is_the_first_question_asked(measured):
    """Not merely present. A customer reads top-down and stops, so a detector
    that finds the gateway and asks about a backup service first has not found
    it."""
    assert measured.rank == 1, (
        "no real model endpoint is the first question: "
        f"{[a.address for a in measured.asked]}"
    )
    assert measured.found


def test_at_most_three_questions_are_asked(measured):
    """Seven ordinary services with a gateway's traffic shape, and the list a
    customer sees is short enough to read.

    Three now, and two of them are real: the self-hosted gateway and the
    PrivateLink service a model provider published. The wasted one is the
    agent's own deploy API, which is a fair question — it is reached in the
    same loop and nothing on the wire says which address in a loop is the
    model.
    """
    assert len(measured.shown) <= 3, [a.address for a in measured.shown]


def test_every_real_endpoint_that_can_be_asked_about_is(measured):
    """Two of the corpus's three model endpoints reach this list. Missing
    either is the failure the whole mechanism exists to prevent, and it is
    invisible in a precision figure — a detector that asks one honest question
    and misses the other scores 1.00."""
    from custos_a0.endpoints import PROVIDER_PRIVATELINK
    from custos_a0.scenarios.hard import GATEWAY

    shown = {a.address for a in measured.shown}
    assert {GATEWAY.ip, PROVIDER_PRIVATELINK.ip} <= shown, sorted(shown)


def test_the_endpoint_aws_will_not_explain_is_asked_about_first(measured):
    """Both real questions are shown, so which one is at the top is the whole
    difference between them. Every other candidate is a machine inside the
    account; this one is a door into another company, and nobody in the
    account can answer it by going and looking."""
    from custos_a0.endpoints import PROVIDER_PRIVATELINK

    assert measured.shown[0].address == PROVIDER_PRIVATELINK.ip
    assert measured.shown[0].candidate.published_endpoint


def test_precision_is_not_worse_than_two_thirds(measured):
    assert measured.precision >= 2 / 3


def test_no_bulk_sender_is_asked_about(measured):
    """Named individually rather than counted, because each is a different
    reason a customer would learn to ignore the list."""
    from custos_a0.endpoints import (
        ARTIFACT_REGISTRY,
        BACKUP_SVC,
        EVENT_PROXY,
        LOG_COLLECTOR,
        METRICS_PUSH,
    )

    asked = {a.address for a in measured.asked}
    for endpoint in (LOG_COLLECTOR, BACKUP_SVC, METRICS_PUSH,
                     ARTIFACT_REGISTRY, EVENT_PROXY):
        assert endpoint.ip not in asked, f"{endpoint.host} drew a question"


def test_a_corpus_with_no_hidden_gateway_still_asks_nothing():
    """The result that mattered before the noise existed, and it has to
    survive the fix: an account where every agent reaches a provider we
    recognise has nothing hidden and should be asked nothing."""
    assert run(spec=CorpusSpec()).asked == ()


def test_noise_alone_asks_nothing_at_all():
    """No gateway, seven services that look like one. Every question here
    would be a question a customer answers no to, and there should be none."""
    assert run(spec=CorpusSpec(noise=True)).asked == ()


def test_the_endpoint_aws_named_is_not_asked_about(measured):
    """The best outcome for a question is not being asked. A Bedrock interface
    VPC endpoint is resolved by the collector before any question exists, so
    the customer is never interrupted about the one case where the answer was
    available for the asking."""
    from custos_a0.endpoints import BEDROCK_PRIVATELINK
    from custos_a0.scenarios.hard import GATEWAY

    asked = {a.address for a in measured.shown}
    assert GATEWAY.ip in asked, "the self-hosted gateway is still a question"
    assert BEDROCK_PRIVATELINK.ip not in asked


def test_the_noise_produces_no_agents_either():
    """The noise exists to measure the gateway detector, not the classifier —
    but it is worth knowing which, because these workloads have two of the
    three properties the decoupling signal reads as agent-shaped: no inbound
    requests, machine-triggered bursts.

    They score at the floor because they make no model call, so the signals
    that would carry them have nothing to measure. That is the classifier
    being correct for the right reason rather than by luck, and if it ever
    stops being true the number moves here first.
    """
    from custos.classify import classify_all, sessionize
    from custos.pipeline import to_scan_input

    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    inp = to_scan_input(build_batch(corpus.build(CorpusSpec(noise=True))))
    telemetry = sessionize(
        inp.records, inp.principal_by_eni, inp.address_by_eni, inp.requests,
        origin=inp.start, interval=inp.interval,
    )
    noise = {
        "log-forwarder", "backup-agent", "metrics-push", "ci-publisher",
        "clickstream", "image-pipeline", "contract-ingest",
    }
    scored = {
        v.principal.rsplit("/", 1)[-1]: v
        for v in classify_all(telemetry)
        if v.principal.rsplit("/", 1)[-1] in noise
    }
    assert set(scored) == noise, f"the noise was not scored at all: {sorted(scored)}"
    for name, verdict in scored.items():
        assert str(verdict.disposition) == "not_agent", f"{name} is {verdict.disposition}"
        assert verdict.confidence < 0.1, f"{name} scored {verdict.confidence:.3f}"


def test_the_detector_still_asks_when_the_account_streams():
    """The blind spot inside the mechanism that exists to cover a blind spot.

    A self-hosted model gateway proxies a model API, which means it proxies
    Server-Sent Events: its responses arrive one token at a time like the ones
    behind it. The detector looks for a destination that receives far more than
    it returns, and it was reading tool traffic as wire bytes — where forty-two
    bytes of framing per token inflate the inbound side until the ratio falls
    under the threshold.

    The result is not a worse ranking. It is silence: zero questions asked, on
    an account whose clients stream. And a report with no findings and no
    questions is precisely the artefact a hidden gateway produces, which is the
    thing this whole mechanism exists to prevent.
    """
    from custos_a0.scenarios.hard import GATEWAY

    asked = {a.address for a in run(streaming=True).asked}
    assert GATEWAY.ip in asked, sorted(asked)
