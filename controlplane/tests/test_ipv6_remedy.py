"""The remedy the product tells a customer to use, exercised for the family it
exists for.

`MODEL_RANGES` is IPv4 only, deliberately: the providers are reachable over
IPv6 and there is no published v6 range anyone can verify, and guessing one
would manufacture agents out of unrelated traffic. So an agent reaching a
provider over IPv6 is invisible, and three separate surfaces say so — the
preflight warns before a byte is shipped, the report discloses the count, and
both point at the same fix: tell us the addresses and declare them.

Nothing tested that the fix works for IPv6. Every declaration test in this
repository uses a v4 CIDR, which is the family that was never blind. A remedy
offered on three surfaces and verified on none is a claim, and this is the
blind spot the catalogue says matters more every year — AWS began charging for
public IPv4 addresses in 2024 and dual-stack VPCs are the answer.

It works. This is here so it keeps working.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.store.db import open_database

ACCOUNT = "447120043318"
AUTH = {"Authorization": "Bearer tok"}

# A public IPv6 address in no catalogue range and carrying no AWS service
# annotation: exactly the destination the warning is about.
V6 = "2600:1f18:abcd::5"


@pytest.fixture(scope="module")
def over_ipv6():
    """The corpus with every provider destination moved to one v6 address."""
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    payload = build_batch(
        corpus.build(corpus.CorpusSpec(days=1))
    ).model_dump(mode="json")
    payload["account_id"] = ACCOUNT

    provider = {
        f["dstaddr"] for f in payload["flows"] if f["dstaddr"].startswith("160.79.")
    }
    assert provider, "the corpus no longer reaches a catalogued provider by address"
    for flow in payload["flows"]:
        if flow["dstaddr"] in provider:
            flow["dstaddr"] = V6
        if flow.get("srcaddr") in provider:
            flow["srcaddr"] = V6
    return payload


@pytest.fixture
def client():
    return TestClient(
        create_app(conn=open_database(), tokens=TokenStore({"tok": ACCOUNT}))
    )


def _agents(client) -> int:
    return len(client.get("/v1/register", headers=AUTH).json()["agents"])


def _next_window(payload: dict) -> dict:
    """The same traffic, an hour later, so the second scan is a new window."""
    end = datetime.fromisoformat(payload["window_end"])
    return payload | {
        "window_start": payload["window_end"],
        "window_end": (end + timedelta(hours=1)).isoformat(),
    }


def test_an_agent_over_ipv6_is_invisible_until_declared(client, over_ipv6):
    """Both halves in one test, because either alone proves nothing.

    That agents appear after a declaration means nothing if they were there
    before it. That they are missing beforehand means nothing if declaring
    does not bring them back.
    """
    client.post("/v1/batches", json=over_ipv6, headers=AUTH)
    blind = _agents(client)

    declared = client.post(
        "/v1/endpoints",
        json={
            "value": f"{V6}/128", "kind": "range", "operator": "ezra",
            "note": "provider reached over IPv6", "region": "us-east-1",
        },
        headers=AUTH,
    )
    assert declared.status_code == 200, declared.text

    client.post("/v1/batches", json=_next_window(over_ipv6), headers=AUTH)
    seen = _agents(client)

    assert seen > blind, f"declaring the v6 range changed nothing: {blind} -> {seen}"
    assert blind == 2, blind
    assert seen == 5, seen


def test_the_declaration_route_accepts_a_v6_cidr(client):
    """The narrow version of the above, so a validation change fails here
    rather than inside a scan."""
    r = client.post(
        "/v1/endpoints",
        json={
            "value": "2600:1f18::/32", "kind": "range", "operator": "ezra",
            "note": "provider v6 range", "region": "us-east-1",
        },
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "2600:1f18::/32"


def test_a_v6_declaration_does_not_cover_v4(client):
    """Mixed-family membership returns False rather than raising, which is
    worth pinning: a declaration that threw would fail a scan, and one that
    matched everything would manufacture agents from unrelated traffic."""
    from custos.declared import Declaration, build

    declared = build([Declaration("2600:1f18::/32", "range", "v6")])
    assert declared.covers("2600:1f18::9")
    assert not declared.covers("10.0.0.1")
    assert not declared.covers("160.79.104.10")


def test_the_report_discloses_the_blindness_before_it_is_fixed(client, over_ipv6):
    """The count is what tells a customer to use the remedy at all."""
    client.post("/v1/batches", json=over_ipv6, headers=AUTH)
    page = client.get("/v1/report", headers=AUTH).text
    assert "IPv6" in page
