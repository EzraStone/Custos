"""The telemetry contract between the collector and the control plane.

These are the only types that cross the boundary out of a customer account.
They live in the control plane package rather than in the experiment because
they are a product interface: the Go collector emits this shape, the classifier
consumes it, and the A0 experiment synthesises it. One definition, three
consumers, so none of them can drift.

Field names and ordering match the AWS log format string in `LOG_FORMAT`, so
fixtures are byte-comparable with what CloudWatch Logs delivers.

SEC-18 is enforced structurally here. No type in this module has a field
capable of holding a payload byte, and `test_sec18_no_payload_fields` walks
every type by reflection and fails on any field it does not recognise. A
redaction step can be misconfigured; an absent field cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TextIO


class Direction(StrEnum):
    EGRESS = "egress"
    INGRESS = "ingress"


# The flow log format Custos asks the customer to configure. Shipped verbatim in
# the collector's Terraform module so the two cannot drift.
LOG_FORMAT = " ".join(
    f"${{{f}}}"
    for f in (
        "version", "account-id", "interface-id", "srcaddr", "dstaddr",
        "srcport", "dstport", "protocol", "packets", "bytes", "start", "end",
        "action", "log-status", "vpc-id", "subnet-id", "flow-direction",
        "pkt-dst-aws-service", "tcp-flags",
    )
)

# TCP flag bits as reported in the tcp-flags field.
FIN, SYN, RST, PSH, ACK = 1, 2, 4, 8, 16


@dataclass(slots=True)
class FlowRecord:
    """One VPC Flow Logs v5 record.

    There is no payload field on this struct and none may be added. The
    collector's wire types mirror this shape, and SEC-18 is enforced by the fact
    that no type in the pipeline is capable of holding a payload byte.
    """

    account_id: str
    interface_id: str
    srcaddr: str
    dstaddr: str
    srcport: int
    dstport: int
    protocol: int
    packets: int
    bytes: int
    start: datetime
    end: datetime
    direction: Direction
    vpc_id: str = "vpc-0a1b2c3d"
    subnet_id: str = "subnet-0ab12345"
    action: str = "ACCEPT"
    log_status: str = "OK"
    dst_aws_service: str = ""
    tcp_flags: int = ACK
    version: int = 5

    def to_line(self) -> str:
        return " ".join(
            str(v)
            for v in (
                self.version, self.account_id, self.interface_id,
                self.srcaddr, self.dstaddr, self.srcport, self.dstport,
                self.protocol, self.packets, self.bytes,
                int(self.start.timestamp()), int(self.end.timestamp()),
                self.action, self.log_status, self.vpc_id, self.subnet_id,
                self.direction, self.dst_aws_service or "-", self.tcp_flags,
            )
        )

    @classmethod
    def from_line(cls, line: str) -> FlowRecord:
        f = line.split()
        if len(f) != 19:
            raise ValueError(f"expected 19 fields, got {len(f)}: {line!r}")
        svc = f[17]
        return cls(
            version=int(f[0]), account_id=f[1], interface_id=f[2],
            srcaddr=f[3], dstaddr=f[4], srcport=int(f[5]), dstport=int(f[6]),
            protocol=int(f[7]), packets=int(f[8]), bytes=int(f[9]),
            start=datetime.fromtimestamp(int(f[10]), tz=UTC),
            end=datetime.fromtimestamp(int(f[11]), tz=UTC),
            action=f[12], log_status=f[13], vpc_id=f[14], subnet_id=f[15],
            direction=Direction(f[16]), dst_aws_service="" if svc == "-" else svc,
            tcp_flags=int(f[18]),
        )


@dataclass(slots=True)
class InboundRequest:
    """One ALB access log line, reduced to what correlation needs.

    The second ingestion source. A0 exists partly to find out how much of the
    signal lives here rather than in the flow logs — and the answer turns out to
    be: most of it.
    """

    at: datetime
    target: str
    sent_bytes: int
    received_bytes: int


def write_lines(fh: TextIO, records: list[FlowRecord]) -> None:
    for r in records:
        fh.write(r.to_line() + "\n")
