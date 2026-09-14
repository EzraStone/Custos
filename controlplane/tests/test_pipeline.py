"""The seam between a shipped batch and the scanner.

What is tested here is the part of ingestion that decides what a report may
claim, rather than what it finds: how much of the account was seen, and what
was reached that the catalogue has nothing to say about.
"""

# --- IPv6, which the catalogue cannot speak to --------------------------------


def _v6_input(*destinations):
    from datetime import UTC, datetime, timedelta

    from custos.scan import ScanInput
    from custos.telemetry import Direction, FlowRecord

    at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    return ScanInput(
        account_id="1", start=at, end=at + timedelta(hours=1),
        records=[
            FlowRecord(
                account_id="1", interface_id="eni-1", srcaddr="10.0.1.5",
                dstaddr=d, srcport=41000, dstport=443, protocol=6,
                packets=40, bytes=1000, start=at, end=at + timedelta(seconds=59),
                direction=Direction.EGRESS,
            )
            for d in destinations
        ],
    )


def test_public_ipv6_destinations_are_counted_once_each():
    from custos.pipeline import ipv6_destinations

    assert ipv6_destinations(
        _v6_input("2606:4700::1", "2606:4700::1", "2606:4700::2")
    ) == 2


def test_private_ipv6_is_not_a_blind_spot():
    """A dual-stack VPC's internal traffic classifies fine. Counting it would
    put a caveat on every dual-stack account whether or not it had one."""
    from custos.pipeline import ipv6_destinations

    assert ipv6_destinations(_v6_input("fd00::1", "fe80::1")) == 0


def test_an_ipv4_only_account_counts_nothing():
    from custos.pipeline import ipv6_destinations

    assert ipv6_destinations(_v6_input("160.79.104.10", "10.0.9.44")) == 0


# --- IPv6, and what is actually blind about it --------------------------------


def _annotated(destination, service):
    from datetime import UTC, datetime, timedelta

    from custos.scan import ScanInput
    from custos.telemetry import Direction, FlowRecord

    at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    return ScanInput(
        account_id="1", start=at, end=at + timedelta(hours=1),
        records=[FlowRecord(
            account_id="1", interface_id="eni-1", srcaddr="10.0.1.5",
            dstaddr=destination, srcport=41000, dstport=443, protocol=6,
            packets=40, bytes=1000, start=at, end=at + timedelta(seconds=59),
            direction=Direction.EGRESS, dst_aws_service=service,
        )],
    )


def test_bedrock_over_ipv6_is_not_counted_as_blindness():
    """`pkt-dst-aws-service` is an annotation about the destination, not about
    its address family. Bedrock over IPv6 is recognised exactly as Bedrock over
    IPv4 is, and counting it would overstate a limitation in the one document
    whose value is that it does not overstate."""
    from custos.pipeline import ipv6_destinations

    assert ipv6_destinations(_annotated("2600:1f18::1", "BEDROCK")) == 0


def test_an_unannotated_ipv6_destination_is_still_counted():
    """A third-party provider reached over IPv6 has no annotation and no
    published range we can verify. That agent makes no finding at all, and the
    count is what stops the report reading as a clean account."""
    from custos.pipeline import ipv6_destinations

    assert ipv6_destinations(_annotated("2606:4700::6810:85e5", "")) == 1


def test_an_ipv6_destination_for_storage_is_still_counted():
    """S3 over IPv6 is not a model endpoint, so the catalogue's silence about
    it is not the silence this count is about — but it is still an address the
    range catalogue could not speak to, and the sentence is about the ranges."""
    from custos.pipeline import ipv6_destinations

    assert ipv6_destinations(_annotated("2600:1f18::9", "S3")) == 1
