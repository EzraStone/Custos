"""The protocol decomposition, and the cases where it has to give up."""

from __future__ import annotations

from custos.framing import (
    ACK_BYTES,
    HANDSHAKE_IN,
    SSE_INFLATION,
    as_if_whole,
    data_bytes,
    responses_streamed,
)


def test_acknowledgements_come_out_of_the_byte_count():
    """One inbound ACK per two outbound segments, 52 bytes each."""
    assert data_bytes(10_000 + 100 * ACK_BYTES, own_packets=200,
                      other_packets=200) == 10_000


def test_handshakes_come_out_too():
    """A certificate chain does not scale with anything the conversation says,
    so on a workload that opens a connection per call it is most of the inbound
    byte count. Leaving it in made the payload rate look like it varied between
    4.2 and 9.2 bytes per token when it is 4.1 to 4.2."""
    assert data_bytes(10_000 + 3 * HANDSHAKE_IN, own_packets=100,
                      other_packets=0, connections=3) == 10_000


def test_the_decomposition_never_goes_negative():
    """The counters come from a flow log, not from a capture we control. A
    window can hold the outbound half of a conversation and not the inbound
    half, and the arithmetic has to degrade to zero rather than to a negative
    byte count that then divides into a ratio."""
    assert data_bytes(100, own_packets=2, other_packets=100_000,
                      connections=50) == 0.0


def test_more_acknowledgements_than_packets_is_impossible():
    """Bounded by the packets that exist, for the same reason."""
    assert data_bytes(5_000, own_packets=4, other_packets=1_000_000) > 0


# --- which regime -----------------------------------------------------------


def test_a_whole_response_reads_as_whole():
    assert not responses_streamed(2_000 * 1_400, 2_000, egress_packets=100)


def test_a_streamed_response_reads_as_streamed():
    assert responses_streamed(2_000 * 190, 2_000, egress_packets=100)


def test_an_agents_own_acknowledgements_do_not_make_it_look_streamed():
    """The case the subtraction exists for. An agent resends its accumulated
    transcript, so it sends far more segments than it receives responses, and
    enough 52-byte acknowledgements drag the inbound mean below the threshold
    on their own."""
    data_packets, wire = 400, 400 * 1_400
    acks = 20_000
    assert not responses_streamed(
        wire + acks * 52, data_packets + acks, egress_packets=acks * 2
    )


def test_no_packet_counts_is_not_read_as_streamed():
    """An older collector, or a flow log format without the field. Falling back
    to the reading this product used before any of this existed is the
    conservative answer."""
    assert not responses_streamed(5_000_000, 0, 0)


# --- normalising onto the scale the thresholds were fitted on ---------------


def test_a_whole_conversation_is_left_exactly_alone():
    """Every threshold and weight in the classifier was fitted against
    non-streaming wire bytes. The normalisation must move nothing for the
    accounts those numbers came from."""
    assert as_if_whole(2_000 * 1_400, 2_000, 100) == 2_000 * 1_400


def test_a_streamed_conversation_comes_back_to_about_what_it_would_have_been():
    """Payload inflated by the SSE framing, with the acknowledgements and the
    handshake left where they are because they are present either way."""
    payload, acks, handshakes = 40_000, 500, 2
    overhead = acks * ACK_BYTES + handshakes * HANDSHAKE_IN
    streamed_wire = int(payload * SSE_INFLATION + overhead)
    packets = int(payload * SSE_INFLATION / 190) + acks

    got = as_if_whole(streamed_wire, packets, acks * 2, handshakes)
    want = payload + overhead
    assert abs(got - want) / want < 0.02, (got, want)


def test_normalising_is_idempotent():
    """Once normalised the conversation reads as whole, so a second pass is a
    no-op. Nothing calls it twice today; a feature pipeline that grew a second
    caller would silently divide by forty-two again."""
    once = as_if_whole(2_000 * 190, 2_000, 100)
    assert as_if_whole(once, 2_000, 100) == once


def test_a_packet_too_small_to_be_an_sse_frame_is_not_one():
    """The floor, stated directly. One frame cannot be smaller than its own
    envelope — 114 bytes of `event:` and `data:`, a 29-byte TLS record header
    and 40 bytes of IP and TCP — so 30 bytes a packet is not a token arriving
    one at a time."""
    assert not responses_streamed(2_000 * 30, 2_000, egress_packets=0)
