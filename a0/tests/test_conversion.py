"""The constants behind every dollar figure, held against the corpus.

`controlplane/custos/framing.py` carries four numbers that were measured here
and cannot be derived from anything in the control plane: how much larger a
streamed response is on the wire, what a TLS handshake costs inbound, and the
two bounds that tell one regime from the other. `spend.py` carries a fifth, the
payload bytes per token.

A constant copied out of a measurement drifts from it silently, which is the
defect class this repository keeps finding. The same pattern as
`test_limits_agree.py` and `test_model_services_agree.py`: two places that have
to agree and nothing making them.
"""

from __future__ import annotations

import pytest
from custos.framing import STREAMED_PACKET_BYTES, responses_streamed
from custos.spend import BYTES_PER_TOKEN

from custos_a0 import corpus as corpus_mod
from custos_a0.conversion import measure

# A workload that streams half its calls and not the other half is read as one
# or the other and is wrong for that half by construction. Excluded by the
# property rather than by name.
MIXED_FLOOR = 1.0


@pytest.fixture(scope="module")
def both():
    corpus = corpus_mod.build()
    return measure(corpus, streaming=True), measure(corpus, streaming=False)


def _pure(rows):
    return [m for m in rows if m.payload_per_token > MIXED_FLOOR]


def test_the_discriminator_reads_every_workload_correctly(both):
    """Both captures, every workload, including the embedding one that does
    not stream even in the streamed capture — which is what makes this a
    measurement of the protocol rather than of the corpus."""
    for rows in both:
        for m in rows:
            read = responses_streamed(
                m.ingress_bytes, m.ingress_packets, m.egress_packets
            )
            assert read is m.streams, (m.workload, m.mean_data_packet)


def test_the_discriminator_sits_in_empty_space(both):
    """A threshold between two adjacent points is a lucky landing rather than a
    finding — the rule the classifier's own thresholds follow."""
    streamed, whole = both
    highest = max(m.mean_data_packet for m in streamed if m.streams)
    lowest = min(
        m.mean_data_packet
        for m in whole + [m for m in streamed if not m.streams]
    )
    assert highest < STREAMED_PACKET_BYTES < lowest
    assert lowest / highest > 4, (highest, lowest)


def test_one_constant_covers_both_regimes(both):
    """The result the arc turned on.

    Four bytes a token was never wrong; it was being applied to wire bytes,
    where it is off by anything from 10% to forty-four times depending on how
    the customer's client is configured. On payload it is one number.
    """
    streamed, whole = both
    rates = [m.payload_per_token for m in _pure(streamed) + _pure(whole)]
    assert max(rates) / min(rates) < 1.05, (min(rates), max(rates))


def test_spend_divides_by_what_was_measured(both):
    """The constant in spend.py against the corpus it came from."""
    streamed, whole = both
    rates = [m.payload_per_token for m in _pure(streamed) + _pure(whole)]
    measured = sum(rates) / len(rates)
    assert abs(BYTES_PER_TOKEN - measured) / measured < 0.02, (
        BYTES_PER_TOKEN, measured
    )


def test_the_mixed_workload_is_still_in_the_corpus(both):
    """The limit this measurement cannot speak for, kept where it can be seen.

    One principal embedding a query whole and streaming the answer gets one
    answer for both halves. If it ever disappears from the corpus the exclusion
    above starts silently covering nothing.
    """
    streamed, _ = both
    assert [m.workload for m in streamed if m.payload_per_token <= MIXED_FLOOR]
