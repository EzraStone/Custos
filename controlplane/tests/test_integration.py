"""The whole loop, in the order it happens for a customer.

Collector output shape → API → register → sanction → second scan → diff.

Every seam in this path is covered by a unit test somewhere. This exists
because the seams are where the bugs have actually been: signal availability
derived from the wrong scope, a diff comparing a scan against itself, a cached
report going stale after a grant. None of those were visible from inside the
component that owned them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.batch import Batch
from custos.store.db import open_database

ACCOUNT = "447120043318"
AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture(scope="module")
def first_batch() -> Batch:
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    batch = build_batch(corpus.build(corpus.CorpusSpec(days=1)))
    return batch.model_copy(update={"account_id": ACCOUNT})


@pytest.fixture
def client():
    app = create_app(conn=open_database(), tokens=TokenStore({"tok": ACCOUNT}))
    return TestClient(app)


def escalate(batch: Batch, principal_fragment: str, action: str) -> Batch:
    """Return the next window, with one principal's role widened."""
    principals = [
        p.model_copy(update={"actions": sorted({*p.actions, action})})
        if principal_fragment in p.principal else p
        for p in batch.principals
    ]
    span = batch.window_end - batch.window_start
    return batch.model_copy(update={
        "window_start": batch.window_end,
        "window_end": batch.window_end + span,
        "principals": principals,
    })


def test_the_whole_loop(client, first_batch):
    # 1. The collector ships a window.
    accepted = client.post(
        "/v1/batches", json=first_batch.model_dump(mode="json"), headers=AUTH
    )
    assert accepted.status_code == 202
    assert accepted.json()["agents_found"] == 5

    # 2. Everything found is unsanctioned and nothing was authorised (SEC-17).
    agents = client.get(
        "/v1/register?unsanctioned_only=true", headers=AUTH
    ).json()["agents"]
    assert len(agents) == 5
    assert all(a["status"] == "discovered" for a in agents)
    assert all(a["imprimatur"] is None for a in agents)

    # 3. Findings are ordered worst first and carry arguable evidence.
    assert agents[0]["blast_radius"] == "destructive"
    assert agents[0]["evidence"]

    # 4. An operator sanctions the one they recognise.
    approved = agents[0]
    granted = client.post(
        f"/v1/agents/{approved['id']}/imprimatur",
        json={"operator": "ezra@custos.dev"}, headers=AUTH,
    )
    assert granted.status_code == 200
    assert granted.json()["imprimatur"]["granted_by"] == "ezra@custos.dev"

    # 5. It leaves the recurring set. That set is the subscription.
    remaining = client.get(
        "/v1/register?unsanctioned_only=true", headers=AUTH
    ).json()["agents"]
    assert len(remaining) == 4

    # 6. A week later, one role gains a destructive permission.
    second = escalate(first_batch, "finance-close", "s3:DeleteBucket")
    assert client.post(
        "/v1/batches", json=second.model_dump(mode="json"), headers=AUTH
    ).status_code == 202

    # 7. The sanction survives the re-scan.
    still = client.get(f"/v1/agents/{approved['id']}/audit", headers=AUTH).json()
    assert [e["action"] for e in still["entries"]] == ["discovered", "sanctioned"]

    # 8. And the escalation is visible in the report.
    report = client.get("/v1/report", headers=AUTH).text
    assert "finance-close" in report
    assert "destructive" in report


def test_a_redelivered_window_changes_nothing(client, first_batch):
    payload = first_batch.model_dump(mode="json")
    client.post("/v1/batches", json=payload, headers=AUTH)
    before = client.get("/v1/register", headers=AUTH).json()["agents"]

    again = client.post("/v1/batches", json=payload, headers=AUTH)
    assert again.json()["duplicate"] is True

    after = client.get("/v1/register", headers=AUTH).json()["agents"]
    assert len(after) == len(before)
    assert {a["id"] for a in after} == {a["id"] for a in before}


def test_a_scan_without_access_logs_loses_recall_not_precision(client, first_batch):
    """The measured consequence of the onboarding ask, end to end."""
    from custos.batch import Collection

    blind = first_batch.model_copy(update={
        "requests": [],
        "collection": Collection(
            lines_read=len(first_batch.flows),
            lines_parsed=len(first_batch.flows),
            have_access_logs=False,
        ),
    })
    client.post("/v1/batches", json=blind.model_dump(mode="json"), headers=AUTH)

    found = client.get("/v1/register", headers=AUTH).json()["agents"]
    # Fewer confirmed than the 5 found with access logs, and none of them wrong.
    assert 0 < len(found) < 5
    principals = {a["principal"] for a in found}
    for chatbot in ("docs-chat", "kb-assistant", "sales-copilot", "search-embed"):
        assert not any(chatbot in p for p in principals), chatbot


def test_every_wire_flow_field_survives_the_conversion():
    """The contract test compares the Go struct to the Pydantic model. Neither
    of them sees this function, which copies one into the other field by field
    — so a field can be on the wire, accepted by the API, and dropped here
    without a single test noticing. That is how src_aws_service was lost the
    day it was added."""
    from custos import batch as schema
    from custos.pipeline import _to_telemetry

    wire_only = {"account_id"}  # carried on the batch, not per record
    sample = schema.FlowRecord(
        account_id="1", interface_id="eni-1", srcaddr="10.0.1.5",
        dstaddr="52.216.10.7", srcport=41000, dstport=443, protocol=6,
        packets=9, bytes=1234,
        start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        end=datetime(2026, 8, 10, 12, 1, tzinfo=UTC),
        action="ACCEPT", log_status="OK", vpc_id="vpc-1", subnet_id="subnet-1",
        direction="egress", src_aws_service="ELASTICACHE", dst_aws_service="S3",
        tcp_flags=18,
    )
    b = schema.Batch(
        account_id="1",
        window_start=sample.start, window_end=sample.end,
        flows=[sample],
    )
    records, _ = _to_telemetry(b)
    got = records[0]

    for name in schema.FlowRecord.model_fields:
        if name in wire_only:
            continue
        want = getattr(sample, name)
        have = getattr(got, name)
        assert str(have) == str(want), f"{name} was dropped or changed: {want!r} -> {have!r}"


def test_collector_destination_names_reach_the_scan():
    from custos import batch as schema
    from custos.pipeline import to_scan_input

    b = schema.Batch(
        account_id="1",
        window_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        window_end=datetime(2026, 8, 10, 13, 0, tzinfo=UTC),
        destinations=[
            schema.Destination(address="10.0.4.23", name="billing-api", kind="load-balancer"),
            schema.Destination(address="10.0.4.24", name="", kind=""),
        ],
    )
    names = to_scan_input(b).destination_names
    # An entry with no name is not a name. Carrying it would make the register
    # show an empty label where it currently shows an honest address.
    assert names == {"10.0.4.23": "billing-api"}
