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


def test_the_batch_cap_matches_the_collector(client):
    """Both are set by measurement, and a mismatch means either the collector
    ships batches the API refuses, or the API accepts batches that take it
    down."""
    from pathlib import Path

    from custos.api.app import MAX_FLOWS_PER_BATCH

    root = Path(__file__).resolve().parents[2]
    source = root / "collector" / "internal" / "ingest" / "cloudwatch.go"
    if not source.exists():
        import pytest

        pytest.skip("collector source not present")

    text = source.read_text()
    assert f"MaxEventsPerRun = {MAX_FLOWS_PER_BATCH:_}" in text, (
        "the API cap and the collector cap have drifted"
    )


# --- multi-account tokens -----------------------------------------------------

FLEET = {"111111111111", "222222222222"}


@pytest.fixture
def fleet_client():
    app = create_app(
        conn=open_database(), tokens=TokenStore({"tok-fleet": FLEET})
    )
    return TestClient(app)


FLEET_AUTH = {"Authorization": "Bearer tok-fleet"}


def test_a_fleet_token_ships_for_any_account_it_covers(fleet_client):
    for account in sorted(FLEET):
        response = fleet_client.post(
            "/v1/batches", json=batch(account=account), headers=FLEET_AUTH
        )
        assert response.status_code == 202, account


def test_a_fleet_token_still_cannot_ship_for_an_account_it_lacks(fleet_client):
    response = fleet_client.post(
        "/v1/batches", json=batch(account="999999999999"), headers=FLEET_AUTH
    )
    assert response.status_code == 403


# Defaulting to the first account would attribute one account's findings to
# another — quietly, and in the direction that makes a report wrong rather than
# empty.
def test_a_fleet_token_must_say_which_account_it_means(fleet_client):
    response = fleet_client.get("/v1/register", headers=FLEET_AUTH)
    assert response.status_code == 400
    assert "pass ?account=" in response.json()["detail"]


def test_a_fleet_token_reads_a_named_account(fleet_client):
    fleet_client.post("/v1/batches", json=batch(account="111111111111"), headers=FLEET_AUTH)
    body = fleet_client.get("/v1/register?account=111111111111", headers=FLEET_AUTH).json()
    assert body["account_id"] == "111111111111"


# A distinct response would confirm the account exists to someone holding a
# credential for a different one.
def test_an_uncovered_account_is_not_found_rather_than_forbidden(fleet_client):
    assert fleet_client.get(
        "/v1/register?account=999999999999", headers=FLEET_AUTH
    ).status_code == 404


def test_a_single_account_token_needs_no_parameter(client):
    assert client.get("/v1/register", headers=AUTH).status_code == 200


def test_agents_are_only_reachable_by_a_token_covering_their_account(
    fleet_client, realistic_payload
):
    payload = dict(realistic_payload)
    payload["account_id"] = "111111111111"
    fleet_client.post("/v1/batches", json=payload, headers=FLEET_AUTH)
    agents = fleet_client.get(
        "/v1/register?account=111111111111&unsanctioned_only=true", headers=FLEET_AUTH
    ).json()["agents"]

    other = TestClient(create_app(
        conn=open_database(), tokens=TokenStore({"tok-other": "999999999999"})
    ))
    assert other.get(
        f"/v1/agents/{agents[0]['id']}/audit",
        headers={"Authorization": "Bearer tok-other"},
    ).status_code == 404


# --- delivery -----------------------------------------------------------------

class _CountingChannel:
    """Records what it was asked to send instead of sending it."""

    def __init__(self, name="slack", fail=False):
        self.name = name
        self.fail = fail
        self.batches = []

    def send(self, findings, at):
        from custos.deliver import Delivery

        if self.fail:
            return Delivery(channel=self.name, sent=0, error="endpoint down")
        self.batches.append(list(findings))
        return Delivery(channel=self.name, sent=len(findings))


def _client_with(channel):
    return TestClient(create_app(
        conn=open_database(), tokens=TokenStore({TOKEN: ACCOUNT}), channels=[channel],
    ))


# A scheduled collector shipping to the API got no notifications at all, so
# continuous operation was silently report-only.
def test_ingestion_delivers_findings(realistic_payload):
    channel = _CountingChannel()
    client = _client_with(channel)

    response = client.post("/v1/batches", json=realistic_payload, headers=AUTH)
    assert response.status_code == 202
    assert response.json()["delivered"] > 0
    assert len(channel.batches) == 1


# The batch was accepted and the findings are in the register regardless of
# whether anyone was told.
def test_a_delivery_failure_does_not_reject_the_batch(realistic_payload):
    client = _client_with(_CountingChannel(fail=True))

    response = client.post("/v1/batches", json=realistic_payload, headers=AUTH)
    assert response.status_code == 202
    assert response.json()["delivered"] == 0
    assert response.json()["agents_found"] == 5


def test_no_channels_configured_delivers_nothing_and_says_zero(client, realistic_payload):
    response = client.post("/v1/batches", json=realistic_payload, headers=AUTH)
    assert response.json()["delivered"] == 0


def test_a_redelivered_window_does_not_re_notify(realistic_payload):
    channel = _CountingChannel()
    client = _client_with(channel)

    client.post("/v1/batches", json=realistic_payload, headers=AUTH)
    again = client.post("/v1/batches", json=realistic_payload, headers=AUTH)

    assert again.json()["duplicate"] is True
    assert len(channel.batches) == 1, "a retried window must not alert twice"


# --- console ------------------------------------------------------------------

