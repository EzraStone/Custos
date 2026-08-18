"""Feature extraction from sessionised telemetry.

Every feature here is computable from aggregated flow logs plus, where the
customer provides them, load balancer access logs. Nothing here reads a
principal name, a hostname, or a payload byte.

Features are raw measurements, not scores. Turning them into a verdict is the
engine's job, and keeping the two separate is what makes a finding explainable
on a call: the engine can say "this fired because the ratio was 47" rather than
"this fired because the score was 0.83".
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .episodes import Episode, PrincipalTelemetry

BUSINESS_START, BUSINESS_END = 8, 19
"""UTC hours treated as working hours. Crude, and deliberately so — this feeds
the weakest signal in the set and refining it would imply a confidence the
signal does not have."""


@dataclass(frozen=True, slots=True)
class Features:
    """What is observable about one principal."""

    have_inbound_logs: bool
    """Whether the capture included load balancer access logs at all.

    A property of the capture, never of the principal. Deriving it from whether
    THIS principal had inbound requests inverts the signal: an agent has none,
    which is the evidence, not an absence of evidence. The engine degrades
    honestly when logs are missing rather than guessing."""

    model_windows: int
    total_model_egress: int
    total_model_ingress: int

    egress_ratio: float
    """Bytes sent to model endpoints per byte received.

    An agent resends its accumulated transcript on every step, so this climbs
    with episode length. A chatbot sends one prompt and gets one answer, so it
    sits near a constant. This is the single most robust feature, because it is
    a property of summed bytes and therefore untouched by aggregation."""

    inbound_coupling: float
    """Fraction of model-active windows that also saw an inbound request.

    Near 1.0 means every burst of model traffic was answering something a human
    asked for. Near 0.0 means the workload decided on its own to call a model.
    Meaningless when `have_inbound_logs` is False."""

    egress_per_inbound_request: float
    """Model egress bytes per inbound request. Distinguishes a workload that
    makes one model call per request from one that makes forty."""

    tool_interleave: float
    """Fraction of model-active windows that also reached a tool destination."""

    distinct_tool_addresses: int
    mcp_windows: int
    """Windows with traffic to a conventional MCP port. Near-deterministic
    evidence of tool use when present, and absent for most workloads."""

    median_episode_windows: float
    p90_episode_windows: float
    """Episode persistence. A long trajectory occupies many consecutive windows;
    request-driven traffic does not."""

    ratio_growth: float
    """How much the egress-to-ingress ratio climbs within an episode, measured
    as the last third against the first third.

    Above 1.0 means context is accumulating. Note that a multi-turn chatbot
    also accumulates context across a conversation, so this feature cannot
    separate on its own — it is only decisive in combination with coupling."""

    offhours_egress_fraction: float
    """Weakest feature in the set, kept because it is nearly free."""

    episodes: int


def _ratio(numerator: float, denominator: float, cap: float = 1e6) -> float:
    if denominator <= 0:
        return cap if numerator > 0 else 0.0
    return numerator / denominator


def _episode_ratio_growth(episode: Episode) -> float | None:
    """Egress:ingress in the last third of an episode over the first third."""
    n = episode.length
    if n < 3:
        return None
    third = max(1, n // 3)
    head, tail = episode.windows[:third], episode.windows[-third:]

    def r(windows) -> float:
        out = sum(w.model_egress for w in windows)
        inn = sum(w.model_ingress for w in windows)
        return _ratio(out, inn, cap=0.0)

    head_r, tail_r = r(head), r(tail)
    if head_r <= 0:
        return None
    return tail_r / head_r


def extract(t: PrincipalTelemetry) -> Features:
    """Compute features for one principal."""
    model_windows = t.model_windows
    n_model = len(model_windows)

    total_out = sum(w.model_egress for w in model_windows)
    total_in = sum(w.model_ingress for w in model_windows)

    have_logs = t.inbound_logs_available
    coupling = 0.0
    if have_logs and n_model:
        interval = t.interval
        request_windows = {
            r.at - ((r.at - t.windows[0].start) % interval) if t.windows else r.at
            for r in t.inbound
        }
        coupled = sum(1 for w in model_windows if w.start in request_windows)
        coupling = coupled / n_model

    growths = [
        g for g in (_episode_ratio_growth(e) for e in t.episodes) if g is not None
    ]

    lengths = [e.length for e in t.episodes] or [0]

    return Features(
        have_inbound_logs=have_logs,
        model_windows=n_model,
        total_model_egress=total_out,
        total_model_ingress=total_in,
        egress_ratio=_ratio(total_out, total_in),
        inbound_coupling=coupling,
        egress_per_inbound_request=_ratio(total_out, len(t.inbound)) if have_logs else 0.0,
        tool_interleave=(
            sum(1 for w in model_windows if w.has_tool) / n_model if n_model else 0.0
        ),
        distinct_tool_addresses=len({a for w in t.windows for a in w.tool_addresses}),
        mcp_windows=sum(1 for w in t.windows if any(c == "mcp" for c in w.tool_classes)),
        median_episode_windows=statistics.median(lengths),
        p90_episode_windows=(
            statistics.quantiles(lengths, n=10)[-1] if len(lengths) >= 10 else max(lengths)
        ),
        ratio_growth=statistics.median(growths) if growths else 1.0,
        offhours_egress_fraction=_ratio(
            sum(
                w.model_egress
                for w in model_windows
                if not (BUSINESS_START <= w.start.hour < BUSINESS_END)
            ),
            total_out,
            cap=0.0,
        ),
        episodes=len(t.episodes),
    )


__all__ = ["BUSINESS_END", "BUSINESS_START", "Features", "extract"]
