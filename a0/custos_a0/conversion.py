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

from custos.framing import HANDSHAKE_IN, SSE_INFLATION

from .endpoints import ANTHROPIC, BEDROCK, OPENAI
from .trace import CallKind, Corpus
from .wire import AggregationConfig, aggregate
from .wire.record import SYN, Direction

MODEL_ADDRESSES = frozenset({ANTHROPIC.ip, OPENAI.ip, BEDROCK.ip})

ENDPOINT_NAMES = {ANTHROPIC.ip: "anthropic", OPENAI.ip: "openai", BEDROCK.ip: "bedrock"}

ACK_BYTES = 52
"""A pure acknowledgement, as the wire model emits them."""


@dataclass(frozen=True, slots=True)
class Measured:
    """One workload's model conversation, as a collector would see it."""

    workload: str
    endpoint: str = ""
    """Which model endpoint this conversation was with.

    A row is one conversation rather than one workload, because the regime is
    decided per destination and a row summing two of them measures neither."""

    output_tokens: float = 0.0
    """Ground truth. Known here and never knowable in a customer's account,
    which is the whole reason this is measured against a corpus."""

    ingress_bytes: int = 0
    ingress_packets: int = 0
    egress_packets: int = 0
    streams: bool = False
    """Ground truth again: whether this workload's responses were streamed."""

    connections: int = 0
    """TLS connections opened to model endpoints. The certificate chain comes
    back inbound on each one and does not scale with the conversation, so on a
    workload that opens a connection per call it is most of the byte count."""

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
    def payload_per_token(self) -> float:
        """What the conversion constant would have to be for this workload,
        once the protocol is out of the way.

        The measurement the whole arc turned on. Before the acknowledgements
        and the certificate chains were removed this read between 4.2 and 9.2
        for a whole response, which looked like a constant that varied by
        workload and was a handshake that does not scale with anything the
        conversation says.
        """
        data = self.data_bytes - self.handshake_bytes
        if self.streams:
            data /= SSE_INFLATION
        return max(data, 0.0) / max(self.output_tokens, 1)

    @property
    def handshake_bytes(self) -> float:
        return self.connections * HANDSHAKE_IN


def measure(corpus: Corpus, streaming: bool) -> list[Measured]:
    """One capture, read the way the control plane reads it.

    One row per conversation — per (workload, model endpoint) — rather than per
    workload, because that is the grain the regime is decided at and a row that
    summed two conversations would be measuring neither. `kb-assistant` is the
    case that makes the difference visible: its embedding call goes to one
    endpoint whole and its completion to another streamed, and as a single row
    it landed between the two constants and fitted neither.
    """
    truth: dict[tuple[str, str], tuple[str, float, bool]] = {}
    for w in corpus.workloads:
        for call in w.calls:
            if call.kind is not CallKind.MODEL:
                continue
            key = (w.src_ip, call.endpoint.ip)
            name, tokens, streams = truth.get(key, (w.name, 0.0, False))
            truth[key] = (
                name,
                tokens + call.resp_bytes / 4,
                streams or bool(call.resp_events),
            )

    totals: dict[tuple[str, str], list[int]] = {}
    for r in aggregate(corpus, AggregationConfig(streaming=streaming)).records:
        if r.direction is Direction.INGRESS and r.srcaddr in MODEL_ADDRESSES:
            row = totals.setdefault((r.dstaddr, r.srcaddr), [0, 0, 0, 0])
            row[0] += r.bytes
            row[1] += r.packets
        elif r.direction is Direction.EGRESS and r.dstaddr in MODEL_ADDRESSES:
            row = totals.setdefault((r.srcaddr, r.dstaddr), [0, 0, 0, 0])
            row[2] += r.packets
            if r.tcp_flags & SYN:
                row[3] += 1

    out = []
    for key, (ingress_bytes, ingress_packets, egress_packets, conns) in totals.items():
        name, tokens, streams = truth[key]
        if tokens < 100:
            continue
        out.append(Measured(
            workload=name, endpoint=ENDPOINT_NAMES.get(key[1], key[1]),
            output_tokens=tokens,
            ingress_bytes=ingress_bytes, ingress_packets=ingress_packets,
            egress_packets=egress_packets, connections=conns,
            streams=streams and streaming,
        ))
    out.sort(key=lambda m: -m.output_tokens)
    return out
