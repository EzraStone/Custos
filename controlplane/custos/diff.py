"""What changed since the last scan.

The specification is explicit that the unsanctioned set regenerating on every
scan is what makes Custos a subscription rather than an audit engagement. That
is only true if the second scan says something the first did not.

A report that repeats last week's findings verbatim gets skimmed the second time
and deleted the third. A report that opens with "three agents appeared this
week, and one of them can now write to your billing tables" gets read every
time.

So this module answers one question: what is different, and which differences
would a platform lead act on. Ordering is by consequence, not by recency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .register.model import Agent, BlastRadius


class ChangeKind(StrEnum):
    APPEARED = "appeared"
    """First time this agent has been seen. The headline of every scan."""

    BLAST_RADIUS_INCREASED = "blast_radius_increased"
    """Its credential can now do more damage than it could. The single most
    actionable finding Custos produces: a specific permission change, with a
    named owner and an obvious remediation."""

    REACH_EXPANDED = "reach_expanded"
    """It started talking to something it had not talked to before."""

    DISAPPEARED = "disappeared"
    """Seen before, absent now. Usually decommissioned; occasionally an agent
    that moved somewhere we cannot see, which is worth knowing either way."""

    RETURNED = "returned"
    """Absent, then back. A decommissioned agent that came back is a different
    conversation from one that never left."""

    VOLUME_JUMPED = "volume_jumped"
    """Materially busier than it was. Not evidence of anything by itself, which
    is why it ranks below the rest."""

    UNCHANGED = "unchanged"


# Ordering by what a platform lead would act on first, not by recency.
_SEVERITY = {
    ChangeKind.BLAST_RADIUS_INCREASED: 0,
    ChangeKind.APPEARED: 1,
    ChangeKind.REACH_EXPANDED: 2,
    ChangeKind.RETURNED: 3,
    ChangeKind.DISAPPEARED: 4,
    ChangeKind.VOLUME_JUMPED: 5,
    ChangeKind.UNCHANGED: 6,
}

VOLUME_JUMP_FACTOR = 3.0
"""How much busier an agent must get before it is worth a line in the report.

Three times. Model workloads are naturally spiky — a batch of tickets, a
deployment, a Monday — and a lower threshold produces a change entry every week
for every agent, which trains the reader to skip the section.
"""


@dataclass(frozen=True, slots=True)
class Change:
    kind: ChangeKind
    agent_id: str
    principal: str
    detail: str
    owner_team: str = ""
    blast_radius: BlastRadius = BlastRadius.READ

    @property
    def severity(self) -> int:
        return _SEVERITY[self.kind]


@dataclass(slots=True)
class ScanDiff:
    changes: list[Change] = field(default_factory=list)
    previous_scan_id: int | None = None
    current_scan_id: int | None = None

    @property
    def actionable(self) -> list[Change]:
        return [c for c in self.changes if c.kind is not ChangeKind.UNCHANGED]

    @property
    def headline(self) -> str:
        """One sentence for the top of a report, or empty if nothing moved."""
        if self.previous_scan_id is None:
            return ""

        appeared = [c for c in self.changes if c.kind is ChangeKind.APPEARED]
        escalated = [c for c in self.changes if c.kind is ChangeKind.BLAST_RADIUS_INCREASED]

        parts = []
        if appeared:
            parts.append(
                f"{len(appeared)} new agent{'s' if len(appeared) != 1 else ''} since "
                "the last scan"
            )
        if escalated:
            parts.append(
                f"{len(escalated)} gained permissions that increase what "
                f"{'they' if len(escalated) != 1 else 'it'} could destroy"
            )
        if not parts:
            return "No change since the last scan."
        return "; ".join(parts).capitalize() + "."


def _short(principal: str) -> str:
    return principal.rsplit("/", 1)[-1]


def _change(agent: Agent, agent_id: str, kind: ChangeKind, detail: str) -> Change:
    """Build a change, carrying the owner and blast radius from the register.

    A module-level function rather than a closure inside the comparison loop.
    The closure form worked, because it was only ever called in the iteration
    that created it, but a function capturing loop variables by reference is a
    footgun that survives until someone defers a call.
    """
    return Change(
        kind=kind, agent_id=agent_id, principal=agent.identity.principal,
        detail=detail, owner_team=agent.identity.owner_team,
        blast_radius=agent.reach.blast_radius,
    )


def compare(
    current: dict[str, Agent],
    current_obs: dict[str, dict],
    previous_obs: dict[str, dict],
    previous_scan_id: int | None = None,
    current_scan_id: int | None = None,
) -> ScanDiff:
    """Compare two scans' observations.

    `current` supplies the register records so a change can name an owner and a
    blast radius; the observation dicts supply what was measured each time.
    """
    diff = ScanDiff(previous_scan_id=previous_scan_id, current_scan_id=current_scan_id)

    if previous_scan_id is None:
        # A first scan has nothing to compare against. Reporting every agent as
        # "new" would be technically true and useless — the report already
        # lists them.
        return diff

    for agent_id, agent in current.items():
        principal = agent.identity.principal
        seen_now = current_obs.get(agent_id)
        seen_before = previous_obs.get(agent_id)

        if seen_now is None and seen_before is not None:
            diff.changes.append(_change(
                agent, agent_id, ChangeKind.DISAPPEARED,
                f"{_short(principal)} was active in the last scan and is absent now",
            ))
            continue

        if seen_now is None:
            continue

        if seen_before is None:
            diff.changes.append(_change(
                agent, agent_id, ChangeKind.APPEARED,
                f"{_short(principal)} appeared for the first time, "
                f"{agent.reach.blast_radius} reach",
            ))
            continue

        before_radius = BlastRadius(seen_before.get("blast_radius", "read"))
        now_radius = BlastRadius(seen_now.get("blast_radius", "read"))
        if now_radius.rank > before_radius.rank:
            diff.changes.append(_change(
                agent, agent_id, ChangeKind.BLAST_RADIUS_INCREASED,
                f"{_short(principal)} went from {before_radius} to {now_radius}: "
                "its credential can now do more damage than it could",
            ))

        new_tools = set(seen_now.get("tools", set())) - set(seen_before.get("tools", set()))
        if new_tools:
            diff.changes.append(_change(
                agent, agent_id, ChangeKind.REACH_EXPANDED,
                f"{_short(principal)} started reaching "
                + ", ".join(sorted(new_tools)[:4]),
            ))

        before_rate = seen_before.get("calls_per_hour", 0.0)
        now_rate = seen_now.get("calls_per_hour", 0.0)
        if before_rate > 0 and now_rate > before_rate * VOLUME_JUMP_FACTOR:
            diff.changes.append(_change(
                agent, agent_id, ChangeKind.VOLUME_JUMPED,
                f"{_short(principal)} is {now_rate / before_rate:.1f}x busier than "
                "in the last scan",
            ))

    # Agents seen previously that are not in the register at all.
    for agent_id in set(previous_obs) - set(current_obs) - set(current):
        diff.changes.append(Change(
            kind=ChangeKind.DISAPPEARED, agent_id=agent_id, principal=agent_id,
            detail="an agent seen in the last scan no longer appears in the register",
        ))

    diff.changes.sort(key=lambda c: (c.severity, c.principal))
    return diff
