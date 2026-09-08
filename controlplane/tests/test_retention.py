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


def _seed_scan(conn, days_ago: int) -> int:
    """A batch and a scan dated `days_ago`, with no agent. Returns the scan id."""
    at = now() - timedelta(days=days_ago)
    scans = ScanStore(conn)
    batch = scans.record_batch(
        account_id=ACCOUNT, region="us-east-1",
        window_start=at, window_end=at + timedelta(hours=1),
        collector="v1", received_at=at, flow_records=10, requests=0,
        have_alb_logs=False,
    )
    return scans.record_scan(
        batch_id=batch.id, account_id=ACCOUNT, started_at=at, principals_seen=1,
        agents_found=0, review_candidates=1, coverage=1.0, truncated=False,
        catalogue_revision="r",
    )


def seed_per_scan_rows(conn, scan_id: int) -> None:
    """Attach a review candidate and a gateway question to one scan.

    Both are questions derived from a window of telemetry that retention
    deletes. A question that outlives the traffic behind it cannot be
    answered — nobody can go back and check — and the report would keep
    asking it.
    """
    from types import SimpleNamespace

    from custos.gateway import Candidate
    from custos.store.declarations import CandidateStore
    from custos.store.scans import ReviewStore

    ReviewStore(conn).record(scan_id, ACCOUNT, [
        SimpleNamespace(principal="role/maybe", confidence=0.5,
                        evidence=["odd bursts"], unavailable=[]),
    ])
    CandidateStore(conn).record(scan_id, ACCOUNT, [
        Candidate(address="10.0.7.40", egress=9_000_000, ingress=200_000,
                  principals=("role/x",), blind_principals=("role/x",)),
    ])
    conn.commit()


# Both tables below were added after retention was written, and neither had a
# test proving prune reaches them. They cascade from scans, which is only true
# while PRAGMA foreign_keys is on and the declaration stays in the schema.
def test_pruning_a_scan_takes_its_questions_with_it():
    """A question derived from traffic that no longer exists cannot be
    answered. Leaving it behind means a report that keeps asking."""
    conn = open_database()
    old_id = _seed_scan(conn, days_ago=400)
    recent_id = _seed_scan(conn, days_ago=2)
    seed_per_scan_rows(conn, old_id)
    seed_per_scan_rows(conn, recent_id)

    assert count(conn, "review_candidates") == 2
    assert count(conn, "gateway_candidates") == 2

    prune(conn, observation_days=90, scan_days=365)

    assert count(conn, "review_candidates") == 1
    assert count(conn, "gateway_candidates") == 1
    surviving = conn.execute(
        "SELECT scan_id FROM review_candidates"
    ).fetchone()["scan_id"]
    assert surviving == recent_id


def _scan_referencing_tables(conn) -> list[str]:
    return [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND sql LIKE '%REFERENCES scans%'"
        )
    ]


def test_every_table_hanging_off_a_scan_has_a_way_of_being_pruned():
    """A meta-test, because the next per-scan table will be added by someone
    who has no reason to know retention exists.

    Two mechanisms are legitimate. `observations` predates the others and is
    deleted explicitly by date, which is why it has no cascade — rebuilding
    that table to add one would be a migration for no behavioural gain. Newer
    tables cascade. What is not legitimate is neither: those rows outlive the
    scan, or the foreign key refuses the delete and prune starts failing.
    """
    import inspect

    from custos.store import retention

    conn = open_database()
    tables = _scan_referencing_tables(conn)
    assert {"observations", "review_candidates", "gateway_candidates"} <= set(tables)

    source = inspect.getsource(retention)
    for table in tables:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
        ).fetchone()["sql"]
        cascades = "ON DELETE CASCADE" in sql
        deleted_by_hand = f"DELETE FROM {table}" in source
        assert cascades or deleted_by_hand, (
            f"{table} references scans with neither a cascade nor an explicit "
            "delete in retention: pruning a scan would either leave its rows "
            "behind or fail on the foreign key"
        )


def test_prune_says_how_many_open_questions_it_took():
    """They leave by cascade, which reports nothing. An operator reading
    "pruned 40 scans" would not know eleven questions went with them."""
    conn = open_database()
    seed_per_scan_rows(conn, _seed_scan(conn, days_ago=400))
    seed_per_scan_rows(conn, _seed_scan(conn, days_ago=1))

    result = prune(conn, observation_days=90, scan_days=365)

    # One review candidate and one gateway candidate, from the old scan only.
    assert result.questions == 2
    assert result.total >= result.questions


