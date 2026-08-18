"""Turn ground-truth calls into the telemetry a collector could actually read.

This is the lossy step. Everything the classifier sees comes out of here, and
everything it cannot see was destroyed here.

The output deliberately contains no workload name, scenario, or label. The only
identity it carries is what AWS would genuinely give us: an ENI, the principal
attached to it, and a target address in the load balancer's access log.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..trace import CallKind, Corpus
from .connpool import (
    DEFAULT_KEEPALIVE,
    HANDSHAKE_IN,
    HANDSHAKE_OUT,
    ConnectionPool,
    ack_traffic,
    framed,
)
from .record import ACK, PSH, SYN, Direction, FlowRecord, InboundRequest


@dataclass(frozen=True, slots=True)
class AggregationConfig:
    """How the customer has their flow logs configured.

    `interval` is the one that matters. AWS offers 60 or 600 seconds. Ten
    minutes is meaningfully cheaper and is what a cost-conscious platform team
    will already have set, so the classifier has to work at 600 or the
    onboarding ask grows a step.
    """

    interval: timedelta = timedelta(seconds=60)
    keepalive: timedelta = DEFAULT_KEEPALIVE
    account_id: str = "447120043318"
    have_alb_logs: bool = True
    """Whether the customer gave us load balancer access logs. A0 measures how
    much the classifier loses without them, because some customers will not."""


@dataclass(slots=True)
class Capture:
    """Everything a collector could ship for one observation window."""

    start: datetime
    end: datetime
    config: AggregationConfig
    records: list[FlowRecord] = field(default_factory=list)
    requests: dict[str, list[InboundRequest]] = field(default_factory=dict)
    """ALB access log lines, keyed by target address."""
    principal_by_eni: dict[str, str] = field(default_factory=dict)
    """Resolved from ENI attachment and instance profile, as the collector
    would resolve it from CloudTrail and describe calls."""
    subnet_by_eni: dict[str, str] = field(default_factory=dict)
    address_by_eni: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.records)


@dataclass(slots=True)
class _Bucket:
    """Accumulator for one 5-tuple in one interval, one direction."""

    wire_bytes: int = 0
    packets: int = 0
    first: datetime | None = None
    last: datetime | None = None
    flags: int = 0

    def add(self, wire_bytes: int, packets: int, at: datetime, flags: int) -> None:
        self.wire_bytes += wire_bytes
        self.packets += packets
        self.first = at if self.first is None else min(self.first, at)
        self.last = at if self.last is None else max(self.last, at)
        self.flags |= flags


def _floor(t: datetime, origin: datetime, interval: timedelta) -> datetime:
    elapsed = (t - origin) // interval
    return origin + elapsed * interval


def aggregate(corpus: Corpus, config: AggregationConfig | None = None) -> Capture:
    """Collapse ground-truth calls into aggregated flow log records."""
    cfg = config or AggregationConfig()
    cap = Capture(start=corpus.start, end=corpus.end, config=cfg)

    for w in corpus.workloads:
        cap.principal_by_eni[w.eni] = w.principal
        cap.subnet_by_eni[w.eni] = w.subnet
        cap.address_by_eni[w.eni] = w.src_ip

        pool = ConnectionPool(keepalive=cfg.keepalive)
        # (window, srcport, dstaddr, dstport, direction) -> bucket
        buckets: dict[tuple[datetime, int, str, int, Direction], _Bucket] = defaultdict(_Bucket)
        aws_service: dict[str, str] = {}

        for call in w.calls:
            if call.kind is CallKind.INBOUND:
                if cfg.have_alb_logs:
                    cap.requests.setdefault(w.src_ip, []).append(
                        InboundRequest(
                            at=call.at, target=w.src_ip,
                            received_bytes=call.req_bytes, sent_bytes=call.resp_bytes,
                        )
                    )
                continue

            ep = call.endpoint
            conn = pool.acquire(ep.ip, ep.port, call.at)
            window = _floor(call.at, corpus.start, cfg.interval)
            if ep.aws_service:
                aws_service[ep.ip] = ep.aws_service

            out_payload, in_payload = call.req_bytes, call.resp_bytes
            flags = ACK | PSH

            if conn.is_new:
                # A fresh connection pays for the handshake before any payload.
                out_payload += HANDSHAKE_OUT
                in_payload += HANDSHAKE_IN
                flags |= SYN
                conn.is_new = False

            out_bytes, out_pkts = framed(out_payload)
            in_bytes, in_pkts = framed(in_payload)
            # Each direction also carries the acknowledgements for the other.
            out_ack_bytes, out_ack_pkts = ack_traffic(in_pkts)
            in_ack_bytes, in_ack_pkts = ack_traffic(out_pkts)

            key = (window, conn.srcport, ep.ip, ep.port)
            buckets[(*key, Direction.EGRESS)].add(
                out_bytes + out_ack_bytes, out_pkts + out_ack_pkts, call.at, flags
            )
            buckets[(*key, Direction.INGRESS)].add(
                in_bytes + in_ack_bytes, in_pkts + in_ack_pkts, call.at, flags
            )

        for (window, srcport, dstaddr, dstport, direction), b in buckets.items():
            assert b.first is not None and b.last is not None
            window_end = min(window + cfg.interval, corpus.end)
            cap.records.append(
                FlowRecord(
                    account_id=cfg.account_id,
                    interface_id=w.eni,
                    srcaddr=w.src_ip if direction is Direction.EGRESS else dstaddr,
                    dstaddr=dstaddr if direction is Direction.EGRESS else w.src_ip,
                    srcport=srcport if direction is Direction.EGRESS else dstport,
                    dstport=dstport if direction is Direction.EGRESS else srcport,
                    protocol=6,
                    packets=b.packets,
                    bytes=b.wire_bytes,
                    start=b.first,
                    end=max(b.last, min(window_end, b.last)),
                    direction=direction,
                    subnet_id=w.subnet,
                    dst_aws_service=aws_service.get(dstaddr, ""),
                    tcp_flags=b.flags,
                )
            )

    cap.records.sort(key=lambda r: (r.start, r.interface_id, r.srcport, r.direction))
    return cap
