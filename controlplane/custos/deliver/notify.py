"""Turning a scan into deliveries.

The orchestration: build findings from what a scan produced, drop what has
already been said, send what remains, and record only what actually landed.

Ordering matters and is not obvious. Suppression runs before sending so a
channel outage does not consume a finding's one delivery. Recording runs after
sending so a finding that failed to send is still deliverable next scan. Get
either backwards and the failure is silent — a finding that was never delivered
and never will be.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from ..logging import event, get
from ..pipeline import IngestResult
from .channel import Channel, Delivery
from .finding import Finding, Severity, from_change, from_drift, from_first_scan
from .suppress import Suppressor

log = get("custos.notify")


@dataclass(slots=True)
class NotifyResult:
    findings: list[Finding] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)
    suppressed: int = 0

    @property
    def ok(self) -> bool:
        return all(d.ok for d in self.deliveries)

    @property
    def urgent(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ACT_NOW]


def build_findings(outcome: IngestResult, account_id: str, at: datetime) -> list[Finding]:
    """Everything a scan produced that someone should act on.

    A first scan produces one summary. Every scan after produces findings only
    for what changed, because repeating the inventory weekly is what turns a
    channel into wallpaper — the report already holds the full list for anyone
    who wants it.
    """
    if outcome.diff.previous_scan_id is None:
        return from_first_scan(
            outcome.result.register.unsanctioned, account_id, at
        )

    findings = [from_change(c, account_id, at) for c in outcome.diff.actionable]
    agents = outcome.result.register.agents
    findings.extend(
        from_drift(d, agents.get(d.agent_id), account_id) for d in outcome.drift
    )

    findings.sort(key=lambda f: (f.severity.rank, f.principal))
    return findings


def notify(
    conn: sqlite3.Connection,
    outcome: IngestResult,
    account_id: str,
    channels: list[Channel],
    at: datetime,
) -> NotifyResult:
    """Deliver a scan's findings across every configured channel."""
    result = NotifyResult(findings=build_findings(outcome, account_id, at))
    if not channels:
        return result

    suppressor = Suppressor(conn)

    for channel in channels:
        deliverable, suppressed = suppressor.filter(result.findings, channel.name, at)
        result.suppressed += suppressed

        if not deliverable:
            result.deliveries.append(Delivery(channel=channel.name, sent=0,
                                              suppressed=suppressed))
            continue

        delivery = channel.send(deliverable, at)
        result.deliveries.append(
            Delivery(channel=delivery.channel, sent=delivery.sent,
                     suppressed=suppressed, error=delivery.error)
        )

        # Only what landed. A finding that failed to send stays deliverable
        # next scan, which is the whole reason this is after the send.
        if delivery.ok:
            suppressor.record(deliverable, channel.name, at)

    event(
        log, "scan.notified",
        account_id=account_id,
        findings=len(result.findings),
        urgent=len(result.urgent),
        suppressed=result.suppressed,
        channels=len(channels),
        failures=sum(1 for d in result.deliveries if not d.ok),
    )
    return result
