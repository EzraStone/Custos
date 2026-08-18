"""The agent record.

The agent record is the product. Everything else in this repository is plumbing
that produces one, keeps one current, or acts on one.

The provenance fields are what let a single schema serve both halves of the
system: a record that arrived by discovery and a record that was entered by
hand are the same shape, differing only in `source` and `confidence`. The
Checkpoint reads the same table either way, which is what makes onboarding
"review this list" rather than "enumerate your forty agents".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Status(StrEnum):
    DISCOVERED = "discovered"
    """Observed by the classifier. Confers no authority of any kind."""

    PENDING_REVIEW = "pending_review"
    """An operator has picked it up but not decided."""

    SANCTIONED = "sanctioned"
    """An operator granted imprimatur. Only reachable through
    `grant_imprimatur`, and only with an operator identity."""

    RETIRED = "retired"
    """No longer running, or authority withdrawn."""


class Source(StrEnum):
    MANUAL = "manual"
    DISCOVERED = "discovered"
    IMPORTED = "imported"


class BlastRadius(StrEnum):
    """What the worst case looks like if this agent is wrong or compromised.

    This is the field that converts interest into a purchase order. "You have 34
    agents" is a curiosity. "Eleven are unsanctioned and one can write to your
    billing tables" is a budget line.
    """

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    @property
    def rank(self) -> int:
        return {"read": 0, "write": 1, "destructive": 2}[self.value]


@dataclass(slots=True)
class Provenance:
    source: Source
    confidence: float = 0.0
    """Classifier confidence. Zero for manually entered records.

    Note what this field cannot do: no value of it, including 1.0, moves a
    record to SANCTIONED. See SEC-17."""
    observed_principal: str = ""
    evidence: list[str] = field(default_factory=list)
    """The sentences the classifier produced. Carried on the record so the
    engineer who owns the workload can argue with the finding directly."""


@dataclass(slots=True)
class Identity:
    principal: str
    owner_team: str = ""
    owner_human: str = ""
    compute: str = ""
    account_id: str = ""

    @property
    def attributed(self) -> bool:
        """SEC-20: an unattributed finding is segregated, never mixed in."""
        return bool(self.owner_team or self.owner_human)


@dataclass(slots=True)
class ModelUse:
    providers: set[str] = field(default_factory=set)
    endpoints: set[str] = field(default_factory=set)
    est_monthly_spend_usd: float = 0.0


@dataclass(slots=True)
class Reach:
    """What this agent can touch. The field that sells the product."""

    credentials: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    data_stores: set[str] = field(default_factory=set)
    blast_radius: BlastRadius = BlastRadius.READ

    @property
    def surface(self) -> int:
        return len(self.tools) + len(self.data_stores)


@dataclass(slots=True)
class Imprimatur:
    """The grant. Null while unsanctioned."""

    granted_by: str
    granted_at: datetime
    approved_tools: set[str] = field(default_factory=set)
    approved_data: set[str] = field(default_factory=set)
    key_id: str = ""
    """Links to the Checkpoint credential. Empty until the Checkpoint is in
    scope for this customer."""


@dataclass(slots=True)
class Baseline:
    calls_per_hour_p50: float = 0.0
    calls_per_hour_p95: float = 0.0
    tool_set: set[str] = field(default_factory=set)
    active_hours: dict[int, float] = field(default_factory=dict)


@dataclass(slots=True)
class Agent:
    id: str
    first_seen: datetime
    last_seen: datetime
    status: Status
    provenance: Provenance
    identity: Identity
    model: ModelUse = field(default_factory=ModelUse)
    reach: Reach = field(default_factory=Reach)
    imprimatur: Imprimatur | None = None
    baseline: Baseline = field(default_factory=Baseline)

    @property
    def unsanctioned(self) -> bool:
        """The recurring value delivery.

        This set regenerates on every scan, which is what makes Custos a
        subscription rather than an audit engagement.
        """
        return self.status is not Status.SANCTIONED and self.status is not Status.RETIRED

    @property
    def priority(self) -> tuple[int, int, float]:
        """Sort key for the report: worst first.

        Blast radius dominates, then reach surface, then confidence. An
        unsanctioned agent that can write to production outranks a dozen
        read-only ones however confident the classifier is about them.
        """
        return (-self.reach.blast_radius.rank, -self.reach.surface, -self.provenance.confidence)
