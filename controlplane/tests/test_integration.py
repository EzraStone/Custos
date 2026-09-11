"""The whole loop, in the order it happens for a customer.

Collector output shape → API → register → sanction → second scan → diff.

Every seam in this path is covered by a unit test somewhere. This exists
because the seams are where the bugs have actually been: signal availability
derived from the wrong scope, a diff comparing a scan against itself, a cached
report going stale after a grant. None of those were visible from inside the
component that owned them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def test_a_named_destination_survives_the_whole_loop():
    """Collector wire → API → classifier → register → the scope an operator
    approves.

    Five modules, each tested on its own. This asserts the one property none of
    them can: that a name the collector resolved is the name a person is shown
    when they are asked to confer authority. Every seam in that path has been
    wrong at least once — the annotation read from the wrong end of a flow
    record, the label built per record instead of per address, the field
    dropped in the batch-to-telemetry copy.
    """
    from custos import batch as schema
    from custos.api import TokenStore, create_app
    from custos.store.db import open_database

    account, token = "447120043318", "tok-loop"
    client = TestClient(create_app(conn=open_database(), tokens=TokenStore({token: account})))
    headers = {"Authorization": f"Bearer {token}"}

    start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    body = _agent_shaped_batch(
        schema, account, start,
        destinations=[
            schema.Destination(address="10.0.4.21", name="billing-api", kind="load-balancer"),
        ],
    )

    assert client.post(
        "/v1/batches", json=body.model_dump(mode="json"), headers=headers
    ).status_code == 202

    agents = client.get("/v1/register", headers=headers).json()["agents"]
    assert agents, "the batch produced no agents to check the scope of"

    reach = {r for a in agents for r in a["tools"] + a["data_stores"]}
    assert "billing-api 10.0.4.21" in reach, f"the name never reached the register: {reach}"
    # The address alone must not also be present: that was the duplicate-entry
    # bug, one destination occupying two slots in the same approval.
    assert "10.0.4.21" not in reach


def _agent_shaped_batch(schema, account: str, start, destinations=()):
    """Traffic with the shape of an agent: model egress, no inbound, a tool."""
    flows = []
    for minute in range(40):
        at = start + timedelta(minutes=minute)
        flows.append(schema.FlowRecord(
            account_id=account, interface_id="eni-1", srcaddr="10.0.1.5",
            dstaddr="160.79.104.10", srcport=41000 + minute, dstport=443,
            protocol=6, packets=40, bytes=140_000,
            start=at, end=at + timedelta(seconds=30), action="ACCEPT",
            log_status="OK", direction="egress", tcp_flags=2,
        ))
        flows.append(schema.FlowRecord(
            account_id=account, interface_id="eni-1", srcaddr="160.79.104.10",
            dstaddr="10.0.1.5", srcport=443, dstport=41000 + minute,
            protocol=6, packets=8, bytes=9_000,
            start=at, end=at + timedelta(seconds=30), action="ACCEPT",
            log_status="OK", direction="ingress", tcp_flags=16,
        ))
        flows.append(schema.FlowRecord(
            account_id=account, interface_id="eni-1", srcaddr="10.0.1.5",
            dstaddr="10.0.4.21", srcport=42000 + minute, dstport=8080,
            protocol=6, packets=6, bytes=3_000,
            start=at, end=at + timedelta(seconds=20), action="ACCEPT",
            log_status="OK", direction="egress", tcp_flags=2,
        ))

    return schema.Batch(
        account_id=account, region="us-east-1",
        window_start=start, window_end=start + timedelta(hours=1),
        collector_version="test",
        collection=schema.Collection(
            lines_read=len(flows), lines_parsed=len(flows), have_access_logs=True
        ),
        flows=flows,
        destinations=list(destinations),
        attachments=[schema.Attachment(
            interface_id="eni-1", principal=f"arn:aws:iam::{account}:role/finance-close",
            address="10.0.1.5", compute="Lambda",
        )],
    )


def test_declaring_a_gateway_makes_its_agents_visible_on_the_next_scan():
    """The whole point of the mechanism, through the API a customer uses.

    The same traffic, ingested twice. Before the declaration it is a workload
    talking to an internal API and there is no agent. After it, the agent is
    in the register. Nothing about the traffic changed — only what the account
    told us its addresses mean.
    """
    from custos import batch as schema
    from custos.api import TokenStore, create_app
    from custos.store.db import open_database
    from custos.store.declarations import DeclarationStore

    account, token = "447120043318", "tok-gw"
    conn = open_database()
    client = TestClient(create_app(conn=conn, tokens=TokenStore({token: account})))
    headers = {"Authorization": f"Bearer {token}"}

    start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    blind = _gateway_batch(schema, account, start, "10.0.7.9")
    assert client.post(
        "/v1/batches", json=blind.model_dump(mode="json"), headers=headers
    ).status_code == 202
    assert client.get("/v1/register", headers=headers).json()["agents"] == []

    DeclarationStore(conn).declare(
        account, "10.0.7.0/24", "range", "ezra@custos.dev", "llm-gateway",
        # A private range needs the region it was asked about: 10.0.7.9 is the
        # gateway here and something else in every other region.
        region="us-east-1",
    )
    conn.commit()

    later = _gateway_batch(schema, account, start + timedelta(hours=2), "10.0.7.9")
    assert client.post(
        "/v1/batches", json=later.model_dump(mode="json"), headers=headers
    ).status_code == 202

    agents = client.get("/v1/register", headers=headers).json()["agents"]
    assert agents, "the agent stayed invisible after its gateway was declared"


def _gateway_batch(schema, account: str, start, gateway: str):
    """An agent whose model calls all go through one internal address.

    It also queries a database every other minute, because an agent does. A
    workload whose only destination is one address is a log shipper, and the
    detector stopped asking about those — so a fixture without the tool leg
    would be testing a shape no agent has.
    """
    flows = []
    for minute in range(40):
        at = start + timedelta(minutes=minute)
        if minute % 2 == 0:
            flows.append(schema.FlowRecord(
                account_id=account, interface_id="eni-1", srcaddr="10.0.1.5",
                dstaddr="10.0.9.44", srcport=52000 + minute, dstport=5432,
                protocol=6, packets=12, bytes=4_000,
                start=at, end=at + timedelta(seconds=30), action="ACCEPT",
                log_status="OK", direction="egress", tcp_flags=2,
            ))
            flows.append(schema.FlowRecord(
                account_id=account, interface_id="eni-1", srcaddr="10.0.9.44",
                dstaddr="10.0.1.5", srcport=5432, dstport=52000 + minute,
                protocol=6, packets=30, bytes=30_000,
                start=at, end=at + timedelta(seconds=30), action="ACCEPT",
                log_status="OK", direction="ingress", tcp_flags=16,
            ))
        flows.append(schema.FlowRecord(
            account_id=account, interface_id="eni-1", srcaddr="10.0.1.5",
            dstaddr=gateway, srcport=41000 + minute, dstport=443,
            protocol=6, packets=40, bytes=140_000,
            start=at, end=at + timedelta(seconds=30), action="ACCEPT",
            log_status="OK", direction="egress", tcp_flags=2,
        ))
        flows.append(schema.FlowRecord(
            account_id=account, interface_id="eni-1", srcaddr=gateway,
            dstaddr="10.0.1.5", srcport=443, dstport=41000 + minute,
            protocol=6, packets=8, bytes=9_000,
            start=at, end=at + timedelta(seconds=30), action="ACCEPT",
            log_status="OK", direction="ingress", tcp_flags=16,
        ))
    return schema.Batch(
        account_id=account, region="us-east-1",
        window_start=start, window_end=start + timedelta(hours=1),
        collector_version="test",
        collection=schema.Collection(
            lines_read=len(flows), lines_parsed=len(flows), have_access_logs=True
        ),
        flows=flows,
        attachments=[schema.Attachment(
            interface_id="eni-1", principal=f"arn:aws:iam::{account}:role/gateway-agent",
            address="10.0.1.5", compute="Lambda",
        )],
    )


def test_a_gateway_candidate_becomes_a_question_and_then_stops_being_one():
    """The loop this whole mechanism exists for, end to end.

    A scan finds an address that behaves like a model endpoint and asks about
    it. Somebody answers by declaring it. The next scan finds the agent — and
    stops asking the question, because it has been answered.
    """
    from custos import batch as schema
    from custos.api import TokenStore, create_app
    from custos.store.db import open_database

    account, token = "447120043318", "tok-cand"
    conn = open_database()
    client = TestClient(create_app(conn=conn, tokens=TokenStore({token: account})))
    headers = {"Authorization": f"Bearer {token}"}

    start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    client.post(
        "/v1/batches",
        json=_gateway_batch(schema, account, start, "10.0.7.9").model_dump(mode="json"),
        headers=headers,
    )

    asked = client.get("/v1/gateway-candidates", headers=headers).json()["candidates"]
    assert [c["address"] for c in asked] == ["10.0.7.9"]
    assert "Is it a model gateway?" in asked[0]["question"]
    assert client.get("/v1/register", headers=headers).json()["agents"] == []

    declared = client.post(
        "/v1/endpoints",
        json={"value": "10.0.7.0/24", "kind": "range",
              "operator": "ezra@custos.dev", "note": "llm-gateway",
              "region": "us-east-1"},
        headers=headers,
    )
    assert declared.status_code == 200

    client.post(
        "/v1/batches",
        json=_gateway_batch(
            schema, account, start + timedelta(hours=2), "10.0.7.9"
        ).model_dump(mode="json"),
        headers=headers,
    )

    assert client.get("/v1/register", headers=headers).json()["agents"], (
        "the agent stayed invisible after its gateway was declared"
    )
    # And the question is not asked again. Somebody answered it.
    assert client.get("/v1/gateway-candidates", headers=headers).json()["candidates"] == []


# --- an account that runs in more than one region -----------------------------

def test_two_regions_of_one_account_build_one_register(client):
    """The shape a real customer has: agents in two regions, one collector
    covering both, two batches for the same hour.

    Before regions were part of a batch's identity the second was treated as a
    retry of the first — one row survived, and half the account's agents were
    never classified at all.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    for region in ("us-east-1", "eu-west-1"):
        payload = build_batch(small, region=region).model_dump(mode="json")
        payload["account_id"] = ACCOUNT
        assert client.post("/v1/batches", json=payload, headers=AUTH).status_code == 202

    scans = client.get("/v1/scans", headers=AUTH).json()["scans"]
    assert len(scans) == 2, "a region was swallowed as a duplicate"

    agents = client.get("/v1/register", headers=AUTH).json()["agents"]
    assert agents, "the corpus produces agents"
    for agent in agents:
        assert sorted(agent["regions"]) == ["eu-west-1", "us-east-1"], (
            "the same role runs in both regions and the register should say so"
        )


