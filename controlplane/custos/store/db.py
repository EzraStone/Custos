"""Database connection and migration.

Thin by design. There is no ORM, no query builder, and no session abstraction —
the whole persistence layer is a few hundred lines of SQL that a reviewer can
read, which matters more here than the ergonomics of a large codebase we do not
have.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .schema import ADDED_COLUMNS, BATCHES_TABLE, SCHEMA, SCHEMA_VERSION


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    """Serialise a datetime for storage.

    Timezone-aware only. A naive datetime reaching storage would compare
    incorrectly against flow log timestamps, which are always UTC, and the
    resulting bug would be silent and off by hours.
    """
    if value.tzinfo is None:
        raise ValueError("refusing to store a naive datetime")
    return value.astimezone(UTC).isoformat()


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def dumps(value: object) -> str:
    """Serialise a set or list to JSON, sorted for stable diffs."""
    if isinstance(value, set | frozenset):
        return json.dumps(sorted(value))
    return json.dumps(value)


def loads(value: str | None) -> list:
    if not value:
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return []


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a connection with the settings this application needs."""
    conn = sqlite3.connect(
        path, isolation_level=None, detect_types=0, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    # Foreign keys are off by default in SQLite, which quietly turns every
    # REFERENCES clause in the schema into documentation.
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL, so that a second connection can read while this one writes. The
    # control plane holds a single connection today, so nothing yet takes
    # advantage of it — this is here for the reader connection that a
    # deployment past one process will need, and because turning it on later
    # against a live database is a worse moment to discover it.
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply the schema. Idempotent; returns the resulting version."""
    _widen_batch_key(conn)
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] if row and row["v"] is not None else 0
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, iso(now())),
        )
    return SCHEMA_VERSION


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current column set.

    The schema is applied with CREATE TABLE IF NOT EXISTS, which does exactly
    nothing to a table that already exists. Every column added after a database
    was first created is therefore invisible to it, and the first write naming
    that column fails at runtime — on a customer's control plane, during an
    upgrade, with their register already in the file.

    ADDED_COLUMNS is the list of every column added since the schema was first
    written. Additive only: SQLite can add a nullable column with a default to
    an existing table cheaply and safely, and anything that is not that — a
    rename, a type change, a constraint — needs a real migration written by
    hand rather than a line in this list.
    """
    for table, column, definition in ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _widen_batch_key(conn: sqlite3.Connection) -> None:
    """Rebuild `batches` when its unique key predates regions.

    The one hand-written migration in this file, and it is here because SQLite
    cannot alter a constraint: the key was (account, window) and has to become
    (account, region, window). Everything else the schema has needed since it
    was written was a column, which `_add_missing_columns` does cheaply.

    Why it cannot wait: with the old key, three regions of one account shipping
    the same hour produced one row. The second and third were treated as
    retries of the first, and a region's traffic went in the bin quietly.

    Rows are copied with their ids, so the scans that reference them still do.
    Foreign keys are off for the swap, because DROP TABLE with them on would
    take those scans with it — which is the accident this whole function exists
    to avoid, in a more permanent form.
    """
    tables = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "batches" not in tables:
        return  # a fresh database: SCHEMA creates the current shape

    for index in conn.execute("PRAGMA index_list(batches)"):
        if index["origin"] != "u":
            continue
        columns = {
            row["name"] for row in conn.execute(f"PRAGMA index_info({index['name']!r})")
        }
        if "region" in columns:
            return  # already the current shape

    columns = [row["name"] for row in conn.execute("PRAGMA table_info(batches)")]
    named = ", ".join(columns)

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(conn):
            # execute, not executescript: executescript commits whatever
            # transaction is open before it runs, which would end this one
            # halfway through a table swap.
            conn.execute(
                BATCHES_TABLE.replace("IF NOT EXISTS batches", "batches_migrated")
                .rstrip().rstrip(";")
            )
            conn.execute(
                f"INSERT INTO batches_migrated ({named}) SELECT {named} FROM batches"
            )
            conn.execute("DROP TABLE batches")
            conn.execute("ALTER TABLE batches_migrated RENAME TO batches")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def open_database(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


_WRITES = threading.Lock()
"""Serialises write transactions across the process.

The control plane is one process holding one connection to one SQLite file, and
FastAPI runs its routes in a thread pool. Two accounts shipping on the hour
therefore reach `transaction` on the same connection at the same time, and
SQLite has one transaction per connection: the second `BEGIN` fails with
"cannot start a transaction within a transaction", the request 500s, and that
account's window is dropped. Reproduced before this existed, with two threads
and a shared connection.

A lock rather than a connection per thread. Per-thread connections are the
larger and better answer, and they are not available while the tests and the
scanner share an in-memory database, which exists only inside the connection
that opened it. What this buys is that the second account waits instead of
failing, which is the whole of the difference that matters.

What it does not buy: a read issued while a write transaction is open is on the
same connection and sees the uncommitted rows. The window is one ingest and the
consequence is a console that briefly shows a scan still being written. Holding
every read behind a twenty-second ingest would be the worse trade, so it is
stated rather than prevented.
"""


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one transaction, rolling back on any exception.

    Ingesting a batch touches four tables. A partial write would leave a scan
    row with no observations, which reads downstream as an agent that stopped
    being seen — a drift finding manufactured out of a crash.

    Serialised process-wide: see `_WRITES`.
    """
    with _WRITES:
        conn.execute("BEGIN")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
