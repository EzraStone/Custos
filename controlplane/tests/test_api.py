"""API behaviour, with emphasis on the ways it must refuse."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.store.db import open_database

ACCOUNT = "447120043318"
OTHER = "999999999999"
TOKEN = "tok-acme"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
W0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def client():
    app = create_app(conn=open_database(), tokens=TokenStore({TOKEN: ACCOUNT}))
    return TestClient(app)


def batch(account=ACCOUNT, start=W0, end=None, flows=None):
    return {
        "account_id": account,
        "region": "us-east-1",
        "window_start": start.isoformat(),
        "window_end": (end or start + timedelta(hours=1)).isoformat(),
        "collector_version": "test",
        "flows": flows or [],
        "requests": [],
        "principals": [],
        "attachments": [],
    }


def test_health_reports_the_revisions_that_decide_what_a_finding_means(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["catalogue_revision"]
    assert body["prices_revision"]


@pytest.mark.parametrize("path", ["/v1/register", "/v1/scans"])
def test_read_endpoints_require_a_credential(client, path):
    assert client.get(path).status_code == 401


def test_ingestion_requires_a_credential(client):
    assert client.post("/v1/batches", json=batch()).status_code == 401


def test_mutating_endpoints_require_a_credential(client):
    assert client.post(
        "/v1/agents/agt_x/imprimatur", json={"operator": "ezra"}
    ).status_code == 401
    assert client.post(
        "/v1/agents/agt_x/status", json={"status": "retired", "operator": "ezra"}
    ).status_code == 401


def test_missing_and_wrong_credentials_are_indistinguishable(client):
    missing = client.get("/v1/register")
    wrong = client.get("/v1/register", headers={"Authorization": "Bearer nope"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


# A token names one account. Shipping telemetry for another is either a
# misconfiguration or an attempt to poison someone else's register.
def test_a_token_cannot_ship_telemetry_for_another_account(client):
    response = client.post("/v1/batches", json=batch(account=OTHER), headers=AUTH)
    assert response.status_code == 403


def test_a_token_cannot_read_another_accounts_register(client):
    client.post("/v1/batches", json=batch(), headers=AUTH)
    body = client.get("/v1/register", headers=AUTH).json()
    assert body["account_id"] == ACCOUNT


def test_batch_is_accepted_and_reports_what_it_found(client):
    response = client.post("/v1/batches", json=batch(), headers=AUTH)
    assert response.status_code == 202
    body = response.json()
    assert body["duplicate"] is False
    assert "batch_id" in body and "scan_id" in body


def test_redelivered_window_is_reported_as_a_duplicate(client):
    client.post("/v1/batches", json=batch(), headers=AUTH)
    again = client.post("/v1/batches", json=batch(), headers=AUTH)
    assert again.json()["duplicate"] is True


def test_inverted_window_is_refused(client):
    bad = batch(start=W0, end=W0 - timedelta(hours=1))
    assert client.post("/v1/batches", json=bad, headers=AUTH).status_code == 422


def test_unknown_fields_are_refused_rather_than_ignored(client):
    """SEC-18 at the HTTP boundary: a modified collector shipping prompts gets
    an error, not a silent accept."""
    payload = batch() | {"prompt": "you are a helpful assistant"}
    assert client.post("/v1/batches", json=payload, headers=AUTH).status_code == 422


def test_coverage_note_states_what_the_scan_could_not_see(client):
    """A scan that found nothing because it could not see is not a clean
    account, and the response has to say which one it was."""
    note = client.post("/v1/batches", json=batch(), headers=AUTH).json()["coverage_note"]
    assert "load balancer" in note
    assert "blast radius" in note


@pytest.fixture(scope="module")
def realistic_payload():
    """The synthetic corpus, built once.

    Generating it costs a couple of seconds and every test that needs a
    realistic batch needs the same one. Rebuilding per test made this module
    the slowest in the suite, and a slow suite is one that stops being run
    before a commit.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    # One day rather than the full three. These tests exercise API behaviour,
    # not classifier accuracy — that is a0/tests/test_g0.py's job, against the
    # full corpus — so the extra volume buys nothing here and costs seconds on
    # every run.
    small = corpus.build(corpus.CorpusSpec(days=1))
    payload = build_batch(small).model_dump(mode="json")
    payload["account_id"] = ACCOUNT
    return payload


def _ingest_real_batch(client, payload):
    assert client.post("/v1/batches", json=payload, headers=AUTH).status_code == 202
    return client.get("/v1/register?unsanctioned_only=true", headers=AUTH).json()["agents"]


def test_register_returns_findings_worst_first_with_evidence(client, realistic_payload):
    agents = _ingest_real_batch(client, realistic_payload)
    assert agents
    assert agents[0]["blast_radius"] == "destructive"
    assert agents[0]["evidence"], "a finding without its evidence is just a score"


def test_sec17_discovered_agents_are_never_sanctioned_by_ingestion(client, realistic_payload):
    for agent in _ingest_real_batch(client, realistic_payload):
        assert agent["status"] == "discovered"
        assert agent["imprimatur"] is None
        assert agent["unsanctioned"] is True


def test_granting_imprimatur_requires_an_operator(client, realistic_payload):
    agent = _ingest_real_batch(client, realistic_payload)[0]
    response = client.post(
        f"/v1/agents/{agent['id']}/imprimatur", json={"operator": ""}, headers=AUTH
    )
    assert response.status_code == 422