def test_pruning_leaves_no_row_pointing_at_a_scan_that_is_gone():
    """The property the mechanisms above exist to produce, checked directly."""
    conn = open_database()
    seed(conn, days_ago=400)
    old_id = _seed_scan(conn, days_ago=400)
    seed_per_scan_rows(conn, old_id)
    seed_per_scan_rows(conn, _seed_scan(conn, days_ago=1))

    prune(conn, observation_days=90, scan_days=365)

    for table in _scan_referencing_tables(conn):
        orphans = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} "
            "WHERE scan_id NOT IN (SELECT id FROM scans)"
        ).fetchone()["n"]
        assert orphans == 0, f"{table} kept {orphans} rows past their scan"


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


# --- delivery history ---------------------------------------------------------

def _deliver(conn, days_ago: int, title="a finding"):
    """Record one delivery `days_ago` in the past.

    The title varies the fingerprint, which is what makes two calls produce two
    rows rather than one updated row.
    """
    from datetime import timedelta

    from custos.deliver import Finding, Severity, Suppressor
    from custos.store.db import now

    finding = Finding(
        severity=Severity.ACT_NOW, title=title, detail="d",
        account_id=ACCOUNT, principal="role/x",
    )
    Suppressor(conn).record([finding], "slack", now() - timedelta(days=days_ago))
    return finding


def test_stale_delivery_history_is_pruned(conn):
    """A fingerprint older than the longest repeat window can never suppress
    anything, so keeping it is storage with no behaviour attached."""
    _deliver(conn, days_ago=400, title="old")
    _deliver(conn, days_ago=1, title="recent")

    result = prune(conn)
    assert result.deliveries == 1
    assert count(conn, "deliveries") == 1


def test_delivery_history_outlives_its_repeat_window(conn):
    """Pruning a record a day before its window expires would re-deliver a
    finding that was correctly suppressed."""
    from custos.deliver.suppress import REPEAT_AFTER

    longest = max(REPEAT_AFTER.values()).days
    _deliver(conn, days_ago=longest + 1)

    prune(conn)
    assert count(conn, "deliveries") == 1, (
        "a record still inside its repeat window plus margin must survive"
    )


# A table that appears the first time some object is constructed is a table
# that migrations and retention both forget about.
def test_the_deliveries_table_exists_without_constructing_a_suppressor(conn):
    conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()


def test_an_account_pruned_back_to_no_scans_still_answers(tmp_path):
    """An account quiet for longer than the scan retention window loses every
    scan row while keeping its register — agents and audit entries are never
    pruned.

    That leaves a state nothing else produces: a register with findings and no
    scan behind them. Every read path has to degrade rather than fail, because
    the alternative is a customer's console going blank on the account they
    stopped scanning rather than saying it has no recent scans.
    """
    from fastapi.testclient import TestClient

    from custos.api import TokenStore, create_app

    conn = open_database()
    client = TestClient(create_app(conn=conn, tokens=TokenStore({"t": ACCOUNT})))
    headers = {"Authorization": "Bearer t"}

    # A scan old enough to fall outside every retention window.
    long_ago = now() - timedelta(days=800)
    scans = ScanStore(conn)
    record = scans.record_batch(
        account_id=ACCOUNT, region="us-east-1",
        window_start=long_ago, window_end=long_ago + timedelta(hours=1),
        collector="test", received_at=long_ago,
        flow_records=1, requests=0, have_alb_logs=False,
    )
    scans.record_scan(
        batch_id=record.id, account_id=ACCOUNT, started_at=long_ago,
        principals_seen=1, agents_found=0, review_candidates=0,
        coverage=1.0, truncated=False, catalogue_revision="x",
    )
    conn.commit()

    prune(conn)
    conn.commit()
    assert scans.scans_for(ACCOUNT) == [], "the old scan should have been pruned"

    # Every read path the console makes on load.
    assert client.get("/v1/register", headers=headers).status_code == 200
    assert client.get("/v1/scans", headers=headers).json()["scans"] == []
    assert client.get("/v1/accounts", headers=headers).status_code == 200

    diff = client.get("/v1/diff", headers=headers)
    assert diff.status_code == 200
    assert diff.json()["previous_scan_id"] is None

    report = client.get("/v1/report", headers=headers)
    assert report.status_code == 200, "the report must render without a scan behind it"
