from datetime import UTC, datetime

import pytest

from custos.store.db import dumps, iso, loads, open_database, parse, transaction

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def test_migration_is_idempotent():
    conn = open_database()
    from custos.store.db import migrate

    assert migrate(conn) == migrate(conn)
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
    assert rows["n"] == 1


def test_foreign_keys_are_enforced():
    """Off by default in SQLite, which turns every REFERENCES clause into a
    comment. If this fails, orphaned observations become possible."""
    conn = open_database()
    with pytest.raises(Exception, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO observations (scan_id, agent_id, observed_at) VALUES (?, ?, ?)",
            (999, "agt_missing", iso(T0)),
        )


def test_naive_datetimes_are_refused():
    """A naive datetime compares incorrectly against flow log timestamps, and
    the resulting bug is silent and off by hours."""
    with pytest.raises(ValueError, match="naive"):
        # Deliberately naive: this is the value the guard exists to reject.
        iso(datetime(2026, 8, 10, 12, 0))  # noqa: DTZ001


def test_datetime_roundtrip_preserves_utc():
    assert parse(iso(T0)) == T0


def test_sets_serialise_sorted_for_stable_diffs():
    assert dumps({"c", "a", "b"}) == '["a", "b", "c"]'
    assert loads(dumps({"c", "a"})) == ["a", "c"]


def test_loads_tolerates_missing_and_corrupt_values():
    assert loads(None) == []
    assert loads("") == []
    assert loads("{not json") == []


def test_transaction_rolls_back_on_failure():
    """A partial batch write leaves a scan with no observations, which reads
    downstream as an agent that stopped being seen — a drift finding
    manufactured out of a crash."""
    conn = open_database()
    with pytest.raises(RuntimeError), transaction(conn) as tx:
        tx.execute(
            "INSERT INTO batches (account_id, window_start, window_end, received_at) "
            "VALUES (?, ?, ?, ?)",
            ("1", iso(T0), iso(T0), iso(T0)),
        )
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"] == 0


def test_transaction_commits_on_success():
    conn = open_database()
    with transaction(conn) as tx:
        tx.execute(
            "INSERT INTO batches (account_id, window_start, window_end, received_at) "
            "VALUES (?, ?, ?, ?)",
            ("1", iso(T0), iso(T0), iso(T0)),
        )
    assert conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"] == 1
