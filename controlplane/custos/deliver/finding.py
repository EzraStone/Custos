"""What gets delivered, as opposed to what gets stored.

A register entry is a record. A finding is a claim someone should act on, and
the two are not the same set — most of the register is agents the customer
already knows about, sanctioned last month, unchanged since.

The distinction matters because the failure mode of a delivery channel is not
missing an alert. It is sending so many that the channel gets muted, after
which every alert is missed. So this module is mostly about what does *not*
become a finding.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ..baseline import Drift
from ..diff import Change, ChangeKind
from ..register.model import Agent, BlastRadius


class Severity(StrEnum):
    """How quickly a human should look.

    Three levels, because a scale with more than three gets argued about
    instead of acted on, and the argument is always about the middle.
    """

    ACT_NOW = "act_now"
    """A credential gained permissions that increase what it could destroy.
    Specific change, named owner, obvious remediation."""

    REVIEW = "review"
    """A new agent, or one that started reaching somewhere new. Worth a look
    this week."""

    NOTE = "note"
    """Context. Delivered in digests, never on its own."""

    @property
    def rank(self) -> int:
        return {"act_now": 0, "review": 1, "note": 2}[self.value]


@dataclass(frozen=True, slots=True)
class Finding:
    """One deliverable claim."""

    severity: Severity
    title: str
    detail: str
    account_id: str
    principal: str = ""
    owner_team: str = ""
    blast_radius: BlastRadius = BlastRadius.READ
    observed_at: datetime | None = None
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def fingerprint(self) -> str:
        """Stable identity for suppression.

        Deliberately excludes the timestamp and the detail text. The same
        escalation observed on three consecutive scans is one finding, and
        including anything that varies between scans would defeat suppression
        entirely — which is the same as having none.
        """
        material = f"{self.account_id}\x00{self.principal}\x00{self.severity}\x00{self.title}"
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    @property
    def owner(self) -> str:
        return self.owner_team or "unattributed"


_CHANGE_SEVERITY = {
    ChangeKind.BLAST_RADIUS_INCREASED: Severity.ACT_NOW,
    ChangeKind.APPEARED: Severity.REVIEW,
    ChangeKind.REACH_EXPANDED: Severity.REVIEW,
    ChangeKind.RETURNED: Severity.REVIEW,
    ChangeKind.DISAPPEARED: Severity.NOTE,
    ChangeKind.VOLUME_JUMPED: Severity.NOTE,
}

_TITLE = {
    ChangeKind.BLAST_RADIUS_INCREASED: "Credential can now do more damage",
    ChangeKind.APPEARED: "New agent discovered",
    ChangeKind.REACH_EXPANDED: "Agent reached something new",
    ChangeKind.RETURNED: "Agent returned after being absent",
    ChangeKind.DISAPPEARED: "Agent no longer active",
    ChangeKind.VOLUME_JUMPED: "Agent materially busier",
}


def from_change(change: Change, account_id: str, at: datetime | None = None) -> Finding:
    return Finding(
        severity=_CHANGE_SEVERITY.get(change.kind, Severity.NOTE),
        title=_TITLE.get(change.kind, "Change observed"),
        detail=change.detail,
        account_id=account_id,
        principal=change.principal,
        owner_team=change.owner_team,
        blast_radius=change.blast_radius,
        observed_at=at,
    )


def from_drift(drift: Drift, agent: Agent | None, account_id: str) -> Finding:
    """Drift is always REVIEW, never ACT_NOW.

    A departure from baseline is a question, not a conclusion. Paging someone
    at 02:00 for one would be claiming a certainty the data does not carry, and
    the first false alarm costs every subsequent alert its credibility.
    """
    return Finding(
        severity=Severity.REVIEW,
        title="Behaviour worth asking about",
        detail=drift.question,
        account_id=account_id,
        principal=agent.identity.principal if agent else drift.agent_id,
        owner_team=agent.identity.owner_team if agent else "",
        blast_radius=agent.reach.blast_radius if agent else BlastRadius.READ,
        observed_at=drift.observed_at,
    )


def from_first_scan(agents: list[Agent], account_id: str, at: datetime) -> list[Finding]:
    """The first scan delivers one summary, not one alert per agent.

    Forty separate messages on day one is how a channel gets muted before it
    has ever been useful. The report carries the detail; this carries the
    reason to open it.
    """
    if not agents:
        return []

    writers = [a for a in agents if a.reach.blast_radius.rank > 0]
    detail = f"{len(agents)} unsanctioned agent{'s' if len(agents) != 1 else ''}"
    if writers:
        detail += (
            f", {len(writers)} holding credentials that permit writes to "
            "production systems"
        )

    return [Finding(
        severity=Severity.ACT_NOW if writers else Severity.REVIEW,
        title="First scan complete",
        detail=detail + ".",
        account_id=account_id,
        observed_at=at,
        evidence=tuple(
            f"{a.identity.principal.rsplit('/', 1)[-1]} — {a.reach.blast_radius}, "
            f"owned by {a.identity.owner_team or 'nobody we could resolve'}"
            for a in agents[:5]
        ),
    )]
