from datetime import timedelta

import pytest

from custos.register.model import Agent, Identity, Provenance, Source, Status
from custos.register.store import agent_id
from custos.store.agents import AgentStore
from custos.store.db import now, open_database
from custos.store.retention import prune, vacuum
from custos.store.scans import ScanStore

ACCOUNT = "447120043318"


@pytest.fixture
def conn():
    return open_database()


def seed(conn, days_ago: int):
    """Write a batch, a scan, and an observation dated `days_ago`."""
    at = now() - timedelta(days=days_ago)
    agents, scans = AgentStore(conn), ScanStore(conn)

    agent = agents.upsert(Agent(
        id=agent_id(ACCOUNT, "role/x"), first_seen=at, last_seen=at,
        status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.9,
                              observed_principal="role/x"),
        identity=Identity(principal="role/x", account_id=ACCOUNT),
    ))
    batch = scans.record_batch(
        account_id=ACCOUNT, region="us-east-1",
        window_start=at, window_end=at + timedelta(hours=1),
        collector="v1", received_at=at, flow_records=10, requests=0,
        have_alb_logs=False,
    )
    scan_id = scans.record_scan(
        batch_id=batch.id, account_id=ACCOUNT, started_at=at, principals_seen=1,
        agents_found=1, review_candidates=0, coverage=1.0, truncated=False,
        catalogue_revision="r",
    )
    scans.record_observation(
        scan_id=scan_id, agent_id=agent.id, observed_at=at, confidence=0.9,
        model_egress=100, model_ingress=10, episodes=1, calls_per_hour=1.0,
        tools=set(), active_hours={}, blast_radius="read",
    )
    return agent.id


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def test_old_observations_are_dropped():
    conn = open_database()
    seed(conn, days_ago=200)
    seed(conn, days_ago=5)

    result = prune(conn, observation_days=90, scan_days=365)
    assert result.observations == 1
    assert count(conn, "observations") == 1


# The register IS the product. Losing a sanction decision means asking a
# customer to re-review forty agents, which ends a pilot.
def test_agents_are_never_pruned(conn):
    seed(conn, days_ago=5000)
    prune(conn, observation_days=1, scan_days=2)
    assert count(conn, "agents") == 1


def test_audit_entries_are_never_pruned(conn):
    """An audit trail with a retention window is not an audit trail."""
    seed(conn, days_ago=5000)
    prune(conn, observation_days=1, scan_days=2)
    assert count(conn, "audit") >= 1


def test_a_sanctioned_agent_survives_aggressive_pruning(conn):
    aid = seed(conn, days_ago=5000)
    AgentStore(conn).grant_imprimatur(aid, operator="ezra", at=now())
    prune(conn, observation_days=1, scan_days=2)

    agent = AgentStore(conn).get(aid)
    assert agent.status is Status.SANCTIONED
    assert agent.imprimatur.granted_by == "ezra"


def test_recent_data_is_left_alone(conn):
    seed(conn, days_ago=1)
    assert prune(conn).total == 0
    assert count(conn, "observations") == 1


def test_observations_cannot_outlive_the_scans_they_reference(conn):
    """The foreign key would refuse it anyway; saying why beats a constraint
    error."""
    with pytest.raises(ValueError, match="reference the scans"):
        prune(conn, observation_days=400, scan_days=90)


def test_pruning_leaves_no_orphaned_observations(conn):
    seed(conn, days_ago=500)
    seed(conn, days_ago=500)
    prune(conn, observation_days=90, scan_days=365)

    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM observations WHERE scan_id NOT IN "
        "(SELECT id FROM scans)"
    ).fetchone()["n"]
    assert orphans == 0


def test_vacuum_runs_after_a_prune(conn):
    seed(conn, days_ago=500)
    prune(conn)
    vacuum(conn)
    assert count(conn, "agents") == 1


def test_pruning_an_empty_database_is_a_no_op(conn):
    assert prune(conn).total == 0
