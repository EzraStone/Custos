"""What a flow record's bytes are made of.

A flow log counts packets on the wire. Everything this product says about a
workload — the ratio that carries the classifier, the token count behind the
dollar figure — is about the conversation inside those packets, and the two are
not the same number.

Four things occupy the inbound byte count of a model conversation:

    payload          the response itself
    acknowledgements one per two outbound segments, 52 bytes each
    handshakes       a TLS server hello and certificate chain per connection
    SSE framing      when the response streams, ~170 bytes per token

The first three are present either way. The fourth is the one that moves, and
it moves by a factor of forty-two — which is why a decomposition that used to
be an academic nicety is now the difference between an agent reading 15:1 and
the same agent reading 0.76:1.

Measured against the A0 corpus with ground truth available: once the
acknowledgements and handshakes are removed, inbound payload is 4.1-4.2 bytes
per token when the response arrives whole and 173.7-173.9 when it streams,
across every workload in the corpus. That is a tight enough band to divide by.
`a0/tests/test_framing.py` holds these constants against that measurement.

Nothing here is a signal and nothing here reads a name. It is arithmetic on
counters the collector already ships.
"""

from __future__ import annotations

ACK_BYTES = 52.0
"""A pure acknowledgement: IPv4 and TCP headers plus timestamps."""

ACKS_PER_SEGMENT = 2
"""Delayed ACK: roughly one acknowledgement per two data segments."""

HANDSHAKE_IN = 5_200.0
"""Server hello and a typical certificate chain, inbound, per connection.

It does not scale with anything the conversation says, so on a low-volume
workload that opens a connection per call it is most of the inbound byte count.
Leaving it in was what made the inbound payload rate look like it varied
between 4.2 and 9.2 bytes per token when it is 4.1 to 4.2."""

SSE_INFLATION = 41.9
"""How much larger a streamed response is on the wire than a whole one.

173.8 / 4.15, both measured. The numerator is protocol arithmetic — an SSE
frame, a TLS record and a TCP segment per token — and the denominator is the
same response as a single body. A constant rather than a per-workload figure
because the measurement found no meaningful spread: every workload in the
corpus lands within a tenth of a byte per token at both ends."""

STREAMED_PACKET_BYTES = 600.0
"""Mean size of an inbound data packet above which responses were not streamed.

Measured empty space: 1,444 to 2,740 bytes when responses arrive whole, 232 to
288 when they stream, nothing between. The gap is a property of the protocol
rather than of the corpus — a sender filling segments produces packets near the
MSS, one flushing per token produces packets the size of one SSE frame."""

MIN_STREAMED_PACKET_BYTES = 100.0
"""And below this it is not a streamed response either.

One SSE frame cannot be smaller than its own envelope: 114 bytes of `event:`
and `data:` around the token, a 29-byte TLS record header and 40 bytes of IP
and TCP. A mean inbound data packet under 100 bytes is therefore not a token
arriving one at a time — it is a byte count that has already had the framing
taken out of it, or a window whose halves do not belong together.

The first of those is the one that matters. `as_if_whole` deflates the bytes
and cannot deflate the packet count, so without a floor here it would look at
its own output and deflate it again — silently, by another factor of
forty-two, on any pipeline that grew a second caller."""


def ack_packets(other_direction_packets: int, own_packets: int) -> int:
    """How many of this direction's packets are acknowledgements.

    Bounded by the packets that exist. A window whose outbound half was
    recorded and whose inbound half was mostly dropped would otherwise imply
    more acknowledgements than packets and produce negative payload.
    """
    if other_direction_packets <= 0 or own_packets <= 0:
        return 0
    return min(other_direction_packets // ACKS_PER_SEGMENT, own_packets)


def data_bytes(
    wire_bytes: int, own_packets: int, other_packets: int, connections: int = 0
) -> float:
    """Bytes in this direction that are neither acknowledgement nor handshake.

    Never negative. The counters come from a flow log rather than from a
    capture we control: a window can hold the outbound half of a conversation
    and not the inbound half, and the arithmetic has to degrade to zero rather
    than to a negative byte count that then divides into a ratio.
    """
    acks = ack_packets(other_packets, own_packets)
    overhead = acks * ACK_BYTES + max(0, connections) * HANDSHAKE_IN
    return max(0.0, wire_bytes - overhead)


def responses_streamed(
    ingress_bytes: int, ingress_packets: int, egress_packets: int
) -> bool:
    """Whether the responses in this conversation arrived one token at a time.

    Inbound packets are not all response data: for an agent resending an
    accumulating transcript the acknowledgements of its own traffic dominate,
    and they are 52 bytes each. Without removing them the inbound mean is
    dragged to 84 bytes and every agent reads as streaming whatever its
    responses actually did.

    Conservative when it cannot tell. No packet counts, or nothing left after
    the acknowledgements, returns False — which is the reading this product
    used before any of this existed.
    """
    if ingress_packets <= 0 or ingress_bytes <= 0:
        return False
    acks = ack_packets(egress_packets, ingress_packets)
    packets = ingress_packets - acks
    if packets <= 0:
        return False
    mean = max(0.0, ingress_bytes - acks * ACK_BYTES) / packets
    return MIN_STREAMED_PACKET_BYTES <= mean < STREAMED_PACKET_BYTES


def as_if_whole(
    ingress_bytes: int, ingress_packets: int, egress_packets: int, connections: int = 0
) -> int:
    """This conversation's inbound bytes as they would read without streaming.

    Deliberately not "the payload". Every threshold and weight in the
    classifier was fitted against non-streaming wire bytes, so the useful
    normalisation is onto that scale rather than onto the truth: it makes the
    feature describe the conversation instead of the client's framing, and it
    moves nothing for the accounts the numbers were fitted on.

    Only the SSE framing is removed. Acknowledgements and handshakes are
    present in both regimes and stay exactly where they are.
    """
    if not responses_streamed(ingress_bytes, ingress_packets, egress_packets):
        return ingress_bytes
    inflated = data_bytes(ingress_bytes, ingress_packets, egress_packets, connections)
    return int(ingress_bytes - inflated * (1 - 1 / SSE_INFLATION))


__all__ = [
    "ACKS_PER_SEGMENT",
    "ACK_BYTES",
    "HANDSHAKE_IN",
    "MIN_STREAMED_PACKET_BYTES",
    "SSE_INFLATION",
    "STREAMED_PACKET_BYTES",
    "ack_packets",
    "as_if_whole",
    "data_bytes",
    "responses_streamed",
]
