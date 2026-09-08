"""What a read costs in queries, for the reads that fan out over accounts.

Most of this system reads one account at a time, and its cost is nobody's
problem. The fleet view is the exception: it is the first screen a customer
with forty accounts sees, it runs once per page load, and it is built from a
loop. A loop over accounts is fine. A loop over accounts containing a loop
over that account's agents is the shape that works on the demo database and
takes nine seconds on the customer who matters most.

These tests count queries rather than time. Timing on a laptop with an empty
SQLite file measures the laptop; the query count is the thing that actually
changes when someone adds a per-row lookup, and it is stable enough to assert.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.store.db import open_database

TOKEN = "tok-fleet"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Comfortably above what the route needs today (7 per account), low enough to
# fail on a per-agent or per-scan lookup added inside the loop. If a genuine
# new column costs an eighth query, raise this deliberately — that is the
# review this number exists to force.
PER_ACCOUNT_BUDGET = 10


class Counter:
    """Counts statements the API issues, ignoring everything before it."""

    def __init__(self, conn):
        self.conn = conn
        self.n = 0

    def __enter__(self):
        self.conn.set_trace_callback(lambda _sql: setattr(self, "n", self.n + 1))
        return self

    def __exit__(self, *_exc):
        self.conn.set_trace_callback(None)
        return False


@pytest.fixture(scope="module")
def payload():
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    return build_batch(corpus.build(corpus.CorpusSpec(days=1))).model_dump(mode="json")


def _fleet(accounts: list[str], payload=None):
    db = open_database()
    app = create_app(conn=db, tokens=TokenStore({TOKEN: set(accounts)}))
    client = TestClient(app)
    for account_id in accounts:
        if payload is not None:
            body = dict(payload) | {"account_id": account_id}
            assert client.post("/v1/batches", json=body, headers=AUTH).status_code == 202
    return db, client


def test_fleet_cost_is_flat_in_agents(payload):
    """The failure this guards against: a lookup per agent inside the loop.

    An account with five agents and an account with five hundred must cost the
    same number of queries, or the customer with the most to find is the one
    who waits."""
    db, client = _fleet(["000000000001"], payload)
    with Counter(db) as loaded:
        assert client.get("/v1/fleet", headers=AUTH).status_code == 200

    empty_db, empty_client = _fleet(["000000000001"])
    with Counter(empty_db) as bare:
        assert empty_client.get("/v1/fleet", headers=AUTH).status_code == 200

    agents = client.get("/v1/fleet", headers=AUTH).json()["accounts"][0]["agents"]
    assert agents >= 5, "this corpus is supposed to produce a register"
    assert loaded.n - bare.n <= 1, (
        f"{agents} agents cost {loaded.n - bare.n} queries more than none"
    )


def test_fleet_cost_is_linear_in_accounts(payload):
    """Linear is the shape this route is allowed to have. Quadratic is not,
    and a join written against every account pair would look fine at three."""
    few = [f"{i:012d}" for i in range(1, 4)]
    many = [f"{i:012d}" for i in range(1, 13)]

    db_few, client_few = _fleet(few, payload)
    with Counter(db_few) as small:
        client_few.get("/v1/fleet", headers=AUTH)

    db_many, client_many = _fleet(many, payload)
    with Counter(db_many) as large:
        client_many.get("/v1/fleet", headers=AUTH)

    assert small.n <= len(few) * PER_ACCOUNT_BUDGET
    assert large.n <= len(many) * PER_ACCOUNT_BUDGET
    # Same per-account cost at four times the size.
    assert large.n / len(many) == pytest.approx(small.n / len(few), abs=1.0)
