"""The register: the canonical inventory, and the transitions it permits.

SEC-17 lives here. Discovery populates this store; it never authorises anything
in it. There is exactly one function in the codebase that can move a record to
SANCTIONED, it requires an operator identity, and it is not reachable from the
classifier.

Without that, traffic that imitates the agent signature would auto-provision
itself a credential — the register would become a self-service credential
issuer for anything capable of producing a burst of model calls.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from .model import Agent, Imprimatur, Source, Status


class TransitionError(RuntimeError):
    """An attempt to move a record along an edge the state machine forbids."""


# The only legal transitions. Everything absent from this table is refused.
_ALLOWED: dict[Status, frozenset[Status]] = {
    Status.DISCOVERED: frozenset({Status.PENDING_REVIEW, Status.SANCTIONED, Status.RETIRED}),
    Status.PENDING_REVIEW: frozenset({Status.SANCTIONED, Status.RETIRED, Status.DISCOVERED}),
    Status.SANCTIONED: frozenset({Status.RETIRED, Status.PENDING_REVIEW}),
    Status.RETIRED: frozenset({Status.PENDING_REVIEW}),
}


def agent_id(account_id: str, principal: str) -> str:
    """Stable identifier, so re-scans update records rather than duplicating."""
    digest = hashlib.sha256(f"{account_id}\x00{principal}".encode()).hexdigest()
    return f"agt_{digest[:20]}"


@dataclass(slots=True)
class AuditEntry:
    at: datetime
    agent_id: str
    actor: str
    action: str
    detail: str = ""


@dataclass(slots=True)
class Register:
    """In-memory register.

    A2 replaces this with a persistent store. The interface is deliberately
    small so that swap does not become an excuse to relax the state machine.
    """

    agents: dict[str, Agent] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)

    def upsert(self, agent: Agent) -> Agent:
        """Insert a record, or refresh an existing one from a new scan.

        A re-scan updates observation — last seen, reach, model use, evidence —
        and never touches status or imprimatur. A sanctioned agent stays
        sanctioned across scans; a discovered one is not promoted by being seen
        again, however many times.
        """
        existing = self.agents.get(agent.id)
        if existing is None:
            self.agents[agent.id] = agent
            self.audit.append(
                AuditEntry(agent.first_seen, agent.id, "system", "discovered",
                           agent.identity.principal)
            )
            return agent

        existing.last_seen = max(existing.last_seen, agent.last_seen)
        existing.first_seen = min(existing.first_seen, agent.first_seen)
        existing.model = agent.model
        existing.reach = agent.reach
        existing.baseline = agent.baseline
        existing.provenance.confidence = agent.provenance.confidence
        existing.provenance.evidence = agent.provenance.evidence
        if not existing.identity.attributed and agent.identity.attributed:
            existing.identity = agent.identity
        return existing

    def transition(self, agent_id_: str, to: Status, actor: str, detail: str = "") -> Agent:
        agent = self.agents[agent_id_]
        if to is Status.SANCTIONED:
            raise TransitionError(
                "SANCTIONED is reachable only through grant_imprimatur(), which "
                "requires an operator identity and an explicit approval scope."
            )
        if to not in _ALLOWED[agent.status]:
            raise TransitionError(f"{agent.status} -> {to} is not a permitted transition")

        agent.status = to
        if to is Status.RETIRED:
            agent.imprimatur = None
        self.audit.append(
            AuditEntry(datetime.now(tz=agent.last_seen.tzinfo), agent_id_, actor, str(to), detail)
        )
        return agent

    def grant_imprimatur(
        self,
        agent_id_: str,
        operator: str,
        at: datetime,
        approved_tools: set[str] | None = None,
        approved_data: set[str] | None = None,
    ) -> Agent:
        """Sanction an agent. The only path to SANCTIONED in the system.

        `operator` is a human identity and is required. Approval scope defaults
        to what was observed, because an operator approving an agent is
        approving what it was seen doing — widening that scope is a separate,
        deliberate act.
        """
        if not operator.strip():
            raise TransitionError("granting imprimatur requires an operator identity")

        agent = self.agents[agent_id_]
        if agent.status is Status.RETIRED:
            raise TransitionError("a retired agent must be reinstated before sanctioning")

        agent.imprimatur = Imprimatur(
            granted_by=operator,
            granted_at=at,
            approved_tools=set(approved_tools if approved_tools is not None else agent.reach.tools),
            approved_data=set(
                approved_data if approved_data is not None else agent.reach.data_stores
            ),
        )
        agent.status = Status.SANCTIONED
        self.audit.append(
            AuditEntry(at, agent_id_, operator, "sanctioned",
                       f"tools={sorted(agent.imprimatur.approved_tools)}")
        )
        return agent

    @property
    def unsanctioned(self) -> list[Agent]:
        """The set that regenerates on every scan and renews the contract."""
        return sorted(
            (a for a in self.agents.values() if a.unsanctioned),
            key=lambda a: a.priority,
        )

    @property
    def attributed_findings(self) -> list[Agent]:
        return [a for a in self.unsanctioned if a.identity.attributed]

    @property
    def unattributed_findings(self) -> list[Agent]:
        """SEC-20: reported separately, never mixed into owned findings."""
        return [a for a in self.unsanctioned if not a.identity.attributed]

    @property
    def discovered_count(self) -> int:
        return sum(1 for a in self.agents.values() if a.provenance.source is Source.DISCOVERED)
