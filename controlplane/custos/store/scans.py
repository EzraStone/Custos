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
    scope_named: int = 0
    scope_total: int = 0
    missing_fields: tuple[str, ...] = ()
    direction_undecided: int = 0
    read_errors: int = 0
    regions: tuple[str, ...] = ()
    bulk_senders: int = 0
    """Internal destinations that had a gateway's traffic shape and were not
    asked about, because the workloads reaching them reach nothing else.

    A judgement, and one that can be wrong. A scan that quietly declined to
    ask about five destinations is not the same as a scan that found none, and
    the reason the question mechanism exists at all is that a report with
    nothing in it is what a hidden gateway produces."""

    @property
    def scope_readable(self) -> float:
        """How much of the approval scope was a name rather than an address.

        Zero destinations is 1.0, not 0.0. A scan that reached nothing internal
        has no unreadable scope, and reporting it as fully unreadable would put
        a warning on an account with nothing to warn about — the same mistake
        as reporting an empty read as full coverage, in the other direction.
        """
        if self.scope_total <= 0:
            return 1.0
        return self.scope_named / self.scope_total


def _scan(row: sqlite3.Row) -> ScanRecord:
    """One scans row as a record.

    Written once because a field added to ScanRecord has to be read in every
    place a scan is loaded. Three copies of this expression is three chances
    to update two of them, and the one that got missed would return a scan
    with an empty region — which the report prints as a coverage claim.
    """
    return ScanRecord(
        id=row["id"], batch_id=row["batch_id"], account_id=row["account_id"],
        started_at=parse(row["started_at"]),
        principals_seen=row["principals_seen"], agents_found=row["agents_found"],
        review_candidates=row["review_candidates"],
        coverage=row["coverage"], truncated=bool(row["truncated"]),
        scope_named=row["scope_named"], scope_total=row["scope_total"],
        missing_fields=tuple(loads(row["missing_fields"])),
        direction_undecided=row["direction_undecided"],
        read_errors=row["read_errors"],
        regions=tuple(loads(row["regions"])),
        bulk_senders=row["bulk_senders"],
    )


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

        Idempotent on (account, region, window). The collector retries with
        bounded backoff, so the same window genuinely does arrive twice, and
        counting it twice would inflate every byte total downstream — which
        means every spend estimate and every egress ratio the classifier reads.

        Region is in the key because a collector covers one region: three
        regions of one account ship three windows for the same hour, and they
        are three collections rather than one retried three times.
        """
        existing = self.conn.execute(
            "SELECT id FROM batches WHERE account_id = ? AND region = ? "
            "AND window_start = ? AND window_end = ?",
            (account_id, region, iso(window_start), iso(window_end)),
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
        scope_named: int = 0,
        scope_total: int = 0,
        missing_fields: tuple[str, ...] = (),
        direction_undecided: int = 0,
        read_errors: int = 0,
        regions: tuple[str, ...] = (),
        bulk_senders: int = 0,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO scans (batch_id, account_id, started_at, principals_seen, "
            "agents_found, review_candidates, coverage, truncated, catalogue_revision, "
            "scope_named, scope_total, missing_fields, direction_undecided, "
            "read_errors, regions, bulk_senders) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_id, account_id, iso(started_at), principals_seen, agents_found,
             review_candidates, coverage, int(truncated), catalogue_revision,
             scope_named, scope_total, dumps(list(missing_fields)),
             direction_undecided, read_errors, dumps(list(regions)),
             bulk_senders),
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
        region: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO observations (scan_id, agent_id, observed_at, confidence, "
            "model_egress, model_ingress, episodes, calls_per_hour, tools, "
            "active_hours, blast_radius, region) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scan_id, agent_id, iso(observed_at), confidence, model_egress,
             model_ingress, episodes, calls_per_hour, dumps(tools),
             dumps({str(k): v for k, v in active_hours.items()}), blast_radius,
             region),
        )

    def scans_for(self, account_id: str, limit: int = 20) -> list[ScanRecord]:
        return [
            _scan(row)
            for row in self.conn.execute(
                "SELECT * FROM scans WHERE account_id = ? ORDER BY started_at DESC, id DESC "
                "LIMIT ?",
                (account_id, limit),
            )
        ]

    def regions_scanned(self, account_id: str) -> list[str]:
        """Every region this account has ever had a batch from.

        From batches rather than from the latest scan, because coverage is a
        question about the account and not about the most recent hour. An
        account collected in three regions that only shipped one this hour is
        still an account we cover in three.
        """
        return [
            row["region"]
            for row in self.conn.execute(
                "SELECT DISTINCT region FROM batches WHERE account_id = ? "
                "AND region != '' ORDER BY region",
                (account_id,),
            )
        ]

    def latest_scan_per_region(self, account_id: str) -> list[ScanRecord]:
        """The most recent scan of each region this account is collected in.

        The served report shows the whole register, and the register spans
        every region. Anything the report says about how the telemetry was
        read — which fields the flow log carried, how much of it parsed — is
        a statement about all of them, and the latest scan alone is one
        region's answer to a question asked about several.

        Ordered by region so the answer is stable between calls.
        """
        rows = self.conn.execute(
            "SELECT s.* FROM scans s JOIN batches b ON b.id = s.batch_id "
            "WHERE s.account_id = ? AND s.id = ("
            "  SELECT s2.id FROM scans s2 JOIN batches b2 ON b2.id = s2.batch_id "
            "  WHERE s2.account_id = s.account_id AND b2.region = b.region "
            "  ORDER BY s2.started_at DESC, s2.id DESC LIMIT 1"
            ") ORDER BY b.region",
            (account_id,),
        )
        return [_scan(row) for row in rows]

    def latest_scan(self, account_id: str) -> ScanRecord | None:
        scans = self.scans_for(account_id, limit=1)
        return scans[0] if scans else None

    def latest_scan_before(
        self, account_id: str, scan_id: int, region: str | None = None
    ) -> ScanRecord | None:
        """The most recent scan preceding `scan_id`.

        Used to pick the comparison baseline during ingestion, where the
        current scan row already exists. Taking `latest_scan` there would
        compare a scan against itself and report that nothing ever changes.

        `region` restricts it to the same region's previous scan, which is the
        only comparison that means anything. A scan of eu-west-1 compared
        against the last scan of us-east-1 reports every workload in one as
        having appeared and every workload in the other as having disappeared,
        every time either is collected.
        """
        where, params = "s.account_id = ? AND s.id < ?", [account_id, scan_id]
        if region is not None:
            where += " AND b.region = ?"
            params.append(region)
        row = self.conn.execute(
            f"SELECT s.* FROM scans s JOIN batches b ON b.id = s.batch_id "
            f"WHERE {where} ORDER BY s.started_at DESC, s.id DESC LIMIT 1",
            tuple(params),
        ).fetchone()
        if row is None:
            return None
        return _scan(row)

    def previous_in_same_region(self, account_id: str, scan_id: int) -> ScanRecord | None:
        """The scan before `scan_id` that covered the same region.

        A named method rather than a region argument at the call site, because
        the caller that needs this has a scan and not a region — and reaching
        for the region through the scan record is how the served report ended
        up describing an account with one region's figures.
        """
        region = self.conn.execute(
            "SELECT b.region AS region FROM scans s JOIN batches b ON b.id = s.batch_id "
            "WHERE s.id = ?",
            (scan_id,),
        ).fetchone()
        if region is None:
            return None
        return self.latest_scan_before(account_id, scan_id, region=region["region"])

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

    def observation_history(
        self, agent_id: str, limit: int = 30, region: str | None = None
    ) -> list[dict]:
        """Most recent observations for one agent, oldest first.

        Oldest first because every consumer is computing a trend, and reversing
        a list at each call site is how an off-by-one gets into a baseline.

        `region` narrows it to one region's history, which is what a baseline
        wants. An agent's behaviour in us-east-1 is a trend; the same agent's
        observations in us-east-1 and eu-west-1 interleaved are two trends
        sampled alternately, and the difference between them reads as drift.
        """
        where, params = "agent_id = ?", [agent_id]
        if region is not None:
            where += " AND region = ?"
            params.append(region)
        rows = [
            dict(row)
            for row in self.conn.execute(
                f"SELECT * FROM observations WHERE {where} "
                "ORDER BY observed_at DESC, id DESC LIMIT ?",
                (*params, limit),
            )
        ]
        for row in rows:
            row["tools"] = set(loads(row["tools"]))
            row["observed_at"] = parse(row["observed_at"])
        return list(reversed(rows))


class ReviewStore:
    """Workloads the classifier was unsure about.

    Separate from AgentStore on purpose, and the separation is the point. A
    review candidate has no status, no imprimatur, and no way to become a
    register entry through this class — the only path into the register is a
    scan that classified something as an agent.

    Kept because only the count was kept, so an operator could see that three
    workloads were uncertain and could not see which three. That is the least
    useful possible amount of information about a maybe.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, scan_id: int, account_id: str, verdicts: list) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO review_candidates "
            "(scan_id, account_id, principal, confidence, evidence, unavailable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (scan_id, account_id, v.principal, v.confidence,
                 dumps(v.evidence), dumps(v.unavailable))
                for v in verdicts
            ],
        )

    def latest_for(self, account_id: str) -> list[dict]:
        """This account's most recent scan's review candidates."""
        row = self.conn.execute(
            "SELECT MAX(scan_id) AS scan_id FROM review_candidates WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None or row["scan_id"] is None:
            return []
        return self._for_scan(row["scan_id"])

    def _for_scan(self, scan_id: int) -> list[dict]:
        return [
            {
                "principal": r["principal"],
                "confidence": r["confidence"],
                "evidence": loads(r["evidence"]),
                "unavailable": loads(r["unavailable"]),
                "scan_id": r["scan_id"],
            }
            for r in self.conn.execute(
                "SELECT * FROM review_candidates WHERE scan_id = ? "
                "ORDER BY confidence DESC, principal",
                (scan_id,),
            )
        ]

    def recurrence(self, account_id: str, principal: str) -> int:
        """How many of this account's scans put this workload in the review band.

        A workload uncertain once is a workload the classifier was unsure about
        on one window. One uncertain in every scan for a month is a different
        thing entirely, and without this an operator cannot tell them apart.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM review_candidates "
            "WHERE account_id = ? AND principal = ?",
            (account_id, principal),
        ).fetchone()
        return int(row["n"]) if row else 0
