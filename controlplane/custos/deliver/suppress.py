"""Not sending.

The interesting half of delivery. A tool that reports the same five agents
every week gets muted in week three, and a muted channel is worse than no
channel — it looks like coverage and provides none.

Two rules, and they are different from each other on purpose.

**Repeat suppression.** A finding already delivered is not delivered again
until it has been quiet for a while. The window is per severity, because an
escalation nobody acted on is worth repeating sooner than a note.

**Resolution.** A finding that stops recurring is not announced as resolved.
Nobody wants a message saying an alert they ignored has gone away, and sending
one doubles the volume for no decision made.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .finding import Finding, Severity

REPEAT_AFTER = {
    Severity.ACT_NOW: timedelta(days=3),
    Severity.REVIEW: timedelta(days=14),
    Severity.NOTE: timedelta(days=30),
}
"""How long before an undelivered-again finding may be sent once more.

Three days for an escalation: long enough not to nag, short enough that
something nobody acted on comes back before it is forgotten. Fourteen for a
review item, which is roughly the cadence at which someone triages. Thirty for
a note, which is effectively "next month's digest".
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
    fingerprint   TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    severity      TEXT NOT NULL,
    channel       TEXT NOT NULL,
    delivered_at  TEXT NOT NULL,
    PRIMARY KEY (fingerprint, channel)
);
CREATE INDEX IF NOT EXISTS deliveries_by_account
    ON deliveries (account_id, delivered_at DESC);
"""


@dataclass(slots=True)
class Suppressor:
    """Tracks what has already been said, per channel.

    Per channel rather than globally: a finding sent to Slack has not been sent
    to a SIEM, and treating them as one would silently drop half the
    integration a customer paid for.
    """

    conn: sqlite3.Connection

    def __post_init__(self) -> None:
        self.conn.executescript(SCHEMA)

    def filter(
        self, findings: list[Finding], channel: str, at: datetime
    ) -> tuple[list[Finding], int]:
        """Return (deliverable, suppressed_count)."""
        deliverable: list[Finding] = []
        suppressed = 0

        for finding in findings:
            row = self.conn.execute(
                "SELECT delivered_at FROM deliveries WHERE fingerprint = ? AND channel = ?",
                (finding.fingerprint, channel),
            ).fetchone()

            if row is not None:
                from ..store.db import parse

                window = REPEAT_AFTER.get(finding.severity, timedelta(days=30))
                if at - parse(row["delivered_at"]) < window:
                    suppressed += 1
                    continue

            deliverable.append(finding)

        return deliverable, suppressed

    def record(self, findings: list[Finding], channel: str, at: datetime) -> None:
        """Mark findings as delivered.

        Called only after a channel reports success. Recording before the send
        would silently drop a finding whenever a webhook was briefly down,
        which is exactly when someone is most likely to need it.
        """
        from ..store.db import iso

        self.conn.executemany(
            "INSERT INTO deliveries (fingerprint, account_id, severity, channel, delivered_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(fingerprint, channel) DO UPDATE SET delivered_at = excluded.delivered_at",
            [
                (f.fingerprint, f.account_id, str(f.severity), channel, iso(at))
                for f in findings
            ],
        )

    def forget(self, account_id: str, before: datetime) -> int:
        """Drop delivery history past its usefulness.

        A fingerprint older than the longest repeat window can never suppress
        anything, so keeping it is storage with no behaviour attached.
        """
        from ..store.db import iso

        return self.conn.execute(
            "DELETE FROM deliveries WHERE account_id = ? AND delivered_at < ?",
            (account_id, iso(before)),
        ).rowcount
