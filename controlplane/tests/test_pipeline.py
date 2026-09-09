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
