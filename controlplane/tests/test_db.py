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


# --- the one hand-written migration -------------------------------------------

OLD_BATCHES = """
CREATE TABLE batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    region        TEXT    NOT NULL DEFAULT '',
    window_start  TEXT    NOT NULL,
    window_end    TEXT    NOT NULL,
    collector     TEXT    NOT NULL DEFAULT '',
    received_at   TEXT    NOT NULL,
    flow_records  INTEGER NOT NULL DEFAULT 0,
    requests      INTEGER NOT NULL DEFAULT 0,
    have_alb_logs INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, window_start, window_end)
);
"""


def _old_database(path):
    """A database written before regions were part of a batch's identity."""
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(OLD_BATCHES)
    conn.execute(
        "INSERT INTO batches (id, account_id, region, window_start, window_end, "
        "received_at, flow_records) VALUES (7, '447120043318', 'us-east-1', "
        "'2026-08-10T12:00:00+00:00', '2026-08-10T13:00:00+00:00', 'then', 41)"
    )
    conn.commit()
    conn.close()
    return path


def _unique_columns(conn, table):
    for index in conn.execute(f"PRAGMA index_list({table})"):
        if index["origin"] == "u":
            return [r["name"] for r in conn.execute(f"PRAGMA index_info('{index['name']}')")]
    return []


def test_an_old_database_gains_the_region_in_its_batch_key(tmp_path):
    """With the old key, three regions of one account shipping the same hour
    produced one row: the second and third were treated as retries of the
    first, and a region's traffic went in the bin quietly."""
    from custos.store.db import open_database

    conn = open_database(_old_database(tmp_path / "old.db"))
    assert _unique_columns(conn, "batches") == [
        "account_id", "region", "window_start", "window_end",
    ]


def test_the_migration_keeps_the_rows_and_their_ids(tmp_path):
    """Scans reference batches by id. A rebuild that renumbered them would
    point every historical scan at the wrong window, or at nothing."""
    from custos.store.db import open_database

    conn = open_database(_old_database(tmp_path / "old.db"))
    row = conn.execute("SELECT * FROM batches").fetchone()

    assert row["id"] == 7
    assert row["account_id"] == "447120043318"
    assert row["region"] == "us-east-1"
    assert row["flow_records"] == 41


def test_the_migration_does_not_take_the_scans_with_it(tmp_path):
    """DROP TABLE with foreign keys on would cascade to scans — which is the
    accident this migration exists to prevent, in a more permanent form."""
    from custos.store.db import open_database

    path = _old_database(tmp_path / "old.db")
    seeded = open_database(path)
    seeded.execute(
        "INSERT INTO scans (batch_id, account_id, started_at, principals_seen, "
        "agents_found, review_candidates, coverage, truncated, catalogue_revision) "
        "VALUES (7, '447120043318', 'then', 3, 1, 0, 1.0, 0, 'r')"
    )
    seeded.commit()
    seeded.close()

    conn = open_database(path)
    assert conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()["n"] == 1
    assert conn.execute("SELECT batch_id FROM scans").fetchone()["batch_id"] == 7


def test_migrating_twice_changes_nothing(tmp_path):
    from custos.store.db import open_database

    path = _old_database(tmp_path / "old.db")
    open_database(path).close()
    conn = open_database(path)

    assert conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"] == 1
    assert _unique_columns(conn, "batches") == [
        "account_id", "region", "window_start", "window_end",
    ]


def test_a_second_region_fits_after_the_migration(tmp_path):
    from custos.store.db import open_database

    conn = open_database(_old_database(tmp_path / "old.db"))
    conn.execute(
        "INSERT INTO batches (account_id, region, window_start, window_end, "
        "received_at) VALUES ('447120043318', 'eu-west-1', "
        "'2026-08-10T12:00:00+00:00', '2026-08-10T13:00:00+00:00', 'now')"
    )
    assert conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"] == 2


def test_a_fresh_database_is_already_the_current_shape():
    from custos.store.db import open_database

    assert _unique_columns(open_database(), "batches") == [
        "account_id", "region", "window_start", "window_end",
    ]


