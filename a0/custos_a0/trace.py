"""Ground truth: what a workload actually did.

Nothing downstream of this module may read a trace. Traces exist only to
synthesise observable telemetry and to score the classifier against known
labels. The classifier sees flow logs and access logs; it never sees this.

That separation is enforced by `tests/test_leakage.py`, which fails if the
classifier's input surface ever grows a field derived from ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .endpoints import Endpoint, EndpointClass


class Label(StrEnum):
    """Ground-truth answer for a workload."""

    AGENT = "agent"
    """Plans, calls tools, and re-enters a model with accumulated context,
    without a human reading each step."""

    NOT_AGENT = "not_agent"
    """Everything else: chatbot backends, batch model jobs, inference services."""


class CallKind(StrEnum):
    INBOUND = "inbound"
    """An external request arriving at the workload. Visible in ALB access
    logs, not in flow logs from the workload's own side."""

    MODEL = "model"
    """An outbound call to a model provider endpoint."""

    TOOL = "tool"
    """An outbound call to an internal API, MCP server, or datastore."""


@dataclass(slots=True)
class Call:
    """One observable interaction, carrying ground truth."""

    at: datetime
    kind: CallKind
    endpoint: Endpoint

    req_bytes: int
    """Application-layer request payload size, before TLS and HTTP framing.
    The wire model in `custos_a0.wire` adds that overhead."""

    resp_bytes: int

    request_id: str = ""
    """Which inbound request caused this call. Empty when nothing human
    triggered it. Ground truth only — inferring this correlation from timing is
    precisely what the experiment tests."""

    step: int = 0
    """Position within the episode, zero-indexed."""


@dataclass(slots=True)
class Workload:
    """One service running under one principal, with its ground-truth label."""

    name: str
    principal: str
    scenario: str
    label: Label
    compute: str
    note: str
    """What makes this workload interesting to classify. Carried into the
    evaluation report so that a misclassification is legible rather than a
    row in a confusion matrix."""

    src_ip: str = ""
    eni: str = ""
    subnet: str = ""
    calls: list[Call] = field(default_factory=list)

    def of_kind(self, kind: CallKind) -> list[Call]:
        return [c for c in self.calls if c.kind == kind]

    @property
    def model_calls(self) -> int:
        return sum(1 for c in self.calls if c.kind == CallKind.MODEL)

    @property
    def tool_calls(self) -> int:
        return sum(1 for c in self.calls if c.kind == CallKind.TOOL)

    @property
    def inbound_requests(self) -> int:
        return sum(1 for c in self.calls if c.kind == CallKind.INBOUND)

    @property
    def reach(self) -> set[Endpoint]:
        """Ground-truth reach: every non-model, non-ingress endpoint touched."""
        return {
            c.endpoint
            for c in self.calls
            if c.endpoint.cls not in (EndpointClass.MODEL, EndpointClass.INGRESS)
        }

    def sort(self) -> None:
        self.calls.sort(key=lambda c: c.at)


@dataclass(slots=True)
class Corpus:
    """A labelled set of workloads observed over a common window."""

    start: datetime
    end: datetime
    workloads: list[Workload] = field(default_factory=list)

    def counts(self) -> tuple[int, int]:
        agents = sum(1 for w in self.workloads if w.label is Label.AGENT)
        return agents, len(self.workloads) - agents

    def by_name(self, name: str) -> Workload:
        for w in self.workloads:
            if w.name == name:
                return w
        raise KeyError(name)
