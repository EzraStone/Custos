"""Two accounts shipping at the same time.

The control plane is one process holding one connection to one SQLite file, and
FastAPI runs its routes in a thread pool. Collectors run on the hour. Two of
them therefore arrive together, which is not an edge case — it is the schedule.

Before the write lock existed, the second one 500'd with "cannot start a
transaction within a transaction" and that account's window was dropped. The
collector retries three times against the same clash and then gives up, and
what the customer sees is a scan that did not happen.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.store.db import connect, open_database, transaction

ACCOUNT = "447120043318"
OTHER = "209384756102"
TOKEN = "tok-shared"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
W0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def batch(account, start=W0):
    return {
        "account_id": account,
        "region": "us-east-1",
        "window_start": start.isoformat(),
        "window_end": (start + timedelta(hours=1)).isoformat(),
        "collector_version": "test",
        "flows": [], "requests": [], "principals": [], "attachments": [],
    }


@pytest.fixture
def client():
    app = create_app(
        conn=open_database(), tokens=TokenStore({TOKEN: {ACCOUNT, OTHER}})
    )
    return TestClient(app)


def test_two_accounts_ingesting_at_once_both_succeed(client):
    """The schedule, not an edge case: collectors run on the hour."""
    results: list[int] = []
    lock = threading.Lock()

    def ship(account):
        response = client.post("/v1/batches", json=batch(account), headers=AUTH)
        with lock:
            results.append(response.status_code)

    threads = [
        threading.Thread(target=ship, args=(account,))
        for account in (ACCOUNT, OTHER)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [202, 202], f"one of the two was dropped: {results}"


def test_a_second_writer_waits_rather_than_failing():
    """Directly against the store, without the API in the way.

    Reproduces the original failure: two threads, one connection, explicit
    BEGIN. Before the lock the second raised OperationalError.
    """
    conn = connect()
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, who TEXT)")
    outcomes: list[str] = []

    def write(name, hold):
        try:
            with transaction(conn):
                conn.execute("INSERT INTO t (who) VALUES (?)", (name,))
                time.sleep(hold)
            outcomes.append(f"{name}: committed")
        except Exception as exc:  # noqa: BLE001 - the failure being asserted against
            outcomes.append(f"{name}: {type(exc).__name__}")

    slow = threading.Thread(target=write, args=("acme", 0.3))
    fast = threading.Thread(target=write, args=("globex", 0.0))
    slow.start()
    time.sleep(0.05)
    fast.start()
    slow.join()
    fast.join()

    assert sorted(outcomes) == ["acme: committed", "globex: committed"], outcomes
    assert sorted(r["who"] for r in conn.execute("SELECT who FROM t")) == [
        "acme", "globex",
    ]


def test_a_failed_write_does_not_take_a_concurrent_one_with_it():
    """The consequence worth naming. Both transactions are on one connection,
    so a rollback that escaped its own transaction would discard somebody
    else's committed work."""
    conn = connect()
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, who TEXT)")

    def write(name, fail):
        try:
            with transaction(conn):
                conn.execute("INSERT INTO t (who) VALUES (?)", (name,))
                time.sleep(0.1)
                if fail:
                    raise RuntimeError("this batch is malformed")
        except RuntimeError:
            pass

    keeper = threading.Thread(target=write, args=("keeper", False))
    failer = threading.Thread(target=write, args=("failer", True))
    keeper.start()
    time.sleep(0.02)
    failer.start()
    keeper.join()
    failer.join()

    assert [r["who"] for r in conn.execute("SELECT who FROM t")] == ["keeper"]


def test_reads_are_not_held_behind_a_write(client):
    """A twenty-second ingest must not make the console unusable for twenty
    seconds. This is why reads are outside the lock, and the cost of that
    choice is a brief partial view rather than a blocked one."""
    started = threading.Event()
    release = threading.Event()

    def slow_write():
        from custos.store.db import transaction as tx

        with tx(client.app.state.db):
            started.set()
            release.wait(timeout=5)

    writer = threading.Thread(target=slow_write, daemon=True)
    writer.start()
    assert started.wait(timeout=5)

    began = time.monotonic()
    response = client.get(f"/v1/register?account={ACCOUNT}", headers=AUTH)
    elapsed = time.monotonic() - began

    release.set()
    writer.join(timeout=5)

    assert response.status_code == 200
    assert elapsed < 1.0, f"the read waited {elapsed:.1f}s on an open write"
