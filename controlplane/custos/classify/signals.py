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


def read_the_response(f: Features) -> bool:
    """Whether there is an inbound payload to compare the outbound one against.

    Taking the protocol out of a byte count can leave nothing behind. A
    principal that sent a great deal to a model endpoint and got back only
    acknowledgements — every response failed, or the inbound half of the window
    was lost — clamps to zero inbound payload, and a ratio against zero hits
    its cap at a million to one. That is the heaviest signal in the system
    firing at full strength on a conversation nobody could read, and the
    evidence sentence beside it would say "received 0.0B back, a ratio of
    1000000.0:1".

    The same defect as the empty-set one below, one layer down, and it arrived
    with the correction that produces the payload figures. Unavailable rather
    than zero for the same reason: "we could not read the response" and "the
    response was empty" are different claims.
    """
    return saw_model_traffic(f) and f.total_model_ingress > 0


def saw_model_traffic(f: Features) -> bool:
    """Whether there was any recognised model traffic to measure.

    Four of the five signals below are ratios and fractions over the intervals
    containing model traffic. With no such intervals every one of them
    evaluates over an empty set, and the results are not neutral — they are
    maximally incriminating. `1 - inbound_coupling` becomes 1.0 and the
    strongest signal in the system fires at full weight on a workload with no
    model traffic at all, describing itself as "100% of the intervals
    containing model traffic had no request arriving at the load balancer".
    There were no such intervals. The sentence is false, and it was reaching
    customer reports.

    So this is an availability predicate, not a zero. The distinction is
    already the rule for load balancer logs — "reported, never silently
    treated as zero" — and this is the same failure one level down.

    What it means in practice: a workload whose model calls go somewhere we do
    not recognise cannot be scored. That is the correct answer. Our entire
    evidence base is model traffic, and a workload we cannot see making model
    calls is not a low-confidence agent, it is an unanswered question — one
    `gateway.py` asks by name.
    """
    return f.model_windows > 0


EGRESS_RATIO_MIDPOINT = 11.5
"""Measured: every agent in the A0 corpus sits above 15.3, every non-agent
below 8.7. The midpoint is set between them.

It was 7.0, against 7.9 and 6.5, for as long as this feature was computed on
wire bytes. Those two figures were not describing the conversation — they
carried one acknowledgement per two outbound segments and a certificate chain
per connection, both of which scale with how a workload's client is configured
rather than with what it said. Taking the protocol out widened the gap between
the classes from 1.2x to 2x, and it is the same corpus and the same workloads.

That gap being narrow was the stated reason this signal is weighted alongside
others rather than used as a threshold. It is wider now and the reason still
holds: a corpus of eleven workloads does not justify a threshold, whatever the
gap looks like."""

EGRESS_RATIO_SCALE = 3.0
"""Unchanged. The logistic's width is about how much confidence a workload near
the midpoint should get, which is a judgement about evidence rather than a
measurement, and nothing this arc found bears on it."""


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
        available=read_the_response,
        activate=lambda f: logistic(f.egress_ratio, EGRESS_RATIO_MIDPOINT, EGRESS_RATIO_SCALE),
        describe=lambda f: (
            f"Sent {_fmt(f.total_model_egress)} of message payload to model "
            f"endpoints and received {_fmt(f.total_model_ingress)} back, a ratio "
            f"of {f.egress_ratio:.1f}:1. An agent resends its accumulated "
            "transcript on every step; a chatbot sends one prompt per answer."
        ),
    ),
    Signal(
        id="inbound_decoupling",
        weight=2.6,
        available=lambda f: f.have_inbound_logs and saw_model_traffic(f),
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
        available=saw_model_traffic,
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
    "offhours_activity": (
        "The fraction of model traffic outside working hours. Carried at weight 0.3 "
        "until an ablation measured it, and it was not merely inert: removing it "
        "widens the separation margin from 0.415 to 0.485 on the base corpus and "
        "from 0.291 to 0.356 on the stress corpus, with no verdict changing on "
        "either and recall and precision 1.00 throughout. It was making the "
        "classifier worse on every corpus there is.\n\n"
        "Rejected rather than reweighted because the reason is structural. A "
        "nightly reconciliation agent runs at 3am and so does a nightly batch "
        "summariser; running unattended is what both are for. Off-hours activity "
        "separates scheduled work from interactive work, which is not the question "
        "being asked — and the corpus has scheduled workloads on both sides "
        "precisely because that distinction is not the one that matters.\n\n"
        "It cost something to remove, and the cost is recorded rather than "
        "absorbed: every threshold clearance narrows, and the review threshold's "
        "drops from 0.030 to 0.010. That threshold was never in the empty space "
        "the comment above claims — 0.030 is not empty space either — and the "
        "sweep prints clearance now so it stops being invisible."
    ),
    "call_burst_timing": (
        "The specification's headline signal: sub-second gaps between sequential "
        "model calls. Not implementable. Flow logs aggregate per 5-tuple over 60 or "
        "600 seconds and HTTP clients reuse connections, so the corpus's busiest "
        "agent produces fewer than 0.5 flow records per model call. The individual "
        "calls do not exist in the data."
    ),
}
