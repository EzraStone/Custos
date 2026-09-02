"""Batch in, persisted register out.

This is the seam between the two halves of the system that were built
separately: `scan.run` classifies telemetry in memory and knows nothing about
storage, and the store knows nothing about classification. This module is the
only thing that knows both, which keeps the scanner testable without a database
and the store testable without a classifier.
"""

from __future__ import annotations

import ipaddress
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .attribute import PrincipalFacts
from .baseline import Drift, detect_from_history
from .batch import Batch
from .classify import Disposition
from .diff import ScanDiff, compare
from .reach import IamCapability
from .report import Coverage
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
    coverage: Coverage = field(default_factory=Coverage)
    """How much of the account this scan saw. Rendered above the findings."""
    diff: ScanDiff = field(default_factory=ScanDiff)
    """What changed since the previous scan. Empty on a first scan."""
    drift: list[Drift] = field(default_factory=list)
    """Departures from each agent's established baseline. Empty until an agent
    has enough history for a baseline to mean anything."""


# Enum construction is not free. Direction(value) runs the full enum lookup
# protocol, which profiled at a fifth of total ingestion time across 40,000
# records — for a two-way mapping that never changes. A dict lookup does the
# same job in a fraction of the time.
_DIRECTIONS: dict[str, Direction] = {str(d): d for d in Direction}


def _to_telemetry(batch: Batch) -> tuple[list[FlowRecord], dict[str, list[InboundRequest]]]:
    records = [
        FlowRecord(
            account_id=f.account_id, interface_id=f.interface_id,
            srcaddr=f.srcaddr, dstaddr=f.dstaddr, srcport=f.srcport,
            dstport=f.dstport, protocol=f.protocol, packets=f.packets,
            bytes=f.bytes, start=f.start, end=f.end, action=f.action,
            log_status=f.log_status, vpc_id=f.vpc_id, subnet_id=f.subnet_id,
            direction=_DIRECTIONS[f.direction],
            src_aws_service=f.src_aws_service, dst_aws_service=f.dst_aws_service,
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


def scope_readability(batch: Batch, scan_input: ScanInput) -> tuple[int, int]:
    """How many of the private destinations this batch reached could be named.

    Counted over what the flow logs actually saw rather than over the register,
    because it is a fact about the account's tagging and not about which
    workloads happened to be classified as agents this time.
    """
    peers: set[str] = set()
    for record in scan_input.records:
        peer = record.dstaddr if record.direction is Direction.EGRESS else record.srcaddr
        if _is_private(peer):
            peers.add(peer)
    named = sum(1 for p in peers if scan_input.destination_names.get(p))
    return named, len(peers)


def _is_private(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_private
    except ValueError:
        return False


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
        destination_names={
            d.address: d.name for d in batch.destinations if d.name
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


def _coverage(batch: Batch) -> Coverage:
    """Build the report's coverage summary from what the collector reported.

    A batch carrying no collection statistics gets the default, which renders
    no banner. Absent statistics mean unknown, not incomplete — an older
    collector that never reported them would otherwise put a red banner on
    every scan, and a warning that is always present is one nobody reads.
    """
    stats = batch.collection
    if stats.lines_read <= 0:
        return Coverage()
    return Coverage(
        parsed_fraction=stats.parsed_fraction,
        truncated=stats.truncated,
        skipped_records=stats.records_skipped,
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
        scan_input = to_scan_input(batch, interval)
        result = run_scan(scan_input)
        named, total = scope_readability(batch, scan_input)

        scan_id = scans.record_scan(
            batch_id=record.id, account_id=batch.account_id, started_at=stamp,
            principals_seen=result.principals_seen,
            agents_found=len(result.register.agents),
            review_candidates=len(result.review_candidates),
            coverage=batch.collection.parsed_fraction,
            truncated=batch.collection.truncated,
            catalogue_revision=result.catalogue_revision,
            # How readable this scan's approval scope is. A scan whose scope is
            # mostly addresses produces correct findings that nobody can act
            # on, and that is invisible unless it is counted.
            scope_named=named,
            scope_total=total,
        )

        # Captured before this scan's observations are written, so the
        # comparison is against the previous scan rather than against itself.
        previous = scans.latest_scan_before(batch.account_id, scan_id)
        previous_obs = (
            scans.observations_for_scan(previous.id) if previous is not None else {}
        )

        window_hours = max(
            (batch.window_end - batch.window_start).total_seconds() / 3600, 1e-9
        )
        telemetry = {t.principal: t for t in result.telemetry}
        current_obs: dict[str, dict] = {}
        stored_agents = {}

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

            observation = {
                "blast_radius": str(agent.reach.blast_radius),
                "tools": set(agent.reach.tools) | set(agent.reach.data_stores),
                "calls_per_hour": len(t.model_windows) / window_hours,
                "active_hours": active_hours,
                "observed_at": batch.window_end,
            }
            current_obs[stored.id] = observation
            stored_agents[stored.id] = stored

            scans.record_observation(
                scan_id=scan_id, agent_id=stored.id, observed_at=batch.window_end,
                confidence=agent.provenance.confidence,
                model_egress=egress, model_ingress=ingress,
                episodes=len(t.episodes),
                calls_per_hour=observation["calls_per_hour"],
                tools=observation["tools"],
                active_hours=active_hours,
                blast_radius=observation["blast_radius"],
            )

        diff = compare(
            stored_agents, current_obs, previous_obs,
            previous_scan_id=previous.id if previous else None,
            current_scan_id=scan_id,
        )

        # Drift needs history, so it runs after this scan's observations are
        # written — detect_from_history excludes the latest row from the
        # baseline it builds.
        drift: list[Drift] = []
        for agent_id in current_obs:
            _, findings = detect_from_history(
                agent_id, scans.observation_history(agent_id)
            )
            drift.extend(findings)
        drift.sort(key=lambda d: d.severity)

    return IngestResult(
        batch=record, scan_id=scan_id, result=result,
        coverage_note=_coverage_note(batch, result),
        coverage=_coverage(batch),
        diff=diff, drift=drift,
    )


def review_principals(result: ScanResult) -> list[str]:
    """Principals the classifier could not decide, for operator review (SEC-17)."""
    return [
        v.principal for v in result.verdicts if v.disposition is Disposition.REVIEW
    ]
