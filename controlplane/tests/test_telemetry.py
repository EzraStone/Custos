"""SEC-18 is enforced by the shape of the types, not by a redaction step."""

import dataclasses
from datetime import UTC, datetime

import pytest

from custos.telemetry import Direction, FlowRecord, InboundRequest

ALLOWED_FLOW_FIELDS = {
    "version", "account_id", "interface_id", "srcaddr", "dstaddr", "srcport",
    "dstport", "protocol", "packets", "bytes", "start", "end", "action",
    "log_status", "vpc_id", "subnet_id", "direction", "dst_aws_service",
    "tcp_flags",
}
ALLOWED_REQUEST_FIELDS = {"at", "target", "sent_bytes", "received_bytes"}


@pytest.mark.parametrize(
    ("cls", "allowed"),
    [(FlowRecord, ALLOWED_FLOW_FIELDS), (InboundRequest, ALLOWED_REQUEST_FIELDS)],
)
def test_sec18_no_payload_fields(cls, allowed):
    """Any field added to a wire type must be added to this allowlist first.

    That is the point. Adding a field capable of carrying prompt or completion
    text requires deliberately editing a test named after the invariant it
    breaks, which is a conversation rather than an accident.
    """
    actual = {f.name for f in dataclasses.fields(cls)}
    assert actual == allowed, actual ^ allowed


def test_flow_record_roundtrips_through_the_native_format():
    r = FlowRecord(
        account_id="447120043318", interface_id="eni-01a2b3c4d",
        srcaddr="10.0.20.11", dstaddr="160.79.104.10", srcport=43112, dstport=443,
        protocol=6, packets=214, bytes=286_432,
        start=datetime(2026, 8, 10, 14, 0, tzinfo=UTC),
        end=datetime(2026, 8, 10, 14, 1, tzinfo=UTC),
        direction=Direction.EGRESS, dst_aws_service="BEDROCK",
    )
    assert FlowRecord.from_line(r.to_line()) == r


def test_absent_aws_service_roundtrips_as_dash():
    r = FlowRecord(
        account_id="1", interface_id="eni-1", srcaddr="10.0.0.1", dstaddr="10.0.0.2",
        srcport=1, dstport=2, protocol=6, packets=1, bytes=1,
        start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC),
        direction=Direction.INGRESS,
    )
    assert " - " in r.to_line()
    assert FlowRecord.from_line(r.to_line()).dst_aws_service == ""


def test_malformed_line_is_rejected():
    with pytest.raises(ValueError, match="expected 19 fields"):
        FlowRecord.from_line("5 447120043318 eni-1")
