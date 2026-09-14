"""What a streamed response costs on the wire.

The corpus has always modelled a model call's response as four bytes per
output token — the same rule of thumb it uses for the request. That is right
for a non-streaming JSON body and wrong for a streamed one, by a factor this
module makes explicit.

It is arithmetic rather than a measurement. Every model provider's streaming
API is Server-Sent Events over HTTP, the envelope is documented, and what
follows from flushing after every token is TLS records and TCP segments. What
is unmeasured is the only part that needs an account to answer: how much real
agent traffic streams.
"""

from __future__ import annotations

from custos_a0.wire.connpool import (
    IP_TCP_HEADER,
    SSE_EVENT_OVERHEAD,
    TLS_RECORD_OVERHEAD,
    framed,
    streamed,
)


def test_a_streamed_token_costs_far_more_than_the_four_bytes_it_is():
    """The finding, as one number.

    500 output tokens is an ordinary model reply. As a JSON body it is two
    kilobytes on the wire. Streamed, every token is its own event, its own TLS
    record and its own segment.
    """
    tokens = 500
    payload = tokens * 4
    body, _ = framed(payload)
    stream, _ = streamed(payload, events=tokens)

    assert stream / body > 40, (body, stream)
    # Per token, to the byte: the text, the SSE envelope, the record header
    # and the packet headers.
    assert stream / tokens == (
        4 + SSE_EVENT_OVERHEAD + TLS_RECORD_OVERHEAD + IP_TCP_HEADER
    )


def test_a_response_that_is_not_streamed_is_unchanged():
    """`streamed` with no events is `framed`. The corpus has to be able to
    model both, because which one a customer's agents do is the open question
    and not something to decide by making it unrepresentable."""
    assert streamed(2_000, events=0) == framed(2_000)


def test_one_event_is_almost_a_plain_response():
    """A provider that sends the whole completion in a single SSE event pays
    the envelope once. The cost is per flush, not per byte, which is what makes
    it a question about streaming rather than about protocol overhead."""
    payload = 2_000
    once, _ = streamed(payload, events=1)
    plain, _ = framed(payload)
    assert once - plain == SSE_EVENT_OVERHEAD


def test_a_flush_larger_than_a_segment_still_pays_for_its_packets():
    """Some providers batch several tokens per event. The envelope is paid once
    per event either way, and a large event still costs the segments it needs —
    a model that divided total bytes by the MSS would undercount."""
    huge, packets = streamed(200_000, events=4)
    assert packets >= 4 * (50_000 // 1460)
    assert huge > 200_000


def test_nothing_sent_costs_nothing():
    assert streamed(0, events=10) == (0, 0)