def test_granting_imprimatur_records_the_human(client, realistic_payload):
    agent = _ingest_real_batch(client, realistic_payload)[0]
    response = client.post(
        f"/v1/agents/{agent['id']}/imprimatur",
        json={"operator": "ezra@custos.dev"}, headers=AUTH,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sanctioned"
    assert body["imprimatur"]["granted_by"] == "ezra@custos.dev"
    assert body["unsanctioned"] is False


def test_sanctioning_removes_an_agent_from_the_unsanctioned_set(client, realistic_payload):
    before = _ingest_real_batch(client, realistic_payload)
    client.post(
        f"/v1/agents/{before[0]['id']}/imprimatur",
        json={"operator": "ezra"}, headers=AUTH,
    )
    after = client.get("/v1/register?unsanctioned_only=true", headers=AUTH).json()["agents"]
    assert len(after) == len(before) - 1


def test_status_endpoint_cannot_reach_sanctioned(client, realistic_payload):
    """SEC-17 over HTTP: there is one door, and this is not it."""
    agent = _ingest_real_batch(client, realistic_payload)[0]
    response = client.post(
        f"/v1/agents/{agent['id']}/status",
        json={"status": "sanctioned", "operator": "ezra"}, headers=AUTH,
    )
    assert response.status_code == 409
    assert "grant_imprimatur" in response.json()["detail"]


def test_unknown_agent_is_not_found(client):
    response = client.post(
        "/v1/agents/agt_nonexistent/imprimatur",
        json={"operator": "ezra"}, headers=AUTH,
    )
    assert response.status_code == 404


def test_audit_trail_names_who_did_what(client, realistic_payload):
    agent = _ingest_real_batch(client, realistic_payload)[0]
    client.post(
        f"/v1/agents/{agent['id']}/imprimatur",
        json={"operator": "ezra@custos.dev"}, headers=AUTH,
    )
    entries = client.get(f"/v1/agents/{agent['id']}/audit", headers=AUTH).json()["entries"]
    assert [e["action"] for e in entries] == ["discovered", "sanctioned"]
    assert entries[-1]["actor"] == "ezra@custos.dev"


def test_scans_are_listed(client):
    client.post("/v1/batches", json=batch(), headers=AUTH)
    body = client.get("/v1/scans", headers=AUTH).json()
    assert len(body["scans"]) == 1


def test_openapi_schema_is_not_served(client):
    """A public schema browser on a security product is an invitation."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# --- rendered report ----------------------------------------------------------

def test_report_renders_the_current_register(client, realistic_payload):
    _ingest_real_batch(client, realistic_payload)
    response = client.get("/v1/report", headers=AUTH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "unsanctioned agent" in response.text


def test_report_requires_a_credential(client):
    assert client.get("/v1/report").status_code == 401


# Caching the rendered page at ingestion would mean a report that silently goes
# stale after a sanction and keeps showing an agent as unsanctioned after
# someone approved it.
def test_report_reflects_a_sanction_immediately(client, realistic_payload):
    agents = _ingest_real_batch(client, realistic_payload)
    sanctioned = agents[0]
    name = sanctioned["principal"].rsplit("/", 1)[-1]

    before = client.get("/v1/report", headers=AUTH).text
    assert name in before

    client.post(
        f"/v1/agents/{sanctioned['id']}/imprimatur",
        json={"operator": "ezra@custos.dev"}, headers=AUTH,
    )
    after = client.get("/v1/report", headers=AUTH).text

    assert name not in after, "a sanctioned agent must leave the findings list"
    assert before.count("<article") - after.count("<article") == 1


def test_report_is_self_contained(client, realistic_payload):
    _ingest_real_batch(client, realistic_payload)
    page = client.get("/v1/report", headers=AUTH).text
    for external in ("http://", "https://", "<script", "src="):
        assert external not in page, external


def test_report_on_an_empty_account_renders_rather_than_failing(client):
    response = client.get("/v1/report", headers=AUTH)
    assert response.status_code == 200
    assert "None found" in response.text


# --- logging ------------------------------------------------------------------

def _log_stream():
    import io

    from custos.logging import configure

    stream = io.StringIO()
    configure(stream=stream)
    return stream


def _events(stream):
    import json

    return [json.loads(ln) for ln in stream.getvalue().strip().splitlines() if ln]


def test_requests_are_logged_without_query_strings(client):
    """A path is a route template we wrote; a query string is caller-supplied
    and could hold anything."""
    stream = _log_stream()
    client.get("/v1/register?unsanctioned_only=true", headers=AUTH)

    requests = [e for e in _events(stream) if e["event"] == "http.request"]
    assert requests
    assert requests[0]["path"] == "/v1/register"
    assert "unsanctioned_only" not in stream.getvalue()
    assert "client_ip" not in stream.getvalue()


def test_sanctioning_is_logged_as_its_own_event(client, realistic_payload):
    """The only action that grants authority. It must survive a log level that
    drops request noise."""
    agents = _ingest_real_batch(client, realistic_payload)
    stream = _log_stream()

    client.post(
        f"/v1/agents/{agents[0]['id']}/imprimatur",
        json={"operator": "ezra@custos.dev"}, headers=AUTH,
    )

    sanctioned = [e for e in _events(stream) if e["event"] == "agent.sanctioned"]
    assert len(sanctioned) == 1
    assert sanctioned[0]["operator"] == "ezra@custos.dev"


def test_credentials_never_appear_in_a_log_line(client):
    stream = _log_stream()
    client.get("/v1/register", headers={"Authorization": "Bearer super-secret-token"})
    assert "super-secret-token" not in stream.getvalue()


def test_ingestion_logs_what_it_found(client, realistic_payload):
    stream = _log_stream()
    client.post("/v1/batches", json=realistic_payload, headers=AUTH)

    ingested = [e for e in _events(stream) if e["event"] == "batch.ingested"]
    assert len(ingested) == 1
    assert ingested[0]["agents_found"] == 5
    assert "coverage" in ingested[0]
