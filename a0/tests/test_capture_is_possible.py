"""Could a network have produced this capture?

Every measured number in this repository comes out of `custos_a0.wire`: the
classifier's margins, the payload constants, the discriminator's empty space.
All of them are properties of a synthetic capture, and nothing checked that the
capture obeys the physics of the medium it claims to describe.

That gap matters more than it sounds. `CONTRIBUTING.md` records three times the
corpus has been wrong in the flattering direction, and a byte model that quietly
produced impossible packets would be wrong in whichever direction its author
was not thinking about. These are the constraints an Ethernet path imposes, and
they are cheap to assert.

They are necessary rather than sufficient. A capture can satisfy every one of
these and still be nothing like a customer's account — that question needs an
account. This is the floor.
"""

from __future__ import annotations

import pytest

from custos_a0 import corpus as corpus_mod
from custos_a0.wire import AggregationConfig, aggregate

IP_TCP_HEADER = 40
"""No IPv4 TCP packet is smaller than its own headers."""

MTU = 1500
"""And none is larger than the path allows. AWS supports jumbo frames inside a
VPC, but not to the internet, and every model endpoint in this corpus is
outside it."""


@pytest.fixture(scope="module")
def captures():
    corpus = corpus_mod.build(corpus_mod.CorpusSpec(hard=True, noise=True))
    return {
        "whole": aggregate(corpus, AggregationConfig()),
        "streamed": aggregate(corpus, AggregationConfig(streaming=True)),
        "batched": aggregate(
            corpus, AggregationConfig(streaming=True, tokens_per_frame=8)
        ),
    }


def test_no_packet_is_smaller_than_its_own_headers(captures):
    for name, capture in captures.items():
        bad = [r for r in capture.records if r.bytes < r.packets * IP_TCP_HEADER]
        assert not bad, f"{name}: {len(bad)} records below the header floor"


def test_no_packet_is_larger_than_the_path_allows(captures):
    for name, capture in captures.items():
        bad = [r for r in capture.records if r.bytes > r.packets * MTU]
        assert not bad, f"{name}: {len(bad)} records above the MTU"


def test_bytes_and_packets_agree_about_whether_anything_happened(captures):
    """A record with packets and no bytes, or bytes and no packets, is not a
    lossy reading of something real — it is arithmetic that got away."""
    for name, capture in captures.items():
        for r in capture.records:
            assert bool(r.bytes) == bool(r.packets), (name, r.bytes, r.packets)


def test_the_capture_spans_the_window_it_claims_to(captures):
    """Every record inside the collection window. A record outside it is one
    the aggregation would drop in production and counts here."""
    for name, capture in captures.items():
        for r in capture.records:
            assert capture.start <= r.start <= capture.end, (name, r.start)
            assert r.start <= r.end, (name, r.start, r.end)


def test_streaming_moves_packets_without_inventing_bytes_from_nothing(captures):
    """The specific claim the framing correction rests on: streaming is more
    packets carrying the same conversation, not a different conversation.

    Outbound payload is unchanged between the two captures — the workload said
    the same thing — while inbound grows. If outbound moved too, the corpus
    would be modelling two different workloads and every comparison between the
    captures would be meaningless."""
    from custos_a0.endpoints import ANTHROPIC, BEDROCK, OPENAI
    from custos_a0.wire.record import Direction

    model = {ANTHROPIC.ip, OPENAI.ip, BEDROCK.ip}

    def totals(capture):
        out = sum(
            r.bytes for r in capture.records
            if r.direction is Direction.EGRESS and r.dstaddr in model
        )
        inn = sum(
            r.bytes for r in capture.records
            if r.direction is Direction.INGRESS and r.srcaddr in model
        )
        return out, inn

    whole_out, whole_in = totals(captures["whole"])
    stream_out, stream_in = totals(captures["streamed"])

    # Model traffic specifically. Measured across the whole capture the answer
    # is 1.6x and means nothing: tool calls and datastore reads dominate the
    # inbound total and none of them stream.
    assert stream_in > whole_in * 5, (whole_in, stream_in)
    # Outbound grows only by the acknowledgements answering the extra inbound
    # packets, which is a fraction rather than a multiple.
    assert stream_out < whole_out * 1.6, (whole_out, stream_out)


def test_batching_tokens_makes_the_stream_smaller_not_larger(captures):
    """Eight tokens to a frame pays the envelope once for eight. If the byte
    model made batching *more* expensive the measurement built on it would have
    the sign of the error backwards."""
    streamed = sum(r.bytes for r in captures["streamed"].records)
    batched = sum(r.bytes for r in captures["batched"].records)
    assert batched < streamed, (batched, streamed)
