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


def _seed_review(client, principal, confidence=0.5, evidence=("odd bursts",)):
    """Put one review candidate in the store, against a real scan."""
    from types import SimpleNamespace

    from custos.store.scans import ReviewStore, ScanStore

    client.post("/v1/batches", json=batch(start=_seed_review.at), headers=AUTH)
    _seed_review.at += timedelta(hours=1)

    db = client.app.state.db
    scan_id = ScanStore(db).latest_scan(ACCOUNT).id
    ReviewStore(db).record(scan_id, ACCOUNT, [
        SimpleNamespace(
            principal=principal, confidence=confidence,
            evidence=list(evidence), unavailable=[],
        )
    ])
    db.commit()
    return scan_id


_seed_review.at = W0


# The served report built its ScanResult with an empty verdict list, so an
# account whose console showed three maybes got a report saying "For review: 0".
# The report is the artefact that gets forwarded to the workload owner.
def test_report_shows_the_maybes_the_store_kept(client):
    _seed_review(client, "arn:aws:sts::447120043318:assumed-role/nightly-etl/i-07")

    page = client.get("/v1/report", headers=AUTH).text

    assert "For review" in page
    assert "nightly-etl" in page
    assert "odd bursts" in page, "a maybe with no evidence is a rumour"


def test_report_states_how_often_a_maybe_has_recurred(client):
    """One uncertain window is a question about a window. Every window for a
    month is a question about the account."""
    principal = "arn:aws:sts::447120043318:assumed-role/nightly-etl/i-07"
    _seed_review(client, principal)
    once = client.get("/v1/report", headers=AUTH).text
    assert "Seen in" not in once, "one scan is the default and says nothing"

    _seed_review(client, principal)
    twice = client.get("/v1/report", headers=AUTH).text

    assert "Seen in" in twice
    assert "2 scans" in twice


def test_report_review_band_carries_no_sanction_control(client):
    """SEC-17 in the report: the maybes are printed, never actionable from
    here. A button in a static document is a promise nothing can keep."""
    _seed_review(client, "arn:aws:sts::447120043318:assumed-role/nightly-etl/i-07")
    page = client.get("/v1/report", headers=AUTH).text
    # Split on the heading, not the summary label: the label is present even
    # on a report with no maybes at all, and this must not pass vacuously.
    section = page.split("<h2>For review</h2>", 1)[1]
    assert "nightly-etl" in section
    for control in ("<button", "<form", "<input"):
        assert control not in section, control


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


def _declare(client, **kw):
    body = {"value": "10.0.7.0/24", "kind": "range", "operator": "ezra@custos.dev"}
    body.update(kw)
    return client.post("/v1/endpoints", json=body, headers=AUTH)


def test_declaring_an_endpoint_records_who_and_when(client):
    r = _declare(client, note="llm-gateway")
    assert r.status_code == 200
    body = r.json()
    assert body["declared_by"] == "ezra@custos.dev"
    assert body["note"] == "llm-gateway"
    assert body["active"] is True


def test_a_declaration_says_it_takes_effect_next_scan(client):
    """Reclassifying stored telemetry would rewrite the history of what was
    found when. Saying so beats leaving an operator to wonder why the register
    did not move."""
    assert _declare(client).json()["effective"] == "next scan"


