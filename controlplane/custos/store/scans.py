"""Batch and scan persistence.

Batches are telemetry as shipped. Scans are what a classifier made of a batch.
Keeping them apart costs a table and buys two things: a re-classification of
old telemetry can be compared against the original run, which is how a
classifier change gets evaluated against real traffic rather than against the
synthetic corpus; and a collector retry updates a batch rather than adding one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from .db import dumps, iso, loads, parse


@dataclass(frozen=True, slots=True)
class BatchRecord:
    id: int
    account_id: str
    window_start: datetime
    window_end: datetime
    flow_records: int
    requests: int
    have_alb_logs: bool
    duplicate: bool = False
    """True when this window had already been received.

    Surfaced rather than hidden: a collector retrying every window would mean
    something is wrong with its scheduling, and the customer's API budget is
    paying for it."""


@dataclass(frozen=True, slots=True)
class ScanRecord:
    id: int
    batch_id: int
    account_id: str
    started_at: datetime
    principals_seen: int
    agents_found: int
    review_candidates: int
    coverage: float
    truncated: bool


class ScanStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record_batch(
        self,
        account_id: str,
        region: str,
        window_start: datetime,
        window_end: datetime,
        collector: str,
        received_at: datetime,
        flow_records: int,
        requests: int,
        have_alb_logs: bool,
    ) -> BatchRecord:
        """Store a batch, replacing any earlier delivery of the same window.

        Idempotent on (account, window). The collector retries with bounded
        backoff, so the same window genuinely does arrive twice, and counting it
        twice would inflate every byte total downstream — which means every
        spend estimate and every egress ratio the classifier reads.
        """
        existing = self.conn.execute(
            "SELECT id FROM batches WHERE account_id = ? AND window_start = ? "
            "AND window_end = ?",
            (account_id, iso(window_start), iso(window_end)),
        ).fetchone()

        if existing is not None:
            self.conn.execute(
                "UPDATE batches SET collector = ?, received_at = ?, flow_records = ?, "
                "requests = ?, have_alb_logs = ?, region = ? WHERE id = ?",
                (collector, iso(received_at), flow_records, requests,
                 int(have_alb_logs), region, existing["id"]),
            )
            batch_id, duplicate = existing["id"], True
        else:
            cursor = self.conn.execute(
                "INSERT INTO batches (account_id, region, window_start, window_end, "
                "collector, received_at, flow_records, requests, have_alb_logs) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, region, iso(window_start), iso(window_end), collector,
                 iso(received_at), flow_records, requests, int(have_alb_logs)),
            )
            batch_id, duplicate = cursor.lastrowid, False

        return BatchRecord(
            id=batch_id, account_id=account_id,
            window_start=window_start, window_end=window_end,
            flow_records=flow_records, requests=requests,
            have_alb_logs=have_alb_logs, duplicate=duplicate,
        )

    def record_scan(
        self,
        batch_id: int,
        account_id: str,
        started_at: datetime,
        principals_seen: int,
        agents_found: int,
        review_candidates: int,
        coverage: float,
        truncated: bool,
        catalogue_revision: str,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO scans (batch_id, account_id, started_at, principals_seen, "
            "agents_found, review_candidates, coverage, truncated, catalogue_revision) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, account_id, iso(started_at), principals_seen, agents_found,
             review_candidates, coverage, int(truncated), catalogue_revision),
        )
        return cursor.lastrowid

    def record_observation(
        self,
        scan_id: int,
        agent_id: str,
        observed_at: datetime,
        confidence: float,
        model_egress: int,
        model_ingress: int,
        episodes: int,
        calls_per_hour: float,
        tools: set[str],
        active_hours: dict[int, float],
        blast_radius: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO observations (scan_id, agent_id, observed_at, confidence, "
            "model_egress, model_ingress, episodes, calls_per_hour, tools, "
            "active_hours, blast_radius) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scan_id, agent_id, iso(observed_at), confidence, model_egress,
             model_ingress, episodes, calls_per_hour, dumps(tools),
             dumps({str(k): v for k, v in active_hours.items()}), blast_radius),
        )

    def scans_for(self, account_id: str, limit: int = 20) -> list[ScanRecord]:
        return [
            ScanRecord(
                id=row["id"], batch_id=row["batch_id"], account_id=row["account_id"],
                started_at=parse(row["started_at"]),
                principals_seen=row["principals_seen"],
                agents_found=row["agents_found"],
                review_candidates=row["review_candidates"],
                coverage=row["coverage"], truncated=bool(row["truncated"]),
            )
            for row in self.conn.execute(
                "SELECT * FROM scans WHERE account_id = ? ORDER BY started_at DESC, id DESC "
                "LIMIT ?",
                (account_id, limit),
            )
        ]

    def latest_scan(self, account_id: str) -> ScanRecord | None:
        scans = self.scans_for(account_id, limit=1)
        return scans[0] if scans else None

    def latest_scan_before(self, account_id: str, scan_id: int) -> ScanRecord | None:
        """The most recent scan preceding `scan_id`.

        Used to pick the comparison baseline during ingestion, where the
        current scan row already exists. Taking `latest_scan` there would
        compare a scan against itself and report that nothing ever changes.
        """
        row = self.conn.execute(
            "SELECT * FROM scans WHERE account_id = ? AND id < ? "
            "ORDER BY started_at DESC, id DESC LIMIT 1",
            (account_id, scan_id),
        ).fetchone()
        if row is None:
            return None
        return ScanRecord(
            id=row["id"], batch_id=row["batch_id"], account_id=row["account_id"],
            started_at=parse(row["started_at"]),
            principals_seen=row["principals_seen"], agents_found=row["agents_found"],
            review_candidates=row["review_candidates"],
            coverage=row["coverage"], truncated=bool(row["truncated"]),
        )

    def observations_for_scan(self, scan_id: int) -> dict[str, dict]:
        """Observations from one scan, keyed by agent id."""
        out: dict[str, dict] = {}
        for row in self.conn.execute(
            "SELECT * FROM observations WHERE scan_id = ?", (scan_id,)
        ):
            record = dict(row)
            record["tools"] = set(loads(row["tools"]))
            record["observed_at"] = parse(row["observed_at"])
            out[row["agent_id"]] = record
        return out

    def observation_history(self, agent_id: str, limit: int = 30) -> list[dict]:
        """Most recent observations for one agent, oldest first.

        Oldest first because every consumer is computing a trend, and reversing
        a list at each call site is how an off-by-one gets into a baseline.
        """
        rows = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM observations WHERE agent_id = ? "
                "ORDER BY observed_at DESC, id DESC LIMIT ?",
                (agent_id, limit),
            )
        ]
        for row in rows:
            row["tools"] = set(loads(row["tools"]))
            row["observed_at"] = parse(row["observed_at"])
        return list(reversed(rows))
