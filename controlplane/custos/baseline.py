"""Per-agent baselines and departures from them.

This module is the only honest home for any claim Custos makes about detecting
a misbehaving or compromised agent, and the claim it supports is narrow:

    This agent is doing something it has not done before.

Not "this agent is compromised". Not "this action is unsafe". A departure from
baseline is a question worth putting to the person who owns the workload, and
the report says exactly that. Anything stronger would be a claim the data
cannot carry, and a security product that overstates once is not believed
again.

Two design decisions follow from that framing.

Baselines need history before they mean anything. An agent seen twice has no
baseline, it has two data points, and calling the second one a departure would
generate a finding out of nothing. `MIN_OBSERVATIONS` is the floor, and below it
this module returns no findings rather than weak ones.

The baseline excludes the observation being tested. Including it drags the
baseline toward the value being judged, which is how a slow drift becomes
invisible — each step looks normal against a baseline that already absorbed it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

MIN_OBSERVATIONS = 5
"""Scans required before a baseline is used.

Below this an agent has data points, not a pattern. Reporting a departure from
three observations would manufacture findings from ordinary variation, and the
first week of a deployment would be nothing but noise.
"""

VOLUME_SIGMA = 3.0
"""How far outside normal a call rate must fall.

Three standard deviations above the historical p95. Model workloads are spiky
enough that anything tighter fires constantly.
"""


class DriftKind(StrEnum):
    NEW_TOOL = "new_tool"
    """Reached something it has never reached. The strongest of these: an
    agent's tool set is stable in normal operation, and a new one is either a
    deployment nobody mentioned or something worth asking about."""

    VOLUME_SPIKE = "volume_spike"
    OFF_PATTERN_HOURS = "off_pattern_hours"
    """Active in hours it has never been active in."""

    ENDPOINT_CHANGE = "endpoint_change"
    """Started calling a different model provider."""


_SEVERITY = {
    DriftKind.NEW_TOOL: 0,
    DriftKind.ENDPOINT_CHANGE: 1,
    DriftKind.OFF_PATTERN_HOURS: 2,
    DriftKind.VOLUME_SPIKE: 3,
}


@dataclass(frozen=True, slots=True)
class Drift:
    kind: DriftKind
    agent_id: str
    observed_at: datetime
    detail: str

    @property
    def severity(self) -> int:
        return _SEVERITY[self.kind]

    @property
    def question(self) -> str:
        """The finding, phrased as what it actually is.

        Every drift finding is rendered this way. The phrasing is deliberate:
        it puts a question to the workload's owner rather than an accusation,
        because a question gets answered and an accusation gets argued with.
        """
        return f"{self.detail}. Is that expected?"


@dataclass(slots=True)
class Baseline:
    """What normal has looked like for one agent."""

    agent_id: str
    observations: int = 0
    calls_per_hour_p50: float = 0.0
    calls_per_hour_p95: float = 0.0
    calls_per_hour_stdev: float = 0.0
    tool_set: set[str] = field(default_factory=set)
    active_hours: set[int] = field(default_factory=set)

    @property
    def established(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(fraction * len(ordered)), len(ordered) - 1)
    return ordered[index]


def build(agent_id: str, history: list[dict]) -> Baseline:
    """Build a baseline from an agent's observation history, oldest first.

    `history` must exclude the observation being tested against it. Including
    it drags the baseline toward the value being judged, which is how a slow
    drift becomes invisible: every step looks normal against a baseline that
    has already absorbed it.
    """
    baseline = Baseline(agent_id=agent_id, observations=len(history))
    if not history:
        return baseline

    rates = [float(h.get("calls_per_hour", 0.0)) for h in history]
    baseline.calls_per_hour_p50 = _percentile(rates, 0.50)
    baseline.calls_per_hour_p95 = _percentile(rates, 0.95)
    baseline.calls_per_hour_stdev = statistics.stdev(rates) if len(rates) > 1 else 0.0

    for h in history:
        baseline.tool_set |= set(h.get("tools", set()))
        hours = h.get("active_hours") or {}
        if isinstance(hours, dict):
            baseline.active_hours |= {int(k) for k in hours}

    return baseline


def detect(baseline: Baseline, observation: dict, observed_at: datetime) -> list[Drift]:
    """Compare one observation against a baseline.

    Returns nothing when the baseline is not established. That is the correct
    outcome for a young agent, not a gap to be filled with weaker signals.
    """
    if not baseline.established:
        return []

    findings: list[Drift] = []

    new_tools = set(observation.get("tools", set())) - baseline.tool_set
    if new_tools:
        findings.append(Drift(
            kind=DriftKind.NEW_TOOL, agent_id=baseline.agent_id, observed_at=observed_at,
            detail=(
                f"reached {', '.join(sorted(new_tools)[:4])} for the first time in "
                f"{baseline.observations} scans"
            ),
        ))

    rate = float(observation.get("calls_per_hour", 0.0))
    ceiling = baseline.calls_per_hour_p95 + VOLUME_SIGMA * baseline.calls_per_hour_stdev
    if baseline.calls_per_hour_p95 > 0 and rate > ceiling:
        findings.append(Drift(
            kind=DriftKind.VOLUME_SPIKE, agent_id=baseline.agent_id,
            observed_at=observed_at,
            detail=(
                f"made {rate:.1f} calls per hour against a usual high of "
                f"{baseline.calls_per_hour_p95:.1f}"
            ),
        ))

    hours = observation.get("active_hours") or {}
    if isinstance(hours, dict) and baseline.active_hours:
        new_hours = {int(k) for k in hours} - baseline.active_hours
        if new_hours:
            listed = ", ".join(f"{h:02d}:00" for h in sorted(new_hours)[:4])
            findings.append(Drift(
                kind=DriftKind.OFF_PATTERN_HOURS, agent_id=baseline.agent_id,
                observed_at=observed_at,
                detail=f"was active at {listed} UTC, outside its usual hours",
            ))

    findings.sort(key=lambda d: d.severity)
    return findings


def detect_from_history(
    agent_id: str, history: list[dict], observed_at: datetime | None = None
) -> tuple[Baseline, list[Drift]]:
    """Build a baseline from all but the latest observation, then test it.

    The convenience wrapper most callers want, and the one that gets the
    exclusion right — leaving it to the caller is how the latest observation
    ends up inside its own baseline.
    """
    if not history:
        return Baseline(agent_id=agent_id), []

    *earlier, latest = history
    baseline = build(agent_id, earlier)
    at = observed_at or latest.get("observed_at")
    return baseline, detect(baseline, latest, at)
