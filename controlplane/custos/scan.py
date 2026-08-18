"""One scan, end to end.

Capture in, register out. This is the A1 scanner: no database, no auth, no
multi-tenancy. Everything it needs arrives as arguments and everything it
produces is returned, which keeps it trivially testable and means A2's
persistent store slots in underneath without touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import reach as reach_mod
from . import spend
from .attribute import Attribution, PrincipalFacts, resolve
from .catalog import RANGES_REVISION
from .classify import Disposition, Verdict, classify_all, sessionize
from .classify.episodes import PrincipalTelemetry
from .reach import IamCapability
from .register.model import (
    Agent,
    Identity,
    ModelUse,
    Provenance,
    Source,
    Status,
)
from .register.store import Register, agent_id
from .spend import PRICES_REVISION
from .telemetry import FlowRecord, InboundRequest


@dataclass(slots=True)
class ScanInput:
    """Everything the collector shipped for one account, one window."""

    account_id: str
    start: datetime
    end: datetime
    records: list[FlowRecord]
    requests: dict[str, list[InboundRequest]] = field(default_factory=dict)
    principal_by_eni: dict[str, str] = field(default_factory=dict)
    address_by_eni: dict[str, str] = field(default_factory=dict)
    compute_by_eni: dict[str, str] = field(default_factory=dict)
    facts: dict[str, PrincipalFacts] = field(default_factory=dict)
    capabilities: dict[str, IamCapability] = field(default_factory=dict)
    interval: timedelta = timedelta(seconds=60)
    inbound_logs_available: bool = True

    @property
    def observed_days(self) -> float:
        return max((self.end - self.start).total_seconds() / 86400, 1e-9)


@dataclass(slots=True)
class ScanResult:
    register: Register
    verdicts: list[Verdict]
    telemetry: list[PrincipalTelemetry]
    catalogue_revision: str = RANGES_REVISION
    prices_revision: str = PRICES_REVISION
    principals_seen: int = 0

    @property
    def review_candidates(self) -> list[Verdict]:
        """SEC-17: surfaced to an operator, never written as agents."""
        return [v for v in self.verdicts if v.disposition is Disposition.REVIEW]

    @property
    def headline(self) -> str:
        agents = self.register.unsanctioned
        writers = [a for a in agents if a.reach.blast_radius.rank > 0]
        if not agents:
            return "No unsanctioned agents found."
        clause = f"{len(agents)} unsanctioned agent{'s' if len(agents) != 1 else ''}"
        if writers:
            clause += (
                f", {len(writers)} holding credentials that permit writes to "
                "production systems"
            )
        return clause + "."


def _compute_for(t: PrincipalTelemetry, inp: ScanInput) -> str:
    for eni in sorted(t.enis):
        if eni in inp.compute_by_eni:
            return inp.compute_by_eni[eni]
    return ""


def _model_use(t: PrincipalTelemetry, inp: ScanInput) -> ModelUse:
    endpoints = {a for w in t.windows for a in w.model_addresses}
    providers = {spend.provider_for(a) for a in endpoints}
    egress = sum(w.model_egress for w in t.windows)
    ingress = sum(w.model_ingress for w in t.windows)
    provider = next(iter(providers - {"unknown"}), "unknown")
    return ModelUse(
        providers=providers,
        endpoints=endpoints,
        est_monthly_spend_usd=spend.estimate_monthly_usd(
            egress, ingress, inp.observed_days, provider
        ),
    )


def _identity(principal: str, attribution: Attribution, inp: ScanInput, compute: str) -> Identity:
    return Identity(
        principal=principal,
        owner_team=attribution.team,
        owner_human=attribution.contact,
        compute=compute,
        account_id=inp.account_id,
    )


def run(inp: ScanInput, register: Register | None = None) -> ScanResult:
    """Classify every principal in a capture and fold the agents into a register.

    Only verdicts in the AGENT band become register entries. Review-band
    verdicts are returned for an operator to look at and are never written —
    SEC-17 is a property of this function as much as of the store.
    """
    reg = register if register is not None else Register()

    telemetry = sessionize(
        inp.records,
        inp.principal_by_eni,
        inp.address_by_eni,
        inp.requests,
        origin=inp.start,
        interval=inp.interval,
        inbound_logs_available=inp.inbound_logs_available,
    )
    verdicts = classify_all(telemetry)
    by_principal = {t.principal: t for t in telemetry}

    for verdict in verdicts:
        if verdict.disposition is not Disposition.AGENT:
            continue

        t = by_principal[verdict.principal]
        facts = inp.facts.get(
            verdict.principal, PrincipalFacts(principal=verdict.principal)
        )
        attribution = resolve(facts)
        capability = inp.capabilities.get(verdict.principal)
        reach_report = reach_mod.build(t, capability)
        compute = _compute_for(t, inp) or facts.compute

        reg.upsert(
            Agent(
                id=agent_id(inp.account_id, verdict.principal),
                first_seen=inp.start,
                last_seen=inp.end,
                status=Status.DISCOVERED,
                provenance=Provenance(
                    source=Source.DISCOVERED,
                    confidence=verdict.confidence,
                    observed_principal=verdict.principal,
                    evidence=verdict.evidence,
                ),
                identity=_identity(verdict.principal, attribution, inp, compute),
                model=_model_use(t, inp),
                reach=reach_report.reach,
            )
        )

    return ScanResult(
        register=reg,
        verdicts=verdicts,
        telemetry=telemetry,
        principals_seen=len(telemetry),
    )
