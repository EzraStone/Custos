"""Does the classifier survive a setting the customer chose?

Finding 3 established that the classifier is invariant to the flow log
aggregation interval, which is the property that made A0 a result rather than a
demo: a customer on 600-second aggregation gets the same verdicts as one on 60.

Streaming is the same kind of question and nobody had asked it. Whether a
workload's model client streams is a line in their code — not a logging choice
onboarding can ask them to change — and it multiplies the inbound side of every
model call by about forty-four.

The signal that carries this product is `egress_asymmetry`: an agent resends
its accumulated transcript, so it sends far more than it receives. These tests
ask whether that is still true when the response arrives one token at a time.
"""

from __future__ import annotations

import pytest

from custos_a0 import corpus as corpus_mod
from custos_a0.evaluate import SCENARIOS, run
from custos_a0.trace import CallKind, Label

# The tolerance is wide on purpose. This is not asking the feature to be
# identical, it is asking it to describe the same conversation: a factor of two
# either way still lands an agent on the same side of the 7:1 midpoint the
# signal is built around, and anything beyond that does not.
TOLERANCE = 2.0


def _mixed_regime(corpus) -> set[str]:
    """Workloads whose model calls are not all of one kind.

    `kb-assistant` embeds a query and then answers from what it retrieves. The
    embedding response is one JSON array with nothing to stream; the completion
    streams.

    This used to be an exclusion. It is an assertion now: the two halves go to
    different endpoints, a flow log is keyed on the 5-tuple, and the regime is
    decided per destination — so a workload running both is read correctly for
    both. The set is computed so the test below can state that it covers them
    rather than skipping them.
    """
    out = set()
    for w in corpus.workloads:
        kinds = {bool(c.resp_events) for c in w.calls if c.kind is CallKind.MODEL}
        if len(kinds) > 1:
            out.add(w.name)
    return out


@pytest.fixture(scope="module")
def paired():
    """The same corpus read twice, whole and streamed, by workload."""
    corpus = corpus_mod.build()
    whole = {r.workload: r for r in run(SCENARIOS[0], corpus, None).rows}
    streamed = {
        r.workload: r
        for r in run(
            next(s for s in SCENARIOS if s.streaming and s.interval_seconds == 60),
            corpus, None,
        ).rows
    }
    return whole, streamed


def test_the_ratio_describes_the_same_conversation_either_way(paired):
    """The invariance the product needs and does not have.

    A workload's egress-to-ingress ratio is a fact about what it said, not
    about how the reply was framed on the wire. If it moves by more than a
    factor of two because a client passed `stream=True`, the signal is
    measuring the client rather than the workload.
    """
    whole, streamed = paired
    drifted = []
    for name, w in whole.items():
        a = w.verdict.features.egress_ratio
        b = streamed[name].verdict.features.egress_ratio
        if a <= 0 or b <= 0:
            continue
        factor = max(a / b, b / a)
        if factor > TOLERANCE:
            drifted.append(f"{name}: {a:.2f} whole, {b:.2f} streamed ({factor:.0f}x)")
    assert not drifted, "the ratio is measuring the protocol:\n  " + "\n  ".join(drifted)


def test_no_agents_asymmetry_inverts(paired):
    """The sharpest form of the same failure.

    A ratio below 1 says a workload received more than it sent, which is the
    shape of a chatbot answering questions. When that sentence is printed as
    evidence beside a confirmed agent it does not merely fail to support the
    finding — it argues against it, in the one section of the report an owner
    is invited to argue with.
    """
    _, streamed = paired
    inverted = [
        f"{r.workload}: {r.verdict.features.egress_ratio:.2f}:1"
        for r in streamed.values()
        if r.label is Label.AGENT and r.verdict.features.egress_ratio < 1.0
    ]
    assert not inverted, (
        "an agent's own evidence reads as a chatbot's:\n  " + "\n  ".join(inverted)
    )


def test_a_workload_running_both_regimes_is_read_correctly_for_both():
    """The limit that was not one.

    This was written down as something a flow log cannot do: one principal
    embedding a query whole and streaming the answer is two conversations, and
    the decision was being made per principal, so it got one answer for both
    halves.

    They are not the same conversation. The embedding goes to one endpoint and
    the completion to another, a flow log is keyed on the 5-tuple, and the peer
    address is the finest grain the data supports. Deciding there costs
    nothing and removes the limit.
    """
    corpus = corpus_mod.build()
    mixed = _mixed_regime(corpus)
    assert mixed, "the corpus no longer contains a mixed-regime workload"

    whole = {r.workload: r for r in run(SCENARIOS[0], corpus, None).rows}
    streamed = {
        r.workload: r
        for r in run(
            next(s for s in SCENARIOS if s.streaming and s.interval_seconds == 60),
            corpus, None,
        ).rows
    }
    for name in mixed:
        a = whole[name].verdict.features.egress_ratio
        b = streamed[name].verdict.features.egress_ratio
        assert max(a / b, b / a) <= TOLERANCE, f"{name}: {a:.2f} whole, {b:.2f} streamed"
