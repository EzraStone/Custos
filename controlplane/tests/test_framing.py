"""The protocol decomposition, and the cases where it has to give up."""

from __future__ import annotations

from custos.framing import (
    ACK_BYTES,
    HANDSHAKE_IN,
    SSE_INFLATION,
    data_bytes,
    payload_in,
    payload_out,
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


# --- wire bytes to payload --------------------------------------------------


def test_a_whole_conversation_loses_only_its_overhead():
    payload, acks, handshakes = 40_000, 500, 2
    wire = int(payload + acks * ACK_BYTES + handshakes * HANDSHAKE_IN)
    got = payload_in(wire, 600, acks * 2, handshakes, streamed=False)
    assert abs(got - payload) < 1


def test_a_streamed_conversation_comes_back_to_its_payload():
    """The framing removed on top of the overhead. Forty-two times, which is
    the whole reason this module exists."""
    payload, acks, handshakes = 40_000, 500, 2
    inflated = payload * SSE_INFLATION
    wire = int(inflated + acks * ACK_BYTES + handshakes * HANDSHAKE_IN)
    packets = int(inflated / 190) + acks

    got = payload_in(wire, packets, acks * 2, handshakes, streamed=True)
    assert abs(got - payload) / payload < 0.02, (got, payload)


def test_the_outbound_side_loses_the_acknowledgements_it_grew():
    """Easy to miss, and it points the same way as the thing being measured.
    Streaming multiplies the inbound packet count by forty-two and every two of
    those are answered outbound, so the numerator of the ratio grows too."""
    payload, inbound_packets = 5_000_000, 40_000
    acks = inbound_packets // 2
    wire = payload + acks * int(ACK_BYTES)
    got = payload_out(wire, egress_packets=acks + 4_000,
                      ingress_packets=inbound_packets)
    assert abs(got - payload) < 1


def test_an_already_corrected_figure_is_not_read_as_streamed_again():
    """Deflating bytes cannot deflate packets, so a second pass would read its
    own output as a streamed conversation and divide by forty-two again. The
    floor is what stops it."""
    once = payload_in(2_000 * 190, 2_000, 100, streamed=True)
    assert not responses_streamed(int(once), 2_000, 100)


def test_a_packet_too_small_to_be_an_sse_frame_is_not_one():
    """The floor, stated directly. One frame cannot be smaller than its own
    envelope — 114 bytes of `event:` and `data:`, a 29-byte TLS record header
    and 40 bytes of IP and TCP — so 30 bytes a packet is not a token arriving
    one at a time."""
    assert not responses_streamed(2_000 * 30, 2_000, egress_packets=0)


def test_too_few_packets_to_average_over_is_not_a_verdict():
    """Six packets carrying 2,400 bytes average 480 each, which is under the
    threshold and would read as streamed — recording a conversation that
    carried 2,350 bytes of payload as 56.

    A 42x error from a sample that cannot support the question. The floor is
    not a statistical bound; it is the point below which a streamed response
    is too short for the distinction to change anything."""
    assert not responses_streamed(2_400, 6, egress_packets=2)
    assert abs(payload_in(2_400, 6, 2, 0, streamed=False) - 2_348) < 5


def test_the_floor_does_not_reject_a_real_stream():
    """A stream of any length worth reading about clears it easily: one packet
    per token, and fifty tokens is a sentence."""
    tokens = 400
    assert responses_streamed(tokens * 190, tokens, egress_packets=40)