def _app_with_console(tmp_path, index="<!doctype html><title>Custos</title>"):
    (tmp_path / "index.html").write_text(index)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('custos')")

    import os

    os.environ["CUSTOS_CONSOLE_DIR"] = str(tmp_path)
    try:
        return TestClient(create_app(
            conn=open_database(), tokens=TokenStore({TOKEN: ACCOUNT}),
        ))
    finally:
        del os.environ["CUSTOS_CONSOLE_DIR"]


def test_the_console_is_served_at_the_root(tmp_path):
    client = _app_with_console(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Custos" in response.text


def test_console_assets_are_served(tmp_path):
    client = _app_with_console(tmp_path)
    assert client.get("/assets/app.js").status_code == 200


# A static mount at the root is greedy. Registered before the API it would
# shadow /v1 and /healthz, and the symptom would be index.html returned where
# JSON was expected — which reads as a client bug rather than a routing one.
def test_the_api_is_still_routed_before_the_console(tmp_path):
    client = _app_with_console(tmp_path)

    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/v1/register").status_code == 401
    assert client.post("/v1/batches", json=batch(), headers=AUTH).status_code == 202


# The control plane is useful without a console and must not refuse to start
# because nobody ran npm run build.
def test_a_missing_console_build_is_not_an_error(tmp_path):
    import os

    os.environ["CUSTOS_CONSOLE_DIR"] = str(tmp_path / "does-not-exist")
    try:
        client = TestClient(create_app(
            conn=open_database(), tokens=TokenStore({TOKEN: ACCOUNT}),
        ))
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 404
    finally:
        del os.environ["CUSTOS_CONSOLE_DIR"]


def _client_for(tokens: dict) -> TestClient:
    return TestClient(create_app(conn=open_database(), tokens=TokenStore(tokens)))


def test_accounts_lists_what_the_credential_covers():
    client = _client_for({"fleet": ["111111111111", "222222222222"]})
    r = client.get("/v1/accounts", headers={"Authorization": "Bearer fleet"})
    assert r.status_code == 200
    assert r.json() == {"accounts": ["111111111111", "222222222222"]}


def test_accounts_needs_a_credential():
    client = _client_for({"fleet": ["111111111111"]})
    assert client.get("/v1/accounts").status_code == 401


def test_accounts_does_not_leak_accounts_the_token_does_not_cover():
    # Two tokens, disjoint scopes. The listing is per-credential, not global:
    # a token covering one account must not learn that the other exists.
    client = _client_for({"a": ["111111111111"], "b": ["222222222222"]})
    r = client.get("/v1/accounts", headers={"Authorization": "Bearer a"})
    assert r.json() == {"accounts": ["111111111111"]}


def _scan_twice(client, headers, *, second_flows=None):
    """Ingest two windows so there is something to compare."""
    first = batch(start=W0)
    client.post("/v1/batches", json=first, headers=headers)
    second = batch(start=W0 + timedelta(hours=1), flows=second_flows)
    client.post("/v1/batches", json=second, headers=headers)


def test_diff_needs_two_scans_before_it_says_anything(client):
    client.post("/v1/batches", json=batch(), headers=AUTH)
    body = client.get("/v1/diff", headers=AUTH).json()
    # One scan is the normal state of a new account, not an error. A 404 here
    # would make every client special-case the first week.
    assert body["previous_scan_id"] is None
    assert body["changes"] == []
    assert "one scan" in body["headline"]


def test_diff_reports_between_the_two_most_recent_scans(client):
    _scan_twice(client, AUTH)
    body = client.get("/v1/diff", headers=AUTH).json()
    assert body["previous_scan_id"] is not None
    assert body["current_scan_id"] != body["previous_scan_id"]


def test_diff_omits_the_agents_that_did_not_move(client):
    _scan_twice(client, AUTH)
    body = client.get("/v1/diff", headers=AUTH).json()
    assert all(c["kind"] != "unchanged" for c in body["changes"])


def test_diff_needs_a_credential(client):
    assert client.get("/v1/diff").status_code == 401


def test_diff_is_scoped_to_the_account(client):
    r = client.get("/v1/diff?account=999999999999", headers=AUTH)
    assert r.status_code == 404


def test_scans_report_how_readable_the_scope_was(client):
    client.post("/v1/batches", json=batch(), headers=AUTH)
    scan = client.get("/v1/scans", headers=AUTH).json()["scans"][0]
    assert "scope_readable" in scan
    assert scan["scope_total"] == 0
    # Nothing internal reached is fully readable, not fully unreadable. There
    # is no unreadable scope on a scan with no destinations, and warning about
    # one would be the empty-read-as-full-coverage mistake in reverse.
    assert scan["scope_readable"] == 1.0


def test_drift_on_an_agent_with_no_history_is_empty_not_an_error(client, realistic_payload):
    agents = _ingest_real_batch(client, realistic_payload)
    r = client.get(f"/v1/agents/{agents[0]['id']}/drift", headers=AUTH)
    assert r.status_code == 200

    # A new finding having no baseline is its normal state, not a failure.
    body = r.json()
    assert body["drift"] == []
    assert body["baseline"]["established"] is False
    assert body["observations"] == 1


def test_drift_says_whether_the_baseline_means_anything(client, realistic_payload):
    """A caller showing drift from an unestablished baseline is showing noise
    with a confident label on it, so the endpoint says which it is."""
    agents = _ingest_real_batch(client, realistic_payload)
    body = client.get(f"/v1/agents/{agents[0]['id']}/drift", headers=AUTH).json()
    assert "established" in body["baseline"]
    assert isinstance(body["baseline"]["tools"], list)


def test_drift_needs_a_credential(client):
    assert client.get("/v1/agents/agt_whatever/drift").status_code == 401


def test_drift_on_an_unknown_agent_is_404(client):
    r = client.get("/v1/agents/agt_nope/drift", headers=AUTH)
    assert r.status_code == 404
