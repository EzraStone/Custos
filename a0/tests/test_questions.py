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


def test_the_real_gateway_is_the_first_question_asked(measured):
    """Not merely present. A customer reads top-down and stops, so a detector
    that finds the gateway and asks about a backup service first has not found
    it."""
    assert measured.rank == 1, (
        "the real gateway is not the first question: "
        f"{[a.address for a in measured.asked]}"
    )
    assert measured.found


def test_at_most_two_questions_are_asked(measured):
    """Seven ordinary services with a gateway's traffic shape, and the list a
    customer sees is short enough to read. The second is the agent's own
    deploy API, which is a fair question — it is reached in the same loop and
    nothing on the wire says which of the two is the model."""
    assert len(measured.shown) <= 2, [a.address for a in measured.shown]


def test_precision_is_not_worse_than_a_half(measured):
    assert measured.precision >= 0.5


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
