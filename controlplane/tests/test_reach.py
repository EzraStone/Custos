from datetime import UTC, datetime, timedelta

from custos.reach import (
    IamCapability,
    granted_blast_radius,
    observed_addresses,
    observed_reach,
)
from custos.register.model import BlastRadius
from custos.telemetry import Direction, FlowRecord


def cap(*actions, **kw) -> IamCapability:
    return IamCapability(principal="role/x", actions=frozenset(actions), **kw)


def test_read_only_actions_are_read():
    assert granted_blast_radius(cap("s3:GetObject", "dynamodb:Query")) is BlastRadius.READ


def test_write_actions_are_write():
    assert granted_blast_radius(cap("s3:GetObject", "s3:PutObject")) is BlastRadius.WRITE


def test_delete_actions_are_destructive():
    assert granted_blast_radius(cap("s3:DeleteBucket")) is BlastRadius.DESTRUCTIVE


def test_wildcards_are_treated_as_destructive():
    """A role with s3:* can delete the bucket. Reporting that as 'write' would
    understate the one thing the customer needs to see."""
    assert granted_blast_radius(cap("s3:*")) is BlastRadius.DESTRUCTIVE
    assert granted_blast_radius(cap("*")) is BlastRadius.DESTRUCTIVE


def test_assume_role_counts_as_write_capability():
    """sts:AssumeRole is lateral movement: the effective radius is whatever the
    assumed role can do, so it cannot be reported as read-only."""
    assert granted_blast_radius(cap("sts:AssumeRole")) is BlastRadius.WRITE


def test_empty_policy_is_read():
    assert granted_blast_radius(cap()) is BlastRadius.READ


def test_blast_radius_ranks_for_sorting():
    assert BlastRadius.DESTRUCTIVE.rank > BlastRadius.WRITE.rank > BlastRadius.READ.rank


def _telemetry(records):
    """Windows built from raw flow records, the way a scan builds them."""
    from custos.classify.episodes import PrincipalTelemetry, build_windows

    windows = build_windows(records, records[0].start, timedelta(seconds=60))
    return PrincipalTelemetry(principal="arn:aws:iam::1:role/r", windows=windows)


def _flow(dst, dstport, service="", direction=Direction.EGRESS, at=None):
    start = at or datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    return FlowRecord(
        account_id="1", interface_id="eni-1",
        srcaddr="10.0.1.5" if direction is Direction.EGRESS else dst,
        dstaddr=dst if direction is Direction.EGRESS else "10.0.1.5",
        srcport=41000 if direction is Direction.EGRESS else dstport,
        dstport=dstport if direction is Direction.EGRESS else 41000,
        protocol=6, packets=10, bytes=5000,
        start=start, end=start + timedelta(seconds=30),
        direction=direction, dst_aws_service=service,
    )


def test_a_destination_appears_once_however_often_it_is_seen():
    """The scope is keyed by address and named afterwards.

    It used to be a set of labels, so one destination could occupy two entries
    — `s3` from a record carrying the service annotation and `52.216.10.7` from
    one that did not. Keying by address makes that shape impossible rather than
    merely unlikely, which is why there is no check for it in the code.
    """
    t = _telemetry([
        _flow("52.216.10.7", 443, "S3"),
        _flow("52.216.10.7", 443, "S3", Direction.INGRESS),
        _flow("52.216.10.7", 443, ""),
    ])
    tools, stores = observed_reach(t)
    assert stores == {"s3"}
    assert tools == set()


def test_the_most_informative_record_names_a_destination():
    """Across windows the merge has to choose, and must not choose by whichever
    window happened to be last.

    A private datastore is the case where the choice is real: it classifies as
    a datastore either way, so both records survive, and only one of them
    carries the service. The annotated record is deliberately in the earlier
    window here — picking the last one would name this `postgres 10.0.9.44`
    from the port table and throw away the fact that it is RDS.
    """
    first = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    later = first + timedelta(minutes=5)
    t = _telemetry([
        _flow("10.0.9.44", 5432, "RDS", at=first),
        _flow("10.0.9.44", 5432, "", at=later),
    ])
    assert len(t.windows) > 1, "the records must land in different windows"
    _, stores = observed_reach(t)
    assert stores == {"rds 10.0.9.44"}


def test_naming_and_classification_are_answered_from_the_same_facts():
    """The class and the label must come from one record's view of a
    destination. When they came from different ones they disagreed."""
    from custos.catalog import classify

    t = _telemetry([
        _flow("52.216.10.7", 443, "S3"),
        _flow("52.216.10.7", 443, "S3", Direction.INGRESS),
    ])
    for window in t.windows:
        for address, seen in window.tool_seen.items():
            assert seen.cls is classify(address, seen.port, seen.aws_service)


def test_an_internal_api_is_not_filed_as_a_datastore_by_association():
    """The split used to be inferred from the set of classes a whole window
    saw, so any window containing a datastore filed everything else in it as a
    datastore too."""
    t = _telemetry([
        _flow("10.0.9.44", 5432),   # postgres
        _flow("10.0.4.23", 443),    # an internal API in the same window
    ])
    tools, stores = observed_reach(t)
    assert stores == {"postgres 10.0.9.44"}
    assert tools == {"10.0.4.23"}


def test_rotating_service_addresses_are_one_entry_in_the_scope():
    t = _telemetry([
        _flow("52.216.10.7", 443, "S3"),
        _flow("52.217.4.19", 443, "S3"),
        _flow("3.5.28.100", 443, "S3"),
    ])
    _, stores = observed_reach(t)
    assert stores == {"s3"}


def test_write_targets_are_computed_from_addresses_not_labels():
    """observed_only asks the catalogue about each destination, and a label is
    not something the catalogue can answer about."""
    t = _telemetry([_flow("10.0.9.44", 5432), _flow("10.0.4.23", 443)])
    assert observed_addresses(t) == {"10.0.9.44", "10.0.4.23"}
