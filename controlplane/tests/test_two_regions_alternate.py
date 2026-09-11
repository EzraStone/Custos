"""Nothing an account can see may alternate with the region last collected.

A customer running one collector per region ships two windows for every hour.
Every account-scoped read is then answering a question about the account from
a row that belongs to one region, and the failure is always the same shape:
the answer flips between two halves and each half's absence reads as a
decision somebody made.

Four of these have been found and fixed — gateway questions, review
candidates, the scan diff, and behavioural baselines — and they were found one
at a time, each after the previous one was fixed. This asserts the property
directly instead: collect two regions alternately, and check that what the
account can see does not depend on which of them went last.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.store.db import open_database

ACCOUNT = "447120043318"
AUTH = {"Authorization": "Bearer tok"}
W0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def client():
    return TestClient(create_app(conn=open_database(), tokens=TokenStore({"tok": ACCOUNT})))


def _ship(client, region: str, hour: int, gateway: str) -> None:
    """One region's window, carrying an agent behind its own gateway."""
    from custos import batch as schema

    start = W0 + timedelta(hours=hour)
    flows = []
    for minute in range(40):
        at = start + timedelta(minutes=minute)
        flows.append(schema.FlowRecord(
            account_id=ACCOUNT, interface_id=f"eni-{region}", srcaddr="10.0.1.5",
            dstaddr=gateway, srcport=41000 + minute, dstport=443, protocol=6,
            packets=40, bytes=140_000, start=at, end=at + timedelta(seconds=30),
            action="ACCEPT", log_status="OK", direction="egress", tcp_flags=2,
        ))
        flows.append(schema.FlowRecord(
            account_id=ACCOUNT, interface_id=f"eni-{region}", srcaddr=gateway,
            dstaddr="10.0.1.5", srcport=443, dstport=41000 + minute, protocol=6,
            packets=8, bytes=9_000, start=at, end=at + timedelta(seconds=30),
            action="ACCEPT", log_status="OK", direction="ingress", tcp_flags=16,
        ))
        if minute % 2 == 0:
            flows.append(schema.FlowRecord(
                account_id=ACCOUNT, interface_id=f"eni-{region}", srcaddr="10.0.1.5",
                dstaddr="10.0.9.44", srcport=52000 + minute, dstport=5432, protocol=6,
                packets=12, bytes=4_000, start=at, end=at + timedelta(seconds=30),
                action="ACCEPT", log_status="OK", direction="egress", tcp_flags=2,
            ))
            flows.append(schema.FlowRecord(
                account_id=ACCOUNT, interface_id=f"eni-{region}", srcaddr="10.0.9.44",
                dstaddr="10.0.1.5", srcport=5432, dstport=52000 + minute, protocol=6,
                packets=30, bytes=30_000, start=at, end=at + timedelta(seconds=30),
                action="ACCEPT", log_status="OK", direction="ingress", tcp_flags=16,
            ))

    body = schema.Batch(
        account_id=ACCOUNT, region=region,
        window_start=start, window_end=start + timedelta(hours=1),
        collector_version="test",
        collection=schema.Collection(
            lines_read=len(flows), lines_parsed=len(flows), have_access_logs=True
        ),
        flows=flows,
        attachments=[schema.Attachment(
            interface_id=f"eni-{region}",
            principal=f"arn:aws:iam::{ACCOUNT}:role/agent-{region}",
            address="10.0.1.5", compute="Lambda",
        )],
    )
    assert client.post(
        "/v1/batches", json=body.model_dump(mode="json"), headers=AUTH
    ).status_code == 202


@pytest.fixture
def two_regions(client):
    """Two regions, each with its own gateway, collected alternately."""
    for hour, region in enumerate(("us-east-1", "eu-west-1", "us-east-1", "eu-west-1")):
        _ship(client, region, hour, "10.0.7.40" if region == "us-east-1" else "10.1.7.40")
    return client


def test_the_questions_do_not_alternate(two_regions):
    asked = two_regions.get("/v1/gateway-candidates", headers=AUTH).json()["candidates"]
    assert {c["region"] for c in asked} == {"us-east-1", "eu-west-1"}


