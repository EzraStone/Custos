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


# --- an agent that runs in more than one region -------------------------------

def _agent_in(region: str, principal: str = "role/finance-close"):
    from datetime import UTC, datetime

    from custos.register.model import (
        Agent,
        Identity,
        ModelUse,
        Provenance,
        Source,
        Status,
    )
    from custos.register.store import agent_id

    at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    return Agent(
        id=agent_id("447120043318", principal),
        first_seen=at, last_seen=at, status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.9,
                              observed_principal=principal),
        identity=Identity(principal=principal, account_id="447120043318"),
        model=ModelUse(est_monthly_spend_usd=100.0),
        regions={region},
    )


def test_a_second_region_is_added_rather_than_replacing_the_first():
    """A scan covers one region, so a role running in three is discovered three
    times. A register that kept only the last would describe an agent as living
    wherever it was most recently looked for."""
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    store.upsert(_agent_in("us-east-1"))
    stored = store.upsert(_agent_in("eu-west-1"))

    assert stored.regions == {"us-east-1", "eu-west-1"}


def test_rescanning_the_same_region_does_not_multiply_it():
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    store.upsert(_agent_in("us-east-1"))
    stored = store.upsert(_agent_in("us-east-1"))

    assert stored.regions == {"us-east-1"}


def test_a_scan_that_names_no_region_does_not_erase_the_ones_known():
    """An older collector never sent one, and an upgrade must not blank the
    history of where an agent has been seen."""
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    store.upsert(_agent_in("us-east-1"))

    unnamed = _agent_in("us-east-1")
    unnamed.regions = set()
    stored = store.upsert(unnamed)

    assert stored.regions == {"us-east-1"}


def test_regions_survive_a_round_trip_through_the_database():
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    written = store.upsert(_agent_in("ap-south-1"))

    assert store.get(written.id).regions == {"ap-south-1"}


def test_spend_is_summed_across_the_regions_an_agent_runs_in():
    """A scan covers one region, so an agent running in three was showing a
    third of its cost — and spend is the number people act on."""
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    east = _agent_in("us-east-1")
    east.region_spend = {"us-east-1": 100.0}
    store.upsert(east)

    west = _agent_in("eu-west-1")
    west.region_spend = {"eu-west-1": 40.0}
    stored = store.upsert(west)

    assert stored.monthly_spend_usd == 140.0


def test_rescanning_a_region_replaces_its_figure_rather_than_adding_to_it():
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    first = _agent_in("us-east-1")
    first.region_spend = {"us-east-1": 100.0}
    store.upsert(first)

    cheaper = _agent_in("us-east-1")
    cheaper.region_spend = {"us-east-1": 60.0}
    stored = store.upsert(cheaper)

    assert stored.monthly_spend_usd == 60.0


def test_an_agent_with_no_per_region_figures_falls_back_to_the_scan_s():
    """Every agent discovered before regions existed, and every batch from a
    collector that does not send one."""
    agent = _agent_in("us-east-1")
    agent.regions = set()
    agent.region_spend = {}
    agent.model.est_monthly_spend_usd = 77.0

    assert agent.monthly_spend_usd == 77.0


def test_per_region_spend_survives_a_round_trip():
    from custos.store.agents import AgentStore
    from custos.store.db import open_database

    store = AgentStore(open_database())
    agent = _agent_in("ap-south-1")
    agent.region_spend = {"ap-south-1": 12.5}
    written = store.upsert(agent)

    assert store.get(written.id).region_spend == {"ap-south-1": 12.5}


# --- reach, which is one region's ---------------------------------------------
#
# Spend was fixed first and reach was left, with every surface saying "reach
# from one" beside it. The underlying defect was worse than a caveat: reach was
# replaced wholesale on every upsert, so scanning eu-west-1 erased the tools
# observed in us-east-1 and the agent appeared to stop touching them.

def _regional(store, principal, region, tools, stores=()):
    from custos.register.model import RegionalReach

    agent = make(principal=principal)
    agent.regions = {region}
    agent.reach = Reach(tools=set(tools), data_stores=set(stores))
    agent.region_reach = {
        region: RegionalReach(tools=frozenset(tools), data_stores=frozenset(stores))
    }
    return store.upsert(agent)


def test_a_second_regions_scan_does_not_erase_the_firsts_reach(store):
    _regional(store, "arn:aws:iam::1:role/x", "us-east-1", ["billing-api"])
    kept = _regional(store, "arn:aws:iam::1:role/x", "eu-west-1", ["ticketing"])

    assert kept.reach.tools == {"billing-api", "ticketing"}


def test_rescanning_a_region_replaces_that_regions_list(store):
    """A tool an agent stopped using should disappear from that region — and
    only from that region. Accumulating would make reach only ever grow, which
    is how a register fills up with services nothing has touched in months."""
    _regional(store, "arn:aws:iam::1:role/x", "us-east-1", ["billing-api", "gone"])
    _regional(store, "arn:aws:iam::1:role/x", "eu-west-1", ["ticketing"])
    kept = _regional(store, "arn:aws:iam::1:role/x", "us-east-1", ["billing-api"])

    assert kept.reach.tools == {"billing-api", "ticketing"}
    assert "gone" not in kept.reach.tools


def test_the_breakdown_survives_the_round_trip(store):
    _regional(store, "arn:aws:iam::1:role/x", "us-east-1", ["billing-api"], ["rds"])
    kept = _regional(store, "arn:aws:iam::1:role/x", "eu-west-1", ["ticketing"])

    assert set(kept.region_reach) == {"us-east-1", "eu-west-1"}
    assert kept.region_reach["us-east-1"].tools == frozenset({"billing-api"})
    assert kept.region_reach["us-east-1"].data_stores == frozenset({"rds"})
    assert kept.region_reach["eu-west-1"].tools == frozenset({"ticketing"})


def test_data_stores_union_the_same_way(store):
    _regional(store, "arn:aws:iam::1:role/x", "us-east-1", [], ["orders-db"])
    kept = _regional(store, "arn:aws:iam::1:role/x", "eu-west-1", [], ["billing-db"])

    assert kept.reach.data_stores == {"orders-db", "billing-db"}


def test_an_agent_from_a_collector_that_sends_no_region_keeps_its_reach(store):
    """An older collector sends no region, so there is no per-region breakdown
    to union. Falling back to the scan's own reach is what keeps that agent's
    tools from vanishing the moment this column existed."""
    agent = make(principal="arn:aws:iam::1:role/old")
    agent.reach = Reach(tools={"billing-api"})
    store.upsert(agent)

    agent.reach = Reach(tools={"ticketing"})
    kept = store.upsert(agent)
    assert kept.reach.tools == {"ticketing"}
