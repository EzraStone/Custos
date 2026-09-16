"""Scoring and disposition.

Deterministic, explainable, and boring by design. No model sits in this path.
Given the same telemetry the engine returns the same verdict, and every verdict
carries the arithmetic that produced it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from .episodes import PrincipalTelemetry
from .features import Features, extract
from .signals import BIAS, SIGNALS, Firing


class Disposition(StrEnum):
    """What the register is permitted to do with a verdict."""

    AGENT = "agent"
    """High confidence. Becomes a `discovered` register entry — which still
    confers no authority whatsoever (SEC-17)."""

    REVIEW = "review"
    """Ambiguous. Surfaced to an operator as a candidate and never written to
    the register as an agent. This band is not a hedge; it is where the
    workloads that merely resemble agents are supposed to land."""

    NOT_AGENT = "not_agent"
    """Below the floor. Recorded for the next scan's baseline, not reported."""


AGENT_THRESHOLD = 0.80
"""Above this a workload is reported as an agent.

Measured against the A0 corpus: every agent scores at or above 0.96 and the
highest negative sits at 0.48. The threshold is in that gap and there is a lot
of it — `a0/tests/test_g0.py` holds these figures against the corpus so the
sentence cannot go stale the way its predecessor did."""

REVIEW_THRESHOLD = 0.40
"""Above this a workload is offered to a human rather than dismissed.

This one is not in empty space and saying so is the point. The nearest
workload sits 0.010 away — a CI pipeline making bursts of model calls, which
is genuinely undecidable and would be undecidable a hundredth either side.

It was 0.030 before `offhours_activity` was removed, which is not empty space
either. The sweep prints the clearance (`make experiment`, "review gap") so
the number is visible rather than implied by a comment, and nothing moves the
threshold to improve it: placing a threshold to put a particular workload on a
particular side is fitting to eleven workloads."""


@dataclass(slots=True)
class Verdict:
    principal: str
    confidence: float
    disposition: Disposition
    features: Features
    firings: list[Firing] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    """Signals that could not be evaluated, usually because the customer did
    not provide load balancer logs. Reported, never silently treated as zero."""

    @property
    def evidence(self) -> list[str]:
        """The sentences behind this verdict, strongest contribution first."""
        return [
            f.evidence
            for f in sorted(self.firings, key=lambda f: f.contribution, reverse=True)
            if f.contribution > 0.05
        ]

    @property
    def degraded(self) -> bool:
        return bool(self.unavailable)


def score(features: Features) -> tuple[float, list[Firing], list[str]]:
    """Return (confidence, firings, unavailable signal ids)."""
    firings: list[Firing] = []
    unavailable: list[str] = []

    for signal in SIGNALS:
        if not signal.available(features):
            # An unevaluable signal contributes nothing. Treating "we cannot
            # see inbound requests" as "there were no inbound requests" would
            # make every chatbot look decoupled and destroy precision.
            unavailable.append(signal.id)
            continue
        activation = max(0.0, min(1.0, signal.activate(features)))
        firings.append(
            Firing(
                id=signal.id,
                activation=activation,
                weight=signal.weight,
                evidence=signal.describe(features),
            )
        )

    total = BIAS + sum(f.contribution for f in firings)
    return 1.0 / (1.0 + math.exp(-total)), firings, unavailable


def disposition_for(confidence: float) -> Disposition:
    if confidence >= AGENT_THRESHOLD:
        return Disposition.AGENT
    if confidence >= REVIEW_THRESHOLD:
        return Disposition.REVIEW
    return Disposition.NOT_AGENT


def classify_principal(t: PrincipalTelemetry) -> Verdict:
    features = extract(t)
    confidence, firings, unavailable = score(features)
    return Verdict(
        principal=t.principal,
        confidence=confidence,
        disposition=disposition_for(confidence),
        features=features,
        firings=firings,
        unavailable=unavailable,
    )


def classify_all(telemetry: list[PrincipalTelemetry]) -> list[Verdict]:
    return sorted(
        (classify_principal(t) for t in telemetry),
        key=lambda v: v.confidence,
        reverse=True,
    )
