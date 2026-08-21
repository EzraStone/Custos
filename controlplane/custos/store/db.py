"""Database connection and migration.

Thin by design. There is no ORM, no query builder, and no session abstraction —
the whole persistence layer is a few hundred lines of SQL that a reviewer can
read, which matters more here than the ergonomics of a large codebase we do not
have.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .schema import SCHEMA, SCHEMA_VERSION


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
    # WAL lets a report render while a scan writes. Without it the console
    # blocks behind ingestion, which is exactly when someone is watching.
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply the schema. Idempotent; returns the resulting version."""
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] if row and row["v"] is not None else 0
    if current < SCHEMA_VERSION:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, iso(now())),
        )
    return SCHEMA_VERSION


def open_database(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block in one transaction, rolling back on any exception.

    Ingesting a batch touches four tables. A partial write would leave a scan
    row with no observations, which reads downstream as an agent that stopped
    being seen — a drift finding manufactured out of a crash.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