def test_the_report_names_the_region_of_the_scan_it_rendered(client):
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    payload = build_batch(small, region="eu-west-1").model_dump(mode="json")
    payload["account_id"] = ACCOUNT
    client.post("/v1/batches", json=payload, headers=AUTH)

    page = client.get("/v1/report", headers=AUTH).text
    assert "covers eu-west-1 and no other region" in page


def test_a_records_region_survives_the_whole_pipeline(client):
    """It is stamped by the collector, crosses the wire, and has to reach the
    telemetry the classifier reads — a field dropped in the middle would be
    invisible until two regions collided over one address."""
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    from custos.pipeline import _to_telemetry

    batch = build_batch(corpus.build(corpus.CorpusSpec(days=1)), region="ap-south-1")
    records, _ = _to_telemetry(batch)

    assert records
    assert {r.region for r in records} == {"ap-south-1"}


def test_the_served_report_names_every_region_it_shows_agents_from(client):
    """The served report renders the whole register — every agent, from every
    region the account has been collected in — but it used to take its region
    label from the latest scan alone, which is one region.

    So a two-region account got a report listing agents from both and a
    limitations section saying "eu-west-1 and no other region". The list is
    what a customer forwards to their security team; the sentence under it
    told them a region they can see agents from was not covered.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    for region in ("us-east-1", "eu-west-1"):
        payload = build_batch(small, region=region).model_dump(mode="json")
        payload["account_id"] = ACCOUNT
        client.post("/v1/batches", json=payload, headers=AUTH)

    page = client.get("/v1/report", headers=AUTH).text
    assert "eu-west-1, us-east-1 and no other region" in page, (
        "the report named one region while listing agents from two"
    )


def test_a_field_missing_in_one_region_is_reported_as_that_regions_gap(client):
    """The flow log format is set per flow log, and a two-region account has
    two of them. The report used to take the latest scan's format and describe
    it as the account's — so a port field switched on in one region and off in
    the other read as "this account records no ports", which is a gap nobody
    can locate and a claim that is false about half the estate.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    for region, missing in (("us-east-1", ["dstport"]), ("eu-west-1", [])):
        payload = build_batch(small, region=region).model_dump(mode="json")
        payload["account_id"] = ACCOUNT
        payload["collection"]["missing_fields"] = missing
        assert client.post("/v1/batches", json=payload, headers=AUTH).status_code == 202

    page = client.get("/v1/report", headers=AUTH).text
    assert "Destination ports were not recorded" in page, (
        "the region with no port field was described by the region that has one"
    )
    assert "That is true of us-east-1; eu-west-1 does record it." in page


