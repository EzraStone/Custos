"""Wire bytes to tokens, measured rather than assumed.

The dollar figure on a Custos report is the number that gets it forwarded to
somebody with a budget, and every one of them comes from the same conversion:
observed wire bytes, divided by a constant, priced per million tokens. That
constant has been four since A0 — the usual rule of thumb for English
serialised into a messages array — and `docs/STATUS.md` has called it a guess
"wrong in a direction nobody has measured" since spend attribution landed.

This measures it. Not the tokenisation, which needs a tokeniser and a corpus of
real prompts; the part that is arithmetic on a documented protocol, and that
turns out to dominate.

A model API that streams flushes after every token, so each token is its own
Server-Sent Events frame, its own TLS record and its own TCP segment. Dividing
those bytes by four does not overstate the token count slightly. It overstates
it by around forty-four times, on the side of the conversation that is priced
highest.

Two numbers come out of this, and both are constants in
`controlplane/custos/spend.py`:

    bytes per output token, streamed    what to divide by
    mean inbound data packet size       how to tell which regime you are in

The second exists because a flow record does not say whether a response
streamed, and without it the first is a choice between two answers forty-four
times apart with nothing to choose on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .endpoints import ANTHROPIC, BEDROCK, OPENAI
from .trace import CallKind, Corpus
from .wire import AggregationConfig, aggregate
from .wire.record import Direction

MODEL_ADDRESSES = frozenset({ANTHROPIC.ip, OPENAI.ip, BEDROCK.ip})

ACK_BYTES = 52
"""A pure acknowledgement, as the wire model emits them."""


@dataclass(frozen=True, slots=True)
class Measured:
    """One workload's model conversation, as a collector would see it."""

    workload: str
    output_tokens: float
    """Ground truth. Known here and never knowable in a customer's account,
    which is the whole reason this is measured against a corpus."""

    ingress_bytes: int
    ingress_packets: int
    egress_packets: int
    streams: bool
    """Ground truth again: whether this workload's responses were streamed."""

    @property
    def ack_packets(self) -> int:
        return self.egress_packets // 2

    @property
    def data_packets(self) -> int:
        return max(self.ingress_packets - self.ack_packets, 1)

    @property
    def data_bytes(self) -> float:
        return max(self.ingress_bytes - self.ack_packets * ACK_BYTES, 0)

    @property
    def mean_data_packet(self) -> float:
        """The discriminator: mean size of an inbound packet that is not an ACK."""
        return self.data_bytes / self.data_packets

    @property
    def bytes_per_output_token(self) -> float:
        """What the conversion constant would have to be for this workload."""
        return self.ingress_bytes / max(self.output_tokens, 1)


def measure(corpus: Corpus, streaming: bool) -> list[Measured]:
    """One capture, read the way the control plane reads it.

    Embedding workloads are included rather than filtered. They are the case
    that proves the discriminator is measuring the protocol rather than the
    corpus: an embedding response is one JSON array with nothing to stream, so
    it stays whole in a streamed capture and has to be read as whole.
    """
    truth = {
        w.src_ip: (
            w.name,
            sum(c.resp_bytes / 4 for c in w.calls if c.kind is CallKind.MODEL),
            any(c.resp_events for c in w.calls if c.kind is CallKind.MODEL),
        )
        for w in corpus.workloads
    }

    totals: dict[str, list[int]] = {}
    for r in aggregate(corpus, AggregationConfig(streaming=streaming)).records:
        if r.direction is Direction.INGRESS and r.srcaddr in MODEL_ADDRESSES:
            row = totals.setdefault(r.dstaddr, [0, 0, 0])
            row[0] += r.bytes
            row[1] += r.packets
        elif r.direction is Direction.EGRESS and r.dstaddr in MODEL_ADDRESSES:
            totals.setdefault(r.srcaddr, [0, 0, 0])[2] += r.packets

    out = []
    for address, (ingress_bytes, ingress_packets, egress_packets) in totals.items():
        name, tokens, streams = truth[address]
        if tokens < 100:
            continue
        out.append(Measured(
            workload=name, output_tokens=tokens,
            ingress_bytes=ingress_bytes, ingress_packets=ingress_packets,
            egress_packets=egress_packets,
            streams=streams and streaming,
        ))
    out.sort(key=lambda m: -m.output_tokens)
    return out