def test_the_fleet_row_names_both(two_regions):
    row = two_regions.get("/v1/fleet", headers=AUTH).json()["accounts"][0]
    assert row["regions"] == ["eu-west-1", "us-east-1"]


def test_the_report_covers_both(two_regions):
    page = two_regions.get("/v1/report", headers=AUTH).text
    assert "eu-west-1, us-east-1 and no other region" in page


def test_the_diff_does_not_report_either_region_as_new(two_regions):
    """The last two windows are one of each. Comparing across them reports
    every workload in one as appeared and every workload in the other as
    disappeared."""
    page = two_regions.get("/v1/report", headers=AUTH).text
    assert "appeared for the first time" not in page
    assert "is absent now" not in page


def test_collecting_one_more_region_changes_nothing_that_is_not_about_it(two_regions):
    """The property itself. Ship another window of one region and the account's
    view of the other must not move."""
    before = two_regions.get("/v1/gateway-candidates", headers=AUTH).json()["candidates"]
    west_before = [c for c in before if c["region"] == "eu-west-1"]

    _ship(two_regions, "us-east-1", 4, "10.0.7.40")

    after = two_regions.get("/v1/gateway-candidates", headers=AUTH).json()["candidates"]
    west_after = [c for c in after if c["region"] == "eu-west-1"]
    assert [c["address"] for c in west_after] == [c["address"] for c in west_before]


def test_pruning_a_regions_batches_does_not_unsay_that_it_was_covered(two_regions):
    """Batches and scans are pruned after ninety days; the register is not,
    because an agent discovered last year is still running.

    So a report rendered after a prune lists agents from a region whose
    batches are gone. A coverage claim built from batches alone then says that
    region was never covered — which is the exact false statement the region
    label exists to prevent, arriving on a timer rather than on a bug.
    """
    from custos.register.model import Agent, Identity, Provenance, Source, Status
    from custos.register.store import agent_id
    from custos.store.agents import AgentStore

    db = two_regions.app.state.db
    # An agent discovered in eu-west-1, of the kind that is still running a
    # year later and long after its batches have gone.
    principal = f"arn:aws:iam::{ACCOUNT}:role/west-agent"
    agent = Agent(
        id=agent_id(ACCOUNT, principal), first_seen=W0, last_seen=W0,
        status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.99,
                              observed_principal=principal, evidence=["bytes"]),
        identity=Identity(principal=principal, account_id=ACCOUNT),
    )
    agent.regions = {"eu-west-1"}
    AgentStore(db).upsert(agent)
    db.commit()

    before = two_regions.get("/v1/report", headers=AUTH).text
    assert "eu-west-1, us-east-1 and no other region" in before

    db.execute("DELETE FROM gateway_candidates WHERE scan_id IN "
               "(SELECT s.id FROM scans s JOIN batches b ON b.id = s.batch_id "
               " WHERE b.region = 'eu-west-1')")
    db.execute("DELETE FROM review_candidates WHERE scan_id IN "
               "(SELECT s.id FROM scans s JOIN batches b ON b.id = s.batch_id "
               " WHERE b.region = 'eu-west-1')")
    db.execute("DELETE FROM observations WHERE scan_id IN "
               "(SELECT s.id FROM scans s JOIN batches b ON b.id = s.batch_id "
               " WHERE b.region = 'eu-west-1')")
    db.execute("DELETE FROM scans WHERE batch_id IN "
               "(SELECT id FROM batches WHERE region = 'eu-west-1')")
    db.execute("DELETE FROM batches WHERE region = 'eu-west-1'")
    db.commit()

    after = two_regions.get("/v1/report", headers=AUTH).text
    assert "west-agent" in after, "the register still lists that region's agent"
    assert "eu-west-1, us-east-1 and no other region" in after, (
        "the report unsaid a region whose agents it is still listing"
    )
    # And says why that region is there without any telemetry behind it.
    assert "eu-west-1 appears above because agents were discovered there" in after
    assert "no longer exists" in after
