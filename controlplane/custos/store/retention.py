"""Retention.

Two reasons this exists, and the second is the one that matters commercially.

The database grows without bound otherwise. An account scanned hourly produces
8,760 scans and rather more observations a year, and none of the old ones are
read after the baseline window has moved past them.

More importantly, "how long do you keep our data" is a question in every
security review, and the only good answer is a number with a mechanism behind
it. "Indefinitely, we have not thought about it" ends the conversation.

What is kept and what is dropped is deliberate:

  agents      forever. The register IS the product, and losing a sanction
              decision means asking a customer to re-review forty agents —
              a conversation that ends a pilot.
  audit       forever. It is the answer to "why is this agent sanctioned",
              and an audit trail with a retention window is not one.
  observations  a rolling window. They feed baselines, and a baseline built
              from year-old behaviour is not describing the workload that runs
              today.
  batches/scans  a rolling window, longer than observations, so scan history
              outlives the detail behind it.
  deliveries  the longest repeat window plus a margin. A fingerprint older
              than that can never suppress anything, so keeping it is storage
              with no behaviour attached.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from .db import iso, now, transaction

DEFAULT_OBSERVATION_DAYS = 90
"""Long enough for a baseline to mean something across quarterly patterns,
short enough that a year-old deployment is not shaping today's normal."""

DEFAULT_SCAN_DAYS = 365
"""Scan history is small and answers 'when did this start', which is the first
question after any finding."""


@dataclass(frozen=True, slots=True)
class Pruned:
    observations: int = 0
    scans: int = 0
    batches: int = 0
    deliveries: int = 0

    @property
    def total(self) -> int:
        return self.observations + self.scans + self.batches + self.deliveries


def prune(
    conn: sqlite3.Connection,
    observation_days: int = DEFAULT_OBSERVATION_DAYS,
    scan_days: int = DEFAULT_SCAN_DAYS,
) -> Pruned:
    """Drop telemetry past its retention window.

    Agents and audit entries are never touched. Deleting a register entry would
    lose a sanction decision, and deleting an audit entry would lose the answer
    to why one was made.
    """
    if observation_days > scan_days:
        # Observations reference scans. Keeping them longer than the scans they
        # point at would orphan them, and the foreign key would refuse the
        # delete anyway — better to say why than to surface a constraint error.
        raise ValueError(
            "observation retention cannot exceed scan retention: observations "
            "reference the scans they came from"
        )

    stamp = now()
    observation_cutoff = iso(stamp - timedelta(days=observation_days))
    scan_cutoff = iso(stamp - timedelta(days=scan_days))

    # Delivery history outlives its longest repeat window by a week, so a
    # finding cannot be re-delivered merely because its record was pruned a day
    # before the window expired.
    from ..deliver.suppress import REPEAT_AFTER

    delivery_cutoff = iso(stamp - max(REPEAT_AFTER.values()) - timedelta(days=7))

    with transaction(conn):
        observations = conn.execute(
            "DELETE FROM observations WHERE observed_at < ?", (observation_cutoff,)
        ).rowcount
        scans = conn.execute(
            "DELETE FROM scans WHERE started_at < ?", (scan_cutoff,)
        ).rowcount
        # Only batches with no surviving scan. A batch whose scan is still
        # inside its window has to stay, or the scan points at nothing.
        batches = conn.execute(
            "DELETE FROM batches WHERE received_at < ? AND id NOT IN "
            "(SELECT batch_id FROM scans)",
            (scan_cutoff,),
        ).rowcount
        deliveries = conn.execute(
            "DELETE FROM deliveries WHERE delivered_at < ?", (delivery_cutoff,)
        ).rowcount

    return Pruned(
        observations=max(observations, 0),
        scans=max(scans, 0),
        batches=max(batches, 0),
        deliveries=max(deliveries, 0),
    )


def vacuum(conn: sqlite3.Connection) -> None:
    """Reclaim space after a prune.

    SQLite does not return freed pages to the filesystem on its own, so a
    database that pruned a year of observations stays the same size on disk —
    which looks exactly like a prune that did not work.
    """
    conn.execute("VACUUM")