def test_declaring_needs_an_operator(client):
    r = client.post(
        "/v1/endpoints",
        json={"value": "10.0.7.0/24", "kind": "range", "operator": ""},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_an_unparseable_range_is_refused_on_the_way_in(client):
    r = _declare(client, value="10.0.7.0/99")
    assert r.status_code == 400
    assert "not a valid network" in r.json()["detail"]
    assert client.get("/v1/endpoints", headers=AUTH).json()["endpoints"] == []


def test_an_unknown_kind_is_refused(client):
    assert _declare(client, kind="cidr").status_code == 422


def test_endpoints_are_listed_for_the_account(client):
    _declare(client, note="llm-gateway")
    listed = client.get("/v1/endpoints", headers=AUTH).json()["endpoints"]
    assert [e["value"] for e in listed] == ["10.0.7.0/24"]


def test_withdrawing_takes_it_out_of_the_active_list(client):
    declaration_id = _declare(client).json()["id"]
    r = client.delete(
        f"/v1/endpoints/{declaration_id}?operator=priya@custos.dev", headers=AUTH
    )
    assert r.status_code == 200
    assert client.get("/v1/endpoints", headers=AUTH).json()["endpoints"] == []


def test_a_withdrawn_declaration_is_still_retrievable(client):
    """Withdrawing can make a finding disappear, so who did it has to survive
    — and that matters most when someone withdraws one for exactly that
    reason."""
    declaration_id = _declare(client).json()["id"]
    client.delete(f"/v1/endpoints/{declaration_id}?operator=priya@custos.dev", headers=AUTH)

    history = client.get(
        "/v1/endpoints?include_withdrawn=true", headers=AUTH
    ).json()["endpoints"]
    assert len(history) == 1
    assert history[0]["active"] is False
    assert history[0]["withdrawn_by"] == "priya@custos.dev"


def test_withdrawing_needs_an_operator(client):
    declaration_id = _declare(client).json()["id"]
    assert client.delete(f"/v1/endpoints/{declaration_id}", headers=AUTH).status_code == 422


def test_withdrawing_something_that_is_not_there_is_404(client):
    r = client.delete("/v1/endpoints/9999?operator=ezra@custos.dev", headers=AUTH)
    assert r.status_code == 404


def test_endpoints_need_a_credential(client):
    assert client.get("/v1/endpoints").status_code == 401
    assert client.post("/v1/endpoints", json={}).status_code == 401


def test_reviews_are_kept_not_just_counted(client, realistic_payload):
    """Only the count was stored, so an operator could see that three
    workloads were uncertain and not which three."""
    _ingest_real_batch(client, realistic_payload)
    body = client.get("/v1/reviews", headers=AUTH).json()

    scan = client.get("/v1/scans", headers=AUTH).json()["scans"][0]
    assert len(body["reviews"]) == scan["review_candidates"]
    if body["reviews"]:
        assert body["reviews"][0]["evidence"], "a maybe with no evidence is a rumour"


def test_reviews_report_how_often_a_workload_recurs(client, realistic_payload):
    """One uncertain window is a different thing from every window for a
    month, and the count is the only way to tell."""
    _ingest_real_batch(client, realistic_payload)
    first = client.get("/v1/reviews", headers=AUTH).json()["reviews"]
    if not first:
        pytest.skip("this corpus produced no review candidates")
    assert first[0]["seen_in_scans"] == 1


@pytest.fixture(scope="module")
def stress_payload():
    """The harder corpus: it contains an agent behind a self-hosted gateway.

    The base corpus deliberately does not, which is why the join below cannot
    be exercised with it — and why the base corpus is the right thing to check
    the join stays silent against.
    """
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    hard = corpus.build(corpus.CorpusSpec(days=1, hard=True))
    payload = build_batch(hard).model_dump(mode="json")
    payload["account_id"] = ACCOUNT
    return payload


# A workload whose model calls go through an address we do not recognise
# produces no finding of any kind, so an account with one looks clean. The
# report has to carry the question, or the document says nothing about the one
# thing that would change how it is read.
def test_the_report_carries_the_open_gateway_questions(client, stress_payload):
    client.post("/v1/batches", json=stress_payload, headers=AUTH)
    page = client.get("/v1/report", headers=AUTH).text

    assert "<h2>Questions</h2>" in page
    assert "10.0.7.40" in page
    assert "Is it a model gateway?" in page
    assert "deploy-remediation" in page, "name who reaches it"


def test_a_declared_address_leaves_the_report(client, stress_payload):
    """Declared is answered. A report that keeps asking makes the declaration
    look ignored."""
    client.post("/v1/batches", json=stress_payload, headers=AUTH)
    assert "<h2>Questions</h2>" in client.get("/v1/report", headers=AUTH).text

    client.post(
        "/v1/endpoints",
        json={"value": "10.0.7.40/32", "kind": "range", "note": "llm-gateway",
              "operator": "ezra@custos.dev"},
        headers=AUTH,
    )
    page = client.get("/v1/report", headers=AUTH).text
    questions = page.split("<h2>Questions</h2>", 1)[1].split("</section>", 1)[0]

    assert "10.0.7.40" not in questions
    # It moves from a question to a stated limitation: the reader still needs
    # to know the account declared it, because that is why anything behind it
    # is visible at all.
    assert "declared 10.0.7.40/32" in page


def test_no_questions_section_when_there_is_nothing_to_ask(client, realistic_payload):
    """The base corpus hides nothing behind a gateway. A section that appeared
    anyway would teach a reader to skip it."""
    _ingest_real_batch(client, realistic_payload)
    assert "<h2>Questions</h2>" not in client.get("/v1/report", headers=AUTH).text


def test_reviews_offer_no_way_into_the_register(client):
    """A route that promoted a maybe by hand would make every guarantee about
    how an agent got into the register conditional on nobody using it."""
    app_routes = {
        (method, getattr(route, "path", ""))
        for route in client.app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    writes = {
        (m, p) for m, p in app_routes
        if p.startswith("/v1/reviews") and m not in {"GET", "HEAD", "OPTIONS"}
    }
    assert not writes, f"the review band has a write path: {writes}"


def test_reviews_need_a_credential(client):
    assert client.get("/v1/reviews").status_code == 401


def test_rates_start_unverified(client):
    body = client.get("/v1/rates", headers=AUTH).json()
    assert body["verified"] is False
    assert body["revision"] == "unverified-placeholder"


def test_supplying_a_rate_makes_it_verified_and_dated(client):
    r = client.post(
        "/v1/rates",
        json={"provider": "anthropic", "input_per_mtok": 1.5,
              "output_per_mtok": 7.5, "operator": "ezra@custos.dev"},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["verified"] is True
    assert r.json()["effective"] == "next scan"
    assert client.get("/v1/rates", headers=AUTH).json()["verified"] is True


def test_a_zero_rate_is_refused(client):
    """A zero would make every agent on that provider look free — the one
    direction this figure must never be wrong in."""
    r = client.post(
        "/v1/rates",
        json={"provider": "anthropic", "input_per_mtok": 0,
              "output_per_mtok": 7.5, "operator": "ezra@custos.dev"},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert client.get("/v1/rates", headers=AUTH).json()["verified"] is False


def test_a_supplied_rate_changes_the_next_scan_and_not_the_last(client, realistic_payload):
    """A report already sent to somebody with a budget should still say what
    it said."""
    agents = _ingest_real_batch(client, realistic_payload)
    if not agents:
        pytest.skip("this corpus produced no agents")
    before = {a["id"]: a["est_monthly_spend_usd"] for a in agents}

    client.post(
        "/v1/rates",
        json={"provider": "anthropic", "input_per_mtok": 30.0,
              "output_per_mtok": 150.0, "operator": "ezra@custos.dev"},
        headers=AUTH,
    )
    unchanged = client.get("/v1/register?unsanctioned_only=true", headers=AUTH).json()["agents"]
    assert {a["id"]: a["est_monthly_spend_usd"] for a in unchanged} == before


def test_rates_keep_every_value_ever_supplied(client):
    for rate in (3.0, 1.5):
        client.post(
            "/v1/rates",
            json={"provider": "anthropic", "input_per_mtok": rate,
                  "output_per_mtok": rate * 5, "operator": "ezra@custos.dev"},
            headers=AUTH,
        )
    history = client.get("/v1/rates", headers=AUTH).json()["history"]
    assert {h["input_per_mtok"] for h in history} == {3.0, 1.5}


def test_rates_need_a_credential(client):
    assert client.get("/v1/rates").status_code == 401
    assert client.post("/v1/rates", json={}).status_code == 401


def _fleet_client(tokens: dict):
    return TestClient(create_app(conn=open_database(), tokens=TokenStore(tokens)))


def test_fleet_lists_every_account_the_credential_covers():
    """Including ones nobody has scanned. An account nobody has looked at is
    not the same as an account with nothing in it, and omitting it would make
    the two identical."""
    client = _fleet_client({"fleet": ["111111111111", "222222222222"]})
    body = client.get("/v1/fleet", headers={"Authorization": "Bearer fleet"}).json()

    assert [a["account_id"] for a in body["accounts"]] == ["111111111111", "222222222222"]
    assert all(a["last_scan"] is None for a in body["accounts"])
    assert all(a["agents"] == 0 for a in body["accounts"])


def test_fleet_counts_what_somebody_would_triage_by(client, realistic_payload):
    _ingest_real_batch(client, realistic_payload)
    row = client.get("/v1/fleet", headers=AUTH).json()["accounts"][0]

    assert row["account_id"] == ACCOUNT
    assert row["unsanctioned"] >= 1
    # Twelve unsanctioned agents that can only read is a different afternoon
    # from one that can delete.
    assert row["destructive"] >= 1
    assert row["last_scan"] is not None
    assert row["coverage"] is not None


def test_fleet_does_not_show_accounts_the_credential_does_not_cover():
    client = _fleet_client({"a": ["111111111111"], "b": ["222222222222"]})
    body = client.get("/v1/fleet", headers={"Authorization": "Bearer a"}).json()
    assert [a["account_id"] for a in body["accounts"]] == ["111111111111"]


def test_fleet_needs_a_credential(client):
    assert client.get("/v1/fleet").status_code == 401


# --- two regions, one window --------------------------------------------------

# The collector reads one region. A customer with agents in three regions
# therefore runs three collectors, which is what preflight tells them to do —
# and all three ship the same account and the same hour.
#
# The batch key is (account, window), so the second arrival was treated as a
# retry: the row was overwritten, the stored region became whichever landed
# last, and the report went on to name that region while the register held
# agents from both.
def test_a_second_region_for_the_same_window_is_refused_not_deduplicated(client):
    east = batch() | {"region": "us-east-1"}
    west = batch() | {"region": "eu-west-1"}

    assert client.post("/v1/batches", json=east, headers=AUTH).status_code == 202

    response = client.post("/v1/batches", json=west, headers=AUTH)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "us-east-1" in detail and "eu-west-1" in detail
    assert "one collector covering both regions" in detail.lower()


def test_a_genuine_retry_of_the_same_region_is_still_a_duplicate(client):
    """The collector retries with bounded backoff, so the same window really
    does arrive twice. Refusing that would turn a flaky network into lost
    telemetry."""
    east = batch() | {"region": "us-east-1"}

    assert client.post("/v1/batches", json=east, headers=AUTH).status_code == 202
    again = client.post("/v1/batches", json=east, headers=AUTH)

    assert again.status_code == 202
    assert again.json()["duplicate"] is True


def test_a_batch_with_no_region_does_not_conflict_with_anything(client):
    """An older collector never sent one. Refusing it would break the upgrade
    path over a field it has no way to populate."""
    assert client.post(
        "/v1/batches", json=batch() | {"region": ""}, headers=AUTH
    ).status_code == 202
    assert client.post(
        "/v1/batches", json=batch() | {"region": "us-east-1"}, headers=AUTH
    ).status_code == 202


def test_different_windows_from_different_regions_are_both_kept(client):
    """The conflict is about one window, not about the account. A customer
    scanning two regions on offset schedules is doing something reasonable."""
    from datetime import timedelta as _td

    east = batch(start=W0) | {"region": "us-east-1"}
    west = batch(start=W0 + _td(hours=1)) | {"region": "eu-west-1"}

    assert client.post("/v1/batches", json=east, headers=AUTH).status_code == 202
    assert client.post("/v1/batches", json=west, headers=AUTH).status_code == 202
    assert len(client.get("/v1/scans", headers=AUTH).json()["scans"]) == 2
