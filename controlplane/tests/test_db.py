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


def test_a_column_added_later_reaches_a_database_that_already_exists(tmp_path):
    """The failure this guards against happens on a customer's control plane,
    during an upgrade, with their register already in the file.

    CREATE TABLE IF NOT EXISTS does nothing to a table that is already there,
    so a column added after their first scan would be invisible to their
    database and the first write naming it would fail at runtime.

    The v2 shape of `scans` is written out rather than derived, because it is
    history: what a customer's database actually looks like today does not
    change when the current schema does.
    """
    import sqlite3

    from custos.store.db import migrate, open_database
    from custos.store.schema import ADDED_COLUMNS

    path = tmp_path / "v2.db"
    conn = open_database(path)
    conn.close()

    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE scans")
    raw.execute(
        "CREATE TABLE scans ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, "
        "account_id TEXT NOT NULL, started_at TEXT NOT NULL, "
        "principals_seen INTEGER NOT NULL DEFAULT 0, "
        "agents_found INTEGER NOT NULL DEFAULT 0, "
        "review_candidates INTEGER NOT NULL DEFAULT 0, "
        "coverage REAL NOT NULL DEFAULT 0.0, "
        "truncated INTEGER NOT NULL DEFAULT 0, "
        "catalogue_revision TEXT NOT NULL DEFAULT '')"
    )
    # A row, because that is the case an additive migration has to survive. An
    # empty table would accept a NOT NULL column with no default and prove
    # nothing about a customer with a year of scans.
    raw.execute(
        "INSERT INTO scans (batch_id, account_id, started_at) VALUES (1, '1', '2026-08-10')"
    )
    raw.commit()
    raw.close()

    conn = open_database(path)
    conn.row_factory = sqlite3.Row
    for table, column, _ in ADDED_COLUMNS:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert column in columns, f"{table}.{column} never reached an existing database"

    # The existing row survived, and running the migration again changes nothing.
    assert conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()["n"] == 1
    assert migrate(conn) == migrate(conn)


def test_added_columns_are_additive_only(tmp_path):
    """Every entry must be a nullable column or one with a default.

    SQLite can add those to an existing table cheaply. Anything else — a
    rename, a type change, a NOT NULL with no default — cannot be applied this
    way and needs a migration someone writes by hand, so it must not be
    possible to smuggle one in as a line in the list.
    """
    from custos.store.schema import ADDED_COLUMNS

    for table, column, definition in ADDED_COLUMNS:
        upper = definition.upper()
        assert "NOT NULL" not in upper or "DEFAULT" in upper, (
            f"{table}.{column} is NOT NULL with no default; SQLite cannot add "
            "that to a table with rows in it"
        )
        assert "PRIMARY KEY" not in upper and "UNIQUE" not in upper, (
            f"{table}.{column} adds a constraint; that is a hand-written migration"
        )
