"""Signals: named, weighted, individually explainable.

A signal converts one feature into an activation in [0, 1] plus a sentence a
human can argue with. The engine sums weighted activations; nothing else in the
system is allowed to influence a verdict.

Weights were set by measuring the A0 corpus, not by intuition. The measurement
is in `docs/A0-FINDINGS.md` and reproducible with `custos-a0 experiment`. Two
features that the specification expected to carry the classifier were measured
and are deliberately NOT signals — see `REJECTED` at the bottom of this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from .features import Features


def logistic(x: float, midpoint: float, scale: float) -> float:
    return 1.0 / (1.0 + math.exp(-(x - midpoint) / scale))


@dataclass(frozen=True, slots=True)
class Signal:
    id: str
    weight: float
    describe: Callable[[Features], str]
    activate: Callable[[Features], float]
    available: Callable[[Features], bool] = lambda f: True


@dataclass(frozen=True, slots=True)
class Firing:
    """One signal's contribution to one verdict."""

    id: str
    activation: float
    weight: float
    evidence: str

    @property
    def contribution(self) -> float:
        return self.activation * self.weight


EGRESS_RATIO_MIDPOINT = 7.0
"""Measured: every agent in the A0 corpus sits above 7.9, every non-agent below
6.5. The midpoint is set between them, and the narrowness of that gap is why
this signal is weighted alongside others rather than used as a threshold."""

EGRESS_RATIO_SCALE = 3.0


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


SIGNALS: tuple[Signal, ...] = (
    Signal(
        id="egress_asymmetry",
        weight=2.4,
        activate=lambda f: logistic(f.egress_ratio, EGRESS_RATIO_MIDPOINT, EGRESS_RATIO_SCALE),
        describe=lambda f: (
            f"Sent {_fmt(f.total_model_egress)} to model endpoints and received "
            f"{_fmt(f.total_model_ingress)} back, a ratio of {f.egress_ratio:.1f}:1. "
            "An agent resends its accumulated transcript on every step; a chatbot "
            "sends one prompt per answer."
        ),
    ),
    Signal(
        id="inbound_decoupling",
        weight=2.6,
        available=lambda f: f.have_inbound_logs,
        activate=lambda f: 1.0 - f.inbound_coupling,
        describe=lambda f: (
            f"{(1 - f.inbound_coupling) * 100:.0f}% of the intervals containing model "
            "traffic had no request arriving at the load balancer. This workload "
            "decides on its own to call a model."
        ),
    ),
    Signal(
        id="tool_interleave",
        weight=2.0,
        activate=lambda f: f.tool_interleave,
        describe=lambda f: (
            f"Model traffic was interleaved with calls to {f.distinct_tool_addresses} "
            f"internal destinations in {f.tool_interleave * 100:.0f}% of active "
            "intervals — the shape of a tool-calling loop."
        ),
    ),
    Signal(
        id="mcp_fingerprint",
        weight=1.6,
        activate=lambda f: 1.0 if f.mcp_windows > 0 else 0.0,
        describe=lambda f: (
            f"Traffic to an MCP server in {f.mcp_windows} intervals."
            if f.mcp_windows
            else "No MCP traffic observed."
        ),
    ),
    Signal(
        id="offhours_activity",
        weight=0.3,
        activate=lambda f: f.offhours_egress_fraction,
        describe=lambda f: (
            f"{f.offhours_egress_fraction * 100:.0f}% of model traffic fell outside "
            "working hours."
        ),
    ),
)

BIAS = -3.2
"""Prior against calling something an agent. Most principals in an AWS account
are not agents, and a classifier that forgets that generates a report nobody
finishes reading."""


REJECTED: dict[str, str] = {
    "context_growth": (
        "The specification's 'monotonically growing payload size'. Measured on the "
        "A0 corpus and rejected for two independent reasons. It does not survive "
        "600-second aggregation at all (every workload measures ~1.0). And where it "
        "is measurable, at 60 seconds, it fires HARDEST on the hardest negative: the "
        "multi-turn chatbot scores 5.67, above every genuine agent. A multi-turn "
        "conversation accumulates context exactly the way an agent trajectory does. "
        "Using this signal would have cost precision on the workload type customers "
        "have most of."
    ),
    "episode_persistence": (
        "Length of a run of consecutive model-active intervals. At 600-second "
        "aggregation it degenerates into a proxy for call volume — the embedding "
        "service measures 432 windows, longer than every agent — and volume is "
        "explicitly not evidence. Dropped rather than weighted low, because a signal "
        "that inverts under a configuration the customer chooses is worse than no "
        "signal."
    ),
    "call_burst_timing": (
        "The specification's headline signal: sub-second gaps between sequential "
        "model calls. Not implementable. Flow logs aggregate per 5-tuple over 60 or "
        "600 seconds and HTTP clients reuse connections, so the corpus's busiest "
        "agent produces fewer than 0.5 flow records per model call. The individual "
        "calls do not exist in the data."
    ),
}
