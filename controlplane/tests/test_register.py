"""SEC-17, tested as a property of the state machine rather than as a promise."""

from datetime import UTC, datetime

import pytest

from custos.register.model import (
    Agent,
    BlastRadius,
    Identity,
    Provenance,
    Reach,
    Source,
    Status,
)
from custos.register.store import Register, TransitionError, agent_id

T0 = datetime(2026, 8, 10, tzinfo=UTC)


def make(principal="arn:aws:iam::1:role/x", confidence=0.99, team="", **reach_kw) -> Agent:
    return Agent(
        id=agent_id("1", principal), first_seen=T0, last_seen=T0,
        status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=confidence,
                              observed_principal=principal),
        identity=Identity(principal=principal, owner_team=team, account_id="1"),
        reach=Reach(**reach_kw),
    )


def test_agent_ids_are_stable_across_scans():
    assert agent_id("1", "role/x") == agent_id("1", "role/x")
    assert agent_id("1", "role/x") != agent_id("2", "role/x")


def test_sec17_maximum_confidence_does_not_sanction():
    r = Register()
    a = r.upsert(make(confidence=1.0))
    assert a.status is Status.DISCOVERED
    assert a.imprimatur is None
    assert a.unsanctioned


def test_sec17_transition_cannot_reach_sanctioned():
    """The only door is grant_imprimatur, and transition() is not it."""
    r = Register()
    a = r.upsert(make())
    with pytest.raises(TransitionError, match="grant_imprimatur"):
        r.transition(a.id, Status.SANCTIONED, actor="someone")


def test_sec17_granting_requires_an_operator_identity():
    r = Register()
    a = r.upsert(make())
    for bad in ("", "   "):
        with pytest.raises(TransitionError, match="operator identity"):
            r.grant_imprimatur(a.id, operator=bad, at=T0)
    assert a.status is Status.DISCOVERED


def test_granting_records_who_and_scopes_to_what_was_observed():
    r = Register()
    a = r.upsert(make(tools={"billing-api"}, data_stores={"billing-db"}))
    r.grant_imprimatur(a.id, operator="ezra@custos.dev", at=T0)
    assert a.status is Status.SANCTIONED
    assert a.imprimatur is not None
    assert a.imprimatur.granted_by == "ezra@custos.dev"
    assert a.imprimatur.approved_tools == {"billing-api"}
    assert a.imprimatur.approved_data == {"billing-db"}
    assert not a.unsanctioned


def test_rescan_refreshes_observation_and_never_promotes_status():
    r = Register()
    a = r.upsert(make())
    r.grant_imprimatur(a.id, operator="ezra", at=T0)

    later = make()
    later.last_seen = datetime(2026, 8, 20, tzinfo=UTC)
    later.reach = Reach(tools={"deploy-ctl"}, blast_radius=BlastRadius.DESTRUCTIVE)
    same = r.upsert(later)

    assert same is a
    assert a.status is Status.SANCTIONED, "a re-scan must not reset a grant"
    assert a.last_seen == datetime(2026, 8, 20, tzinfo=UTC)
    assert a.reach.blast_radius is BlastRadius.DESTRUCTIVE


def test_rescan_of_a_discovered_agent_does_not_promote_it():
    r = Register()
    a = r.upsert(make())
    for _ in range(50):
        r.upsert(make())
    assert a.status is Status.DISCOVERED


def test_retiring_revokes_the_grant():
    r = Register()
    a = r.upsert(make())
    r.grant_imprimatur(a.id, operator="ezra", at=T0)
    r.transition(a.id, Status.RETIRED, actor="ezra")
    assert a.imprimatur is None
    assert a.status is Status.RETIRED


def test_a_retired_agent_must_be_reinstated_before_sanctioning():
    r = Register()
    a = r.upsert(make())
    r.transition(a.id, Status.RETIRED, actor="ezra")
    with pytest.raises(TransitionError, match="reinstated"):
        r.grant_imprimatur(a.id, operator="ezra", at=T0)


def test_illegal_transitions_are_refused():
    r = Register()
    a = r.upsert(make())
    r.transition(a.id, Status.RETIRED, actor="ezra")
    with pytest.raises(TransitionError, match="not a permitted transition"):
        r.transition(a.id, Status.DISCOVERED, actor="ezra")


def test_sec20_unattributed_findings_are_segregated():
    r = Register()
    r.upsert(make(principal="role/owned", team="payments"))
    r.upsert(make(principal="role/orphan"))
    assert [a.identity.principal for a in r.attributed_findings] == ["role/owned"]
    assert [a.identity.principal for a in r.unattributed_findings] == ["role/orphan"]


def test_unsanctioned_set_is_ordered_worst_first():
    r = Register()
    r.upsert(make(principal="role/read", confidence=0.99, blast_radius=BlastRadius.READ))
    r.upsert(make(principal="role/destroy", confidence=0.81,
                  blast_radius=BlastRadius.DESTRUCTIVE, tools={"a"}))
    r.upsert(make(principal="role/write", confidence=0.95, blast_radius=BlastRadius.WRITE))
    assert [a.identity.principal for a in r.unsanctioned] == [
        "role/destroy", "role/write", "role/read"
    ]


def test_every_state_change_is_audited():
    r = Register()
    a = r.upsert(make())
    r.transition(a.id, Status.PENDING_REVIEW, actor="ezra")
    r.grant_imprimatur(a.id, operator="ezra", at=T0)
    assert [e.action for e in r.audit] == ["discovered", "pending_review", "sanctioned"]
    assert r.audit[-1].actor == "ezra"