def test_every_added_column_reaches_a_database_that_predates_it(tmp_path):
    """The test above proves it for `scans`, by writing out the v2 shape of
    that one table. Every other table's added columns were only ever checked
    against a database that already had them, which proves nothing.

    This takes a current database, removes every column in the list, puts a row
    in each affected table, and reopens it — which is the upgrade a customer
    performs, with their register already in the file.
    """
    import sqlite3

    from custos.store.db import open_database
    from custos.store.schema import ADDED_COLUMNS

    path = tmp_path / "old.db"
    open_database(path).close()

    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    for table, column, _ in ADDED_COLUMNS:
        raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    # A row per affected table, because an empty table would accept anything
    # and prove nothing about a customer with a year of history.
    raw.execute(
        "INSERT INTO batches (id, account_id, window_start, window_end, received_at) "
        "VALUES (1, '1', '2026-08-10T12:00:00+00:00', '2026-08-10T13:00:00+00:00', 'then')"
    )
    raw.execute(
        "INSERT INTO scans (id, batch_id, account_id, started_at) "
        "VALUES (1, 1, '1', '2026-08-10T12:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO agents (id, account_id, principal, status, first_seen, last_seen, "
        "source, confidence, evidence) VALUES ('agt_1', '1', 'role/x', 'discovered', "
        "'2026-08-10T12:00:00+00:00', '2026-08-10T12:00:00+00:00', 'discovered', 0.9, '[]')"
    )
    raw.execute(
        "INSERT INTO observations (scan_id, agent_id, observed_at) "
        "VALUES (1, 'agt_1', '2026-08-10T12:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO declared_endpoints (account_id, value, kind, declared_by, declared_at) "
        "VALUES ('1', '10.0.7.0/24', 'range', 'ezra', '2026-08-10T12:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO gateway_candidates (scan_id, account_id, address, egress, ingress, "
        "principals, blind, question) VALUES (1, '1', '10.0.7.40', 5, 1, '[]', '[]', 'q?')"
    )
    raw.commit()
    raw.close()

    conn = open_database(path)
    conn.row_factory = sqlite3.Row
    for table, column, _ in ADDED_COLUMNS:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert column in columns, f"{table}.{column} never reached an existing database"

    for table in ("batches", "scans", "agents", "observations",
                  "declared_endpoints", "gateway_candidates"):
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608
        assert n == 1, f"the upgrade lost {table}'s row"


def test_an_upgraded_database_can_still_take_a_scan(tmp_path):
    """Columns arriving is not the same as the code that writes them working.
    A default of '[]' read back through a hydrator expecting a mapping is a
    migration that passes its own test and fails on the first ingest.
    """
    import sqlite3

    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    from custos.pipeline import ingest
    from custos.store.db import open_database
    from custos.store.schema import ADDED_COLUMNS

    path = tmp_path / "upgrade.db"
    open_database(path).close()
    raw = sqlite3.connect(path)
    for table, column, _ in ADDED_COLUMNS:
        raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    raw.commit()
    raw.close()

    conn = open_database(path)
    batch = build_batch(corpus.build(corpus.CorpusSpec(days=1)), region="us-east-1")
    outcome = ingest(conn, batch)
    assert outcome.result.register.agents, "an upgraded database took no agents"


def test_no_table_carries_a_comment_inside_its_definition():
    """SQLite's ALTER TABLE rewrites the stored CREATE TABLE text, and a
    comment inside the parentheses makes DROP COLUMN and RENAME COLUMN fail
    with "incomplete input".

    It fails on a customer's database, during an upgrade, in a migration that
    worked everywhere it was tested — because the schema is created fresh in
    every test and only an existing database is ever altered. The `scans` table
    had four such comments and they cost this file two hours.
    """
    import re

    from custos.store.schema import BATCHES_TABLE, SCHEMA

    for body in re.finditer(r"CREATE TABLE[^(]*\((.*?)\n\);", SCHEMA + BATCHES_TABLE, re.S):
        assert "--" not in body.group(1), (
            "a comment inside a CREATE TABLE body breaks ALTER TABLE; put it "
            "above the statement"
        )