def test_one_region_reading_badly_puts_the_banner_on_the_whole_report(client):
    """The banner is above everything because a caveat at the bottom is a
    caveat nobody reads before forming a conclusion. Taking it from the latest
    scan meant a region that read 40% of its flow log was covered up by a
    region that read all of its own — the report came out clean, with no
    banner, over an estate a third of which nobody had seen.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    for region, parsed in (("us-east-1", 40), ("eu-west-1", 100)):
        payload = build_batch(small, region=region).model_dump(mode="json")
        payload["account_id"] = ACCOUNT
        payload["collection"] |= {"lines_read": 100, "lines_parsed": parsed}
        assert client.post("/v1/batches", json=payload, headers=AUTH).status_code == 202

    page = client.get("/v1/report", headers=AUTH).text
    assert "Incomplete coverage" in page, "the bad region was covered up by the good one"
    assert "40% of flow log lines parsed in us-east-1" in page


def test_failed_reads_are_counted_across_every_region(client):
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    for region, errors in (("us-east-1", 3), ("eu-west-1", 4)):
        payload = build_batch(small, region=region).model_dump(mode="json")
        payload["account_id"] = ACCOUNT
        payload["collection"]["read_errors"] = errors
        client.post("/v1/batches", json=payload, headers=AUTH)

    page = client.get("/v1/report", headers=AUTH).text
    assert "7 AWS reads failed" in page, "one region's failures stood for the account's"


def test_principals_seen_is_not_one_regions_count_beside_every_regions_agents(client):
    """The masthead prints "Principals seen" next to "Agents found". The
    second is the whole register; the first was the latest scan's, which is
    one region — so the ratio a reader takes from the header was between two
    different populations.

    Summing them would be worse: an IAM role is account-wide, so a role
    running in two regions would be counted twice.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    small = corpus.build(corpus.CorpusSpec(days=1))
    for region in ("us-east-1", "eu-west-1"):
        payload = build_batch(small, region=region).model_dump(mode="json")
        payload["account_id"] = ACCOUNT
        client.post("/v1/batches", json=payload, headers=AUTH)

    page = client.get("/v1/report", headers=AUTH).text
    assert "counts one region" in page, "the header ratio was left unexplained"
