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


@pytest.fixture(scope="module")
def both():
    corpus = corpus_mod.build()
    return measure(corpus, streaming=True), measure(corpus, streaming=False)


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
    rates = [m.payload_per_token for m in streamed + whole]
    assert max(rates) / min(rates) < 1.05, (min(rates), max(rates))


def test_spend_divides_by_what_was_measured(both):
    """The constant in spend.py against the corpus it came from."""
    streamed, whole = both
    rates = [m.payload_per_token for m in streamed + whole]
    measured = sum(rates) / len(rates)
    assert abs(BYTES_PER_TOKEN - measured) / measured < 0.02, (
        BYTES_PER_TOKEN, measured
    )


def test_a_workload_running_both_regimes_is_measured_as_both(both):
    """The exclusion that turned into a row.

    One principal embedding a query whole and streaming the answer used to
    land between the two constants and fit neither, and that was written down
    as something a flow log cannot resolve. The two halves go to different
    endpoints; a row is one conversation now, and both of them are in the band
    above with nothing excluded from it.
    """
    streamed, _ = both
    by_workload: dict[str, set[bool]] = {}
    for m in streamed:
        by_workload.setdefault(m.workload, set()).add(m.streams)

    mixed = {name for name, kinds in by_workload.items() if len(kinds) > 1}
    assert mixed, "the corpus no longer contains a workload running both regimes"
    for m in streamed:
        if m.workload in mixed:
            assert 3.9 < m.payload_per_token < 4.4, (m.workload, m.endpoint)


# --- what the correction assumes about the provider -------------------------


@pytest.mark.parametrize("per_frame,floor", [(1, 4.10), (2, 2.10), (5, 0.85), (10, 0.45)])
def test_a_provider_that_batches_tokens_is_over_corrected(per_frame, floor):
    """The calibration's own assumption, measured rather than asserted away.

    The inflation constant is 41.9 because one token rides in one Server-Sent
    Events frame, pays one 114-byte envelope, one TLS record header and one
    packet header. Anthropic and OpenAI do that. A provider batching five
    tokens into a frame pays the envelope once for all five, so its stream is
    roughly a quarter of the size — and a correction assuming one token a frame
    removes about four times too much.

    The direction is the part that matters. Over-correcting the inbound side
    makes the payload look smaller, which makes the egress-to-ingress ratio
    look *larger*, which makes a workload look more like an agent. The error
    points at false positives, and a false positive is the failure this product
    can least afford.

    Nothing corrects for it. The mean packet size does move with batching — 232
    bytes at one token a frame, 341 at twenty — but not in a way that recovers
    the factor: the implied inflation reads 4.7 where the truth is 41.9,
    because TCP coalescing means packets are not frames. Deriving a correction
    from that would be inventing a number, so this measures the error instead.
    """
    from custos_a0.wire import AggregationConfig

    corpus = corpus_mod.build()
    rows = measure(
        corpus, streaming=True,
        config=AggregationConfig(streaming=True, tokens_per_frame=per_frame),
    )
    streamed = [m.payload_per_token for m in rows if m.streams]
    assert streamed, per_frame
    assert abs(min(streamed) - floor) < 0.15, (per_frame, min(streamed))


def test_batching_never_changes_which_regime_a_conversation_reads_as():
    """The discriminator survives what the constant does not.

    A batched stream is still a stream — packets the size of a frame rather
    than of a segment — so it is still read as streamed and still corrected,
    just by the wrong factor. That is the better failure of the two: being
    read as whole would leave the framing in entirely.
    """
    from custos_a0.wire import AggregationConfig

    corpus = corpus_mod.build()
    for per_frame in (1, 2, 5, 10, 20):
        rows = measure(
            corpus, streaming=True,
            config=AggregationConfig(streaming=True, tokens_per_frame=per_frame),
        )
        streamed = sum(1 for m in rows if m.streams)
        assert streamed == 10, (per_frame, streamed)
