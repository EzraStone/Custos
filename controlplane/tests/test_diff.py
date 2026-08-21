"""Scan comparison: what a second scan says that the first did not."""

from datetime import UTC, datetime

from custos.diff import VOLUME_JUMP_FACTOR, ChangeKind, compare
from custos.register.model import (
    Agent,
    BlastRadius,
    Identity,
    Provenance,
    Reach,
    Source,
    Status,
)

T0 = datetime(2026, 8, 10, tzinfo=UTC)


def agent(agent_id="agt_1", principal="arn:aws:iam::1:role/finance-close",
          team="finance", radius=BlastRadius.WRITE, tools=()):
    return Agent(
        id=agent_id, first_seen=T0, last_seen=T0, status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.95,
                              observed_principal=principal),
        identity=Identity(principal=principal, owner_team=team, account_id="1"),
        reach=Reach(tools=set(tools), blast_radius=radius),
    )


def obs(radius="write", tools=(), rate=10.0):
    return {"blast_radius": radius, "tools": set(tools), "calls_per_hour": rate}


def test_a_first_scan_reports_no_changes():
    """Every agent would technically be new, and the report already lists them."""
    diff = compare({"agt_1": agent()}, {"agt_1": obs()}, {}, previous_scan_id=None)
    assert diff.changes == []
    assert diff.headline == ""


def test_a_new_agent_is_the_headline():
    diff = compare({"agt_1": agent()}, {"agt_1": obs()}, {}, previous_scan_id=1)
    assert [c.kind for c in diff.changes] == [ChangeKind.APPEARED]
    assert "1 new agent" in diff.headline
    assert diff.changes[0].owner_team == "finance"


def test_blast_radius_increase_outranks_everything_else():
    """The most actionable finding: a specific permission change, named owner,
    obvious remediation."""
    current = {"agt_1": agent(radius=BlastRadius.DESTRUCTIVE, tools=("a", "b")),
               "agt_2": agent(agent_id="agt_2", principal="role/other")}
    diff = compare(
        current,
        {"agt_1": obs(radius="destructive", tools=("a", "b")), "agt_2": obs()},
        {"agt_1": obs(radius="write", tools=("a",))},
        previous_scan_id=1,
    )
    assert diff.changes[0].kind is ChangeKind.BLAST_RADIUS_INCREASED
    assert "write to destructive" in diff.changes[0].detail
    assert "could destroy" in diff.headline


def test_blast_radius_decrease_is_not_reported_as_an_escalation():
    diff = compare(
        {"agt_1": agent(radius=BlastRadius.READ)},
        {"agt_1": obs(radius="read")},
        {"agt_1": obs(radius="destructive")},
        previous_scan_id=1,
    )
    assert not any(
        c.kind is ChangeKind.BLAST_RADIUS_INCREASED for c in diff.changes
    )


def test_new_reach_is_reported():
    diff = compare(
        {"agt_1": agent(tools=("billing-api", "orders-db"))},
        {"agt_1": obs(tools=("billing-api", "orders-db"))},
        {"agt_1": obs(tools=("billing-api",))},
        previous_scan_id=1,
    )
    reach = [c for c in diff.changes if c.kind is ChangeKind.REACH_EXPANDED]
    assert len(reach) == 1
    assert "orders-db" in reach[0].detail


def test_losing_reach_is_not_reported_as_expansion():
    diff = compare(
        {"agt_1": agent(tools=("billing-api",))},
        {"agt_1": obs(tools=("billing-api",))},
        {"agt_1": obs(tools=("billing-api", "orders-db"))},
        previous_scan_id=1,
    )
    assert not any(c.kind is ChangeKind.REACH_EXPANDED for c in diff.changes)


def test_a_disappeared_agent_is_reported():
    diff = compare(
        {"agt_1": agent()}, {}, {"agt_1": obs()}, previous_scan_id=1
    )
    assert [c.kind for c in diff.changes] == [ChangeKind.DISAPPEARED]


def test_volume_must_jump_materially_to_be_reported():
    """Model workloads are spiky. A low threshold produces an entry every week
    for every agent, which trains the reader to skip the section."""
    below = compare(
        {"agt_1": agent()},
        {"agt_1": obs(rate=10.0 * (VOLUME_JUMP_FACTOR - 0.5))},
        {"agt_1": obs(rate=10.0)},
        previous_scan_id=1,
    )
    assert not any(c.kind is ChangeKind.VOLUME_JUMPED for c in below.changes)

    above = compare(
        {"agt_1": agent()},
        {"agt_1": obs(rate=10.0 * (VOLUME_JUMP_FACTOR + 1))},
        {"agt_1": obs(rate=10.0)},
        previous_scan_id=1,
    )
    assert any(c.kind is ChangeKind.VOLUME_JUMPED for c in above.changes)


def test_a_quiet_week_says_so_rather_than_saying_nothing():
    diff = compare(
        {"agt_1": agent()}, {"agt_1": obs()}, {"agt_1": obs()}, previous_scan_id=1
    )
    assert diff.actionable == []
    assert diff.headline == "No change since the last scan."


def test_changes_are_ordered_by_consequence():
    current = {
        "agt_1": agent(agent_id="agt_1", principal="role/aaa", radius=BlastRadius.DESTRUCTIVE),
        "agt_2": agent(agent_id="agt_2", principal="role/bbb"),
        "agt_3": agent(agent_id="agt_3", principal="role/ccc", tools=("x", "y")),
    }
    diff = compare(
        current,
        {
            "agt_1": obs(radius="destructive"),
            "agt_2": obs(),
            "agt_3": obs(tools=("x", "y")),
        },
        {"agt_1": obs(radius="read"), "agt_3": obs(tools=("x",))},
        previous_scan_id=1,
    )
    kinds = [c.kind for c in diff.changes]
    assert kinds.index(ChangeKind.BLAST_RADIUS_INCREASED) < kinds.index(ChangeKind.APPEARED)
    assert kinds.index(ChangeKind.APPEARED) < kinds.index(ChangeKind.REACH_EXPANDED)
