"""Sessionisation: flow records into windows, windows into episodes.

The unit of classification is not a call — aggregation made calls unobservable —
and it is not a whole principal either, because a principal's traffic over three
days averages away everything interesting. The unit is an **episode**: a run of
consecutive aggregation windows in which the principal was talking to a model.

An episode is the closest observable proxy for "one task the workload was
doing." An agent's episode is one trajectory. A chatbot's episode is whatever
requests happened to arrive close together. The difference between those two
things is what every feature downstream measures.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..catalog import DestinationClass, classify, is_tool_destination
from ..telemetry import SYN, Direction, FlowRecord, InboundRequest


@dataclass(slots=True)
class Window:
    """One aggregation interval for one principal."""

    start: datetime
    model_egress: int = 0
    model_ingress: int = 0
    tool_egress: int = 0
    tool_ingress: int = 0
    model_connections: int = 0
    """Distinct 5-tuples to model endpoints carrying a SYN in this window."""
    tool_classes: set[DestinationClass] = field(default_factory=set)
    tool_addresses: set[str] = field(default_factory=set)
    model_addresses: set[str] = field(default_factory=set)

    @property
    def has_model(self) -> bool:
        return self.model_egress > 0

    @property
    def has_tool(self) -> bool:
        return self.tool_egress > 0


@dataclass(slots=True)
class Episode:
    """A contiguous run of model-active windows."""

    windows: list[Window]

    @property
    def start(self) -> datetime:
        return self.windows[0].start

    @property
    def length(self) -> int:
        return len(self.windows)

    @property
    def model_egress(self) -> int:
        return sum(w.model_egress for w in self.windows)

    @property
    def model_ingress(self) -> int:
        return sum(w.model_ingress for w in self.windows)

    @property
    def tool_windows(self) -> int:
        return sum(1 for w in self.windows if w.has_tool)


@dataclass(slots=True)
class PrincipalTelemetry:
    """Everything observable about one principal over the capture window.

    `principal` and `enis` are carried for attribution and reporting. Feature
    extraction is forbidden from reading them — see `test_leakage.py` — because
    scoring on a role name called "-agent" would pass A0 for a reason that
    would not generalise and could be defeated by renaming a role.
    """

    principal: str
    enis: set[str] = field(default_factory=set)
    addresses: set[str] = field(default_factory=set)
    windows: list[Window] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    inbound: list[InboundRequest] = field(default_factory=list)
    interval: timedelta = timedelta(seconds=60)

    inbound_logs_available: bool = False
    """Whether the CAPTURE included load balancer access logs at all.

    This is a property of the capture, never of the principal. A principal with
    zero inbound requests in a capture that has ALB logs is the strongest
    positive evidence in the system — the workload decided on its own to call a
    model. A principal with zero inbound requests in a capture that has no ALB
    logs tells us nothing. Conflating the two treats every chatbot as decoupled
    and destroys precision."""

    @property
    def model_windows(self) -> list[Window]:
        return [w for w in self.windows if w.has_model]


def _floor(t: datetime, origin: datetime, interval: timedelta) -> datetime:
    return origin + ((t - origin) // interval) * interval


def build_windows(
    records: list[FlowRecord], origin: datetime, interval: timedelta
) -> list[Window]:
    """Fold flow records into per-interval windows for a single principal."""
    windows: dict[datetime, Window] = {}
    seen_syn: set[tuple[datetime, int, str]] = set()

    for r in records:
        key = _floor(r.start, origin, interval)
        w = windows.get(key)
        if w is None:
            w = windows[key] = Window(start=key)

        egress = r.direction is Direction.EGRESS
        peer = r.dstaddr if egress else r.srcaddr
        port = r.dstport if egress else r.srcport
        cls = classify(peer, port, r.dst_aws_service)

        if cls is DestinationClass.MODEL:
            w.model_addresses.add(peer)
            if egress:
                w.model_egress += r.bytes
                if r.tcp_flags & SYN:
                    marker = (key, r.srcport, peer)
                    if marker not in seen_syn:
                        seen_syn.add(marker)
                        w.model_connections += 1
            else:
                w.model_ingress += r.bytes
        elif is_tool_destination(cls):
            w.tool_classes.add(cls)
            w.tool_addresses.add(peer)
            if egress:
                w.tool_egress += r.bytes
            else:
                w.tool_ingress += r.bytes

    return [windows[k] for k in sorted(windows)]


def build_episodes(
    windows: list[Window], interval: timedelta, gap_tolerance: int = 1
) -> list[Episode]:
    """Group model-active windows into episodes.

    `gap_tolerance` idle windows may sit inside an episode without splitting it.
    One is the right default at a 60s interval: a model call that straddles a
    window boundary, or a slow tool, should not shatter a single trajectory into
    fragments that then look like many short episodes.
    """
    active = [w for w in windows if w.has_model]
    if not active:
        return []

    episodes: list[Episode] = []
    current = [active[0]]
    for prev, w in zip(active, active[1:], strict=False):
        gap = int((w.start - prev.start) / interval) - 1
        if gap <= gap_tolerance:
            current.append(w)
        else:
            episodes.append(Episode(current))
            current = [w]
    episodes.append(Episode(current))
    return episodes


def sessionize(
    records: list[FlowRecord],
    principal_by_eni: dict[str, str],
    address_by_eni: dict[str, str],
    requests: dict[str, list[InboundRequest]],
    origin: datetime,
    interval: timedelta,
    gap_tolerance: int = 1,
    inbound_logs_available: bool = True,
) -> list[PrincipalTelemetry]:
    """Group a whole capture by principal and sessionise each one.

    `inbound_logs_available` states whether the capture included load balancer
    access logs. It must be passed from the capture's configuration, not
    inferred from whether any requests were seen.
    """
    by_principal: dict[str, list[FlowRecord]] = defaultdict(list)
    enis: dict[str, set[str]] = defaultdict(set)

    for r in records:
        principal = principal_by_eni.get(r.interface_id)
        if principal is None:
            # An ENI we could not attribute. SEC-20: never silently folded into
            # another principal's telemetry.
            continue
        by_principal[principal].append(r)
        enis[principal].add(r.interface_id)

    out: list[PrincipalTelemetry] = []
    for principal, recs in by_principal.items():
        addresses = {address_by_eni[e] for e in enis[principal] if e in address_by_eni}
        windows = build_windows(recs, origin, interval)
        inbound: list[InboundRequest] = []
        for addr in addresses:
            inbound.extend(requests.get(addr, []))
        out.append(
            PrincipalTelemetry(
                principal=principal,
                enis=enis[principal],
                addresses=addresses,
                windows=windows,
                episodes=build_episodes(windows, interval, gap_tolerance),
                inbound=sorted(inbound, key=lambda r: r.at),
                interval=interval,
                inbound_logs_available=inbound_logs_available,
            )
        )

    out.sort(key=lambda t: t.principal)
    return out
