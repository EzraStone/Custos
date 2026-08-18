from custos.reach import IamCapability, granted_blast_radius
from custos.register.model import BlastRadius


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
