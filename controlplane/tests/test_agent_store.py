"""The persistent register is held to the same SEC-17 tests as the in-memory one.

Two implementations of the state machine that could drift would be exactly the
wrong thing to be clever about, so these mirror test_register.py deliberately.
"""

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
from custos.register.store import TransitionError, agent_id
from custos.store.agents import AgentStore
from custos.store.db import open_database

T0 = datetime(2026, 8, 10, tzinfo=UTC)
T1 = datetime(2026, 8, 20, tzinfo=UTC)
ACCOUNT = "447120043318"


@pytest.fixture
def store():
    return AgentStore(open_database())


def make(principal="arn:aws:iam::1:role/x", confidence=0.99, team="", **reach_kw) -> Agent:
    return Agent(
        id=agent_id(ACCOUNT, principal), first_seen=T0, last_seen=T0,
        status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=confidence,
                              observed_principal=principal, evidence=["because bytes"]),
        identity=Identity(principal=principal, owner_team=team, account_id=ACCOUNT),
        reach=Reach(**reach_kw),
    )


def test_roundtrip_preserves_every_field(store):
    original = make(team="payments", tools={"billing-api"}, data_stores={"billing-db"},
                    blast_radius=BlastRadius.DESTRUCTIVE)
    original.model.providers = {"anthropic"}
    original.model.est_monthly_spend_usd = 1420.5
    store.upsert(original)

    got = store.get(original.id)
    assert got is not None
    assert got.identity.owner_team == "payments"
    assert got.reach.tools == {"billing-api"}
    assert got.reach.blast_radius is BlastRadius.DESTRUCTIVE
    assert got.model.est_monthly_spend_usd == 1420.5
    assert got.provenance.evidence == ["because bytes"]


def test_sec17_maximum_confidence_does_not_sanction(store):
    a = store.upsert(make(confidence=1.0))
    assert a.status is Status.DISCOVERED
    assert store.get(a.id).imprimatur is None


def test_sec17_transition_cannot_reach_sanctioned(store):
    a = store.upsert(make())
    with pytest.raises(TransitionError, match="grant_imprimatur"):
        store.transition(a.id, Status.SANCTIONED, actor="someone", at=T0)


def test_sec17_granting_requires_an_operator_identity(store):
    a = store.upsert(make())
    for bad in ("", "   "):
        with pytest.raises(TransitionError, match="operator identity"):
            store.grant_imprimatur(a.id, operator=bad, at=T0)
    assert store.get(a.id).status is Status.DISCOVERED


def test_sec17_rescan_never_promotes_or_revokes(store):
    """The UPDATE in upsert omits every authorisation column. If someone adds
    one, this fails."""
    a = store.upsert(make(tools={"billing-api"}))
    store.grant_imprimatur(a.id, operator="ezra@custos.dev", at=T0)

    later = make(tools={"deploy-ctl"}, blast_radius=BlastRadius.DESTRUCTIVE)
    later.last_seen = T1
    store.upsert(later)

    got = store.get(a.id)
    assert got.status is Status.SANCTIONED, "a re-scan must not reset a grant"
    assert got.imprimatur.granted_by == "ezra@custos.dev"
    assert got.imprimatur.approved_tools == {"billing-api"}, (
        "the grant's scope must not silently widen to newly observed reach"
    )
    assert got.reach.blast_radius is BlastRadius.DESTRUCTIVE
    assert got.last_seen == T1


def test_fifty_rescans_do_not_promote_a_discovered_agent(store):
    a = store.upsert(make())
    for _ in range(50):
        store.upsert(make())
    assert store.get(a.id).status is Status.DISCOVERED


def test_rescan_without_iam_access_does_not_erase_a_known_owner(store):
    """A scan run with reduced permissions must not blank attribution an
    earlier scan established. Losing an owner turns an actionable finding into
    noise."""
    a = store.upsert(make(team="payments"))
    store.upsert(make(team=""))
    assert store.get(a.id).identity.owner_team == "payments"


def test_first_seen_only_moves_backwards_and_last_seen_forwards(store):
    a = store.upsert(make())
    earlier = make()
    earlier.first_seen = datetime(2026, 1, 1, tzinfo=UTC)
    earlier.last_seen = datetime(2026, 1, 2, tzinfo=UTC)
    store.upsert(earlier)

    got = store.get(a.id)
    assert got.first_seen == datetime(2026, 1, 1, tzinfo=UTC)
    assert got.last_seen == T0


def test_retiring_revokes_the_grant(store):
    a = store.upsert(make())
    store.grant_imprimatur(a.id, operator="ezra", at=T0)
    store.transition(a.id, Status.RETIRED, actor="ezra", at=T1)

    got = store.get(a.id)
    assert got.status is Status.RETIRED
    assert got.imprimatur is None


def test_retired_agent_must_be_reinstated_before_sanctioning(store):
    a = store.upsert(make())
    store.transition(a.id, Status.RETIRED, actor="ezra", at=T0)
    with pytest.raises(TransitionError, match="reinstated"):
        store.grant_imprimatur(a.id, operator="ezra", at=T1)


def test_illegal_transitions_are_refused(store):
    a = store.upsert(make())
    store.transition(a.id, Status.RETIRED, actor="ezra", at=T0)
    with pytest.raises(TransitionError, match="not a permitted transition"):
        store.transition(a.id, Status.DISCOVERED, actor="ezra", at=T1)


def test_every_state_change_is_audited(store):
    a = store.upsert(make())
    store.transition(a.id, Status.PENDING_REVIEW, actor="ezra", at=T0)
    store.grant_imprimatur(a.id, operator="ezra", at=T1)
    assert [e["action"] for e in store.audit_for(a.id)] == [
        "discovered", "pending_review", "sanctioned"
    ]


def test_unsanctioned_set_is_ordered_worst_first(store):
    store.upsert(make(principal="role/read", blast_radius=BlastRadius.READ))
    store.upsert(make(principal="role/destroy", blast_radius=BlastRadius.DESTRUCTIVE,
                      tools={"a"}))
    store.upsert(make(principal="role/write", blast_radius=BlastRadius.WRITE))
    assert [a.identity.principal for a in store.unsanctioned(ACCOUNT)] == [
        "role/destroy", "role/write", "role/read"
    ]


def test_sanctioned_agents_leave_the_unsanctioned_set(store):
    a = store.upsert(make())
    assert len(store.unsanctioned(ACCOUNT)) == 1
    store.grant_imprimatur(a.id, operator="ezra", at=T0)
    assert store.unsanctioned(ACCOUNT) == []


def test_accounts_are_isolated(store):
    store.upsert(make(principal="role/a"))
    other = make(principal="role/b")
    other.identity.account_id = "999999999999"
    other.id = agent_id("999999999999", "role/b")
    store.upsert(other)

    assert len(store.list_for_account(ACCOUNT)) == 1
    assert len(store.list_for_account("999999999999")) == 1
