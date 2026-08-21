from datetime import UTC, datetime, timedelta

import pytest

from custos.register.model import Agent, Identity, Provenance, Source, Status
from custos.register.store import agent_id
from custos.store.agents import AgentStore
from custos.store.db import open_database
from custos.store.scans import ScanStore

ACCOUNT = "447120043318"
W0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
W1 = W0 + timedelta(hours=1)


@pytest.fixture
def conn():
    return open_database()


@pytest.fixture
def scans(conn):
    return ScanStore(conn)


def record(scans, start=W0, end=W1, flows=1000, requests=50, alb=True, collector="v1"):
    return scans.record_batch(
        account_id=ACCOUNT, region="us-east-1", window_start=start, window_end=end,
        collector=collector, received_at=end, flow_records=flows,
        requests=requests, have_alb_logs=alb,
    )


def test_first_delivery_of_a_window_is_not_a_duplicate(scans):
    batch = record(scans)
    assert not batch.duplicate
    assert batch.flow_records == 1000


# The collector retries with bounded backoff, so the same window genuinely
# arrives twice. Counting it twice inflates every byte total downstream, which
# means every spend estimate and every egress ratio the classifier reads.
def test_redelivered_window_updates_rather_than_duplicating(scans, conn):
    record(scans, flows=1000)
    again = record(scans, flows=1200, collector="v2")

    assert again.duplicate
    assert conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"] == 1
    row = conn.execute("SELECT flow_records, collector FROM batches").fetchone()
    assert row["flow_records"] == 1200
    assert row["collector"] == "v2"


def test_different_windows_are_separate_batches(scans, conn):
    record(scans, start=W0, end=W1)
    record(scans, start=W1, end=W1 + timedelta(hours=1))
    assert conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"] == 2


def test_scans_are_listed_newest_first(scans):
    batch = record(scans)
    for i in range(3):
        scans.record_scan(
            batch_id=batch.id, account_id=ACCOUNT,
            started_at=W0 + timedelta(hours=i), principals_seen=11, agents_found=5,
            review_candidates=2, coverage=1.0, truncated=False, catalogue_revision="r",
        )
    listed = scans.scans_for(ACCOUNT)
    assert [s.started_at for s in listed] == sorted(
        (s.started_at for s in listed), reverse=True
    )
    assert scans.latest_scan(ACCOUNT).started_at == W0 + timedelta(hours=2)


def test_latest_scan_of_an_unknown_account_is_none(scans):
    assert scans.latest_scan("000000000000") is None


def _agent(store, principal="arn:aws:iam::1:role/x"):
    return store.upsert(Agent(
        id=agent_id(ACCOUNT, principal), first_seen=W0, last_seen=W1,
        status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.95,
                              observed_principal=principal),
        identity=Identity(principal=principal, account_id=ACCOUNT),
    ))


def test_observations_are_recorded_and_retrieved_per_scan(conn, scans):
    agents = AgentStore(conn)
    agent = _agent(agents)
    batch = record(scans)
    scan_id = scans.record_scan(
        batch_id=batch.id, account_id=ACCOUNT, started_at=W0, principals_seen=1,
        agents_found=1, review_candidates=0, coverage=1.0, truncated=False,
        catalogue_revision="r",
    )
    scans.record_observation(
        scan_id=scan_id, agent_id=agent.id, observed_at=W0, confidence=0.95,
        model_egress=1_000_000, model_ingress=100_000, episodes=4,
        calls_per_hour=12.5, tools={"billing-api", "orders-db"},
        active_hours={2: 0.5, 14: 0.5}, blast_radius="write",
    )

    observations = scans.observations_for_scan(scan_id)
    assert observations[agent.id]["tools"] == {"billing-api", "orders-db"}
    assert observations[agent.id]["model_egress"] == 1_000_000


# Every consumer of history computes a trend, so the order is part of the
# contract rather than an implementation detail.
def test_observation_history_is_oldest_first(conn, scans):
    agents = AgentStore(conn)
    agent = _agent(agents)
    batch = record(scans)
    for i in range(5):
        scan_id = scans.record_scan(
            batch_id=batch.id, account_id=ACCOUNT, started_at=W0 + timedelta(days=i),
            principals_seen=1, agents_found=1, review_candidates=0, coverage=1.0,
            truncated=False, catalogue_revision="r",
        )
        scans.record_observation(
            scan_id=scan_id, agent_id=agent.id, observed_at=W0 + timedelta(days=i),
            confidence=0.9, model_egress=i * 100, model_ingress=10, episodes=1,
            calls_per_hour=float(i), tools=set(), active_hours={}, blast_radius="read",
        )

    history = scans.observation_history(agent.id)
    assert [h["model_egress"] for h in history] == [0, 100, 200, 300, 400]


def test_observation_history_respects_its_limit(conn, scans):
    agents = AgentStore(conn)
    agent = _agent(agents)
    batch = record(scans)
    for i in range(10):
        scan_id = scans.record_scan(
            batch_id=batch.id, account_id=ACCOUNT, started_at=W0 + timedelta(days=i),
            principals_seen=1, agents_found=1, review_candidates=0, coverage=1.0,
            truncated=False, catalogue_revision="r",
        )
        scans.record_observation(
            scan_id=scan_id, agent_id=agent.id, observed_at=W0 + timedelta(days=i),
            confidence=0.9, model_egress=i, model_ingress=1, episodes=1,
            calls_per_hour=1.0, tools=set(), active_hours={}, blast_radius="read",
        )

    history = scans.observation_history(agent.id, limit=3)
    assert [h["model_egress"] for h in history] == [7, 8, 9]


def test_scan_coverage_and_truncation_are_persisted(scans):
    batch = record(scans)
    scans.record_scan(
        batch_id=batch.id, account_id=ACCOUNT, started_at=W0, principals_seen=11,
        agents_found=5, review_candidates=2, coverage=0.62, truncated=True,
        catalogue_revision="2026-08-18",
    )
    latest = scans.latest_scan(ACCOUNT)
    assert latest.coverage == 0.62
    assert latest.truncated is True
