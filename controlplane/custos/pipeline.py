"""Batch in, persisted register out.

This is the seam between the two halves of the system that were built
separately: `scan.run` classifies telemetry in memory and knows nothing about
storage, and the store knows nothing about classification. This module is the
only thing that knows both, which keeps the scanner testable without a database
and the store testable without a classifier.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from .api.schema import Batch
from .attribute import PrincipalFacts
from .classify import Disposition
from .reach import IamCapability
from .scan import ScanInput, ScanResult
from .scan import run as run_scan
from .store.agents import AgentStore
from .store.db import now, transaction
from .store.scans import BatchRecord, ScanStore
from .telemetry import Direction, FlowRecord, InboundRequest

DEFAULT_INTERVAL = timedelta(seconds=60)
"""Flow log aggregation interval assumed when the batch does not say.

A0 established that the classifier is invariant between 60s and 600s, so
guessing wrong here costs nothing measurable — which is the only reason a
default is acceptable at all.
"""


@dataclass(slots=True)
class IngestResult:
    batch: BatchRecord
    scan_id: int
    result: ScanResult
    coverage_note: str = ""


def _to_telemetry(batch: Batch) -> tuple[list[FlowRecord], dict[str, list[InboundRequest]]]:
    records = [
        FlowRecord(
            account_id=f.account_id, interface_id=f.interface_id,
            srcaddr=f.srcaddr, dstaddr=f.dstaddr, srcport=f.srcport,
            dstport=f.dstport, protocol=f.protocol, packets=f.packets,
            bytes=f.bytes, start=f.start, end=f.end, action=f.action,
            log_status=f.log_status, vpc_id=f.vpc_id, subnet_id=f.subnet_id,
            direction=Direction(f.direction), dst_aws_service=f.dst_aws_service,
            tcp_flags=f.tcp_flags,
        )
        for f in batch.flows
    ]

    requests: dict[str, list[InboundRequest]] = {}
    for r in batch.requests:
        requests.setdefault(r.target, []).append(
            InboundRequest(at=r.at, target=r.target,
                           sent_bytes=r.sent_bytes, received_bytes=r.received_bytes)
        )
    return records, requests


def to_scan_input(batch: Batch, interval: timedelta = DEFAULT_INTERVAL) -> ScanInput:
    """Convert a shipped batch into scanner input."""
    records, requests = _to_telemetry(batch)

    return ScanInput(
        account_id=batch.account_id,
        start=batch.window_start,
        end=batch.window_end,
        records=records,
        requests=requests,
        principal_by_eni={
            a.interface_id: a.principal for a in batch.attachments if a.principal
        },
        address_by_eni={
            a.interface_id: a.address for a in batch.attachments if a.address
        },
        compute_by_eni={
            a.interface_id: a.compute for a in batch.attachments if a.compute
        },
        facts={
            p.principal: PrincipalFacts(
                principal=p.principal, account_id=p.account_id,
                iam_path=p.iam_path, compute=p.compute,
                role_tags=dict(p.role_tags), resource_tags=dict(p.resource_tags),
            )
            for p in batch.principals
        },
        capabilities={
            p.principal: IamCapability(
                principal=p.principal,
                actions=frozenset(p.actions),
                assumable_roles=frozenset(p.assumable_roles),
            )
            for p in batch.principals
        },
        interval=interval,
        inbound_logs_available=batch.have_alb_logs,
    )


def _coverage_note(batch: Batch, result: ScanResult) -> str:
    """A sentence about what this scan could not see, or empty.

    Rendered wherever the scan is reported. A scan that found nothing because it
    could not see is not the same as a clean account, and the difference is the
    entire meaning of the result.
    """
    notes = []
    if not batch.have_alb_logs:
        notes.append(
            "no load balancer access logs, so low-volume agents surface for "
            "review rather than as findings"
        )
    if not batch.attachments:
        notes.append("no interface attributions, so findings cannot name an owner")
    if not batch.principals:
        notes.append("no IAM facts, so blast radius could not be established")
    return "; ".join(notes)


def ingest(
    conn: sqlite3.Connection,
    batch: Batch,
    interval: timedelta = DEFAULT_INTERVAL,
    received_at: datetime | None = None,
) -> IngestResult:
    """Ingest one batch: classify it, fold agents into the register, persist.

    The whole thing runs in one transaction. A partial write would leave a scan
    row with no observations, which the drift detector would later read as
    agents that stopped being seen — alerts manufactured out of a crash.
    """
    agents = AgentStore(conn)
    scans = ScanStore(conn)
    stamp = received_at or now()

    with transaction(conn):
        record = scans.record_batch(
            account_id=batch.account_id, region=batch.region,
            window_start=batch.window_start, window_end=batch.window_end,
            collector=batch.collector_version, received_at=stamp,
            flow_records=len(batch.flows), requests=len(batch.requests),
            have_alb_logs=batch.have_alb_logs,
        )

        # Classification runs against the existing register so a re-scan
        # refreshes records rather than creating duplicates.
        result = run_scan(to_scan_input(batch, interval))

        scan_id = scans.record_scan(
            batch_id=record.id, account_id=batch.account_id, started_at=stamp,
            principals_seen=result.principals_seen,
            agents_found=len(result.register.agents),
            review_candidates=len(result.review_candidates),
            coverage=1.0 if batch.flows else 0.0,
            truncated=False,
            catalogue_revision=result.catalogue_revision,
        )

        window_hours = max(
            (batch.window_end - batch.window_start).total_seconds() / 3600, 1e-9
        )
        telemetry = {t.principal: t for t in result.telemetry}

        for agent in result.register.agents.values():
            stored = agents.upsert(agent)
            t = telemetry.get(agent.identity.principal)
            if t is None:
                continue

            egress = sum(w.model_egress for w in t.windows)
            ingress = sum(w.model_ingress for w in t.windows)
            active_hours: dict[int, float] = {}
            for w in t.model_windows:
                active_hours[w.start.hour] = active_hours.get(w.start.hour, 0.0) + 1.0

            scans.record_observation(
                scan_id=scan_id, agent_id=stored.id, observed_at=batch.window_end,
                confidence=agent.provenance.confidence,
                model_egress=egress, model_ingress=ingress,
                episodes=len(t.episodes),
                calls_per_hour=len(t.model_windows) / window_hours,
                tools=set(agent.reach.tools) | set(agent.reach.data_stores),
                active_hours=active_hours,
                blast_radius=str(agent.reach.blast_radius),
            )

    return IngestResult(
        batch=record, scan_id=scan_id, result=result,
        coverage_note=_coverage_note(batch, result),
    )


def review_principals(result: ScanResult) -> list[str]:
    """Principals the classifier could not decide, for operator review (SEC-17)."""
    return [
        v.principal for v in result.verdicts if v.disposition is Disposition.REVIEW
    ]
