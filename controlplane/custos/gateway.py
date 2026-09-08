"""Finding the model gateway a customer forgot to mention.

`declared.py` gives a customer a way to say "our model calls go through
10.0.7.0/24". This exists because most of them will not think to, and because
the question is unanswerable as usually asked. "Do you run a self-hosted model
gateway?" gets a confident no from the platform lead whose predecessor stood
one up, and there is no follow-up question that helps.

A list is answerable. "Forty per cent of what this workload sends goes to
10.0.7.9, it gets almost nothing back, and it never talks to a model provider
we recognise — is that your gateway?" is a question somebody can check in a
minute.

The shape being looked for is the shape of the whole product, aimed one level
down. An agent sends far more than it receives because it resends its
accumulated transcript; a workload behind a gateway does exactly that to an
internal address instead of to api.anthropic.com. The traffic is unchanged.
Only the destination's name is missing.

**This never classifies anything.** A candidate is a question put to a human,
and it is deliberately not wired to the classifier: a heuristic that promoted
an internal address to a model endpoint on its own would manufacture agents out
of any sufficiently chatty internal API, and the first false positive of that
kind costs more trust than every true one earns.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import DestinationClass, classify
from .classify.episodes import PrincipalTelemetry

MIN_RATIO = 3.0
"""Egress:ingress below which a destination is not model-shaped.

Deliberately far below the 7:1 the classifier looks for in a confirmed agent.
This is a question rather than a finding, and the cost of asking about an
ordinary internal API is a customer saying no."""

MIN_BYTES = 1_000_000
"""Below this a destination is not worth asking about. A megabyte of egress
over a whole window is a health check, not a model conversation."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """An internal destination that behaves like a model endpoint."""

    address: str
    egress: int
    ingress: int
    principals: tuple[str, ...]
    """Who talked to it. A gateway usually serves several workloads, and one
    that serves several is a much better question than one that serves one."""
    blind_principals: tuple[str, ...]
    """Of those, the ones with no recognised model traffic at all.

    The sharpest signal here. A workload that already talks to Anthropic is
    making tool calls to this address; a workload that talks to nothing we
    recognise, and sends this address a transcript-shaped stream, is either
    behind a gateway or is not an agent at all.
    """

    @classmethod
    def from_row(cls, row: dict) -> Candidate:
        """Rebuild a candidate the store wrote, so the functions below work on
        stored questions as well as freshly computed ones."""
        return cls(
            address=row["address"], egress=row["egress"], ingress=row["ingress"],
            principals=tuple(row["principals"]),
            blind_principals=tuple(row["blind_principals"]),
        )

    @property
    def ratio(self) -> float:
        return self.egress / max(self.ingress, 1)

    @property
    def question(self) -> str:
        """The candidate, phrased for the person who has to answer it."""
        who = (
            f"{len(self.blind_principals)} workloads that never reach a model "
            "provider we recognise"
            if len(self.blind_principals) != 1
            else "a workload that never reaches a model provider we recognise"
        )
        return (
            f"{self.address} received {self.egress / 1e6:.1f}MB from {who}, and "
            f"returned {self.ingress / 1e6:.1f}MB — a ratio of {self.ratio:.1f}:1. "
            "Is it a model gateway?"
        )


def candidates(telemetry: list[PrincipalTelemetry], limit: int = 5) -> list[Candidate]:
    """Internal destinations worth asking a customer about.

    Ordered by how many blind workloads reach them, then by volume. A gateway
    that three unexplained workloads talk to is a better question than one a
    single workload talks to, because the alternative explanation — that this
    one workload has an unusual internal API — gets weaker with each workload
    that shares it.
    """
    egress: dict[str, int] = {}
    ingress: dict[str, int] = {}
    reached_by: dict[str, set[str]] = {}
    blind_reached_by: dict[str, set[str]] = {}

    for t in telemetry:
        blind = not any(w.model_addresses for w in t.windows)
        for window in t.windows:
            for address, seen in window.tool_seen.items():
                # Only destinations the built-in catalogue calls an internal
                # API. A datastore is a datastore, an MCP server announces
                # itself on its own port, and anything already classified as a
                # model needs no question asking about it.
                if seen.cls is not DestinationClass.INTERNAL_API:
                    continue
                if classify(address, seen.port, seen.aws_service) is not (
                    DestinationClass.INTERNAL_API
                ):
                    continue
                reached_by.setdefault(address, set()).add(t.principal)
                if blind:
                    blind_reached_by.setdefault(address, set()).add(t.principal)

            # Byte counts are per window and not per destination — the wire
            # does not carry them that way — so a window reaching two internal
            # addresses attributes its egress to both. That overcounts, which
            # is the right direction for a question: it asks about more things
            # rather than missing the one that mattered.
            for address in window.tool_seen:
                if address in reached_by:
                    egress[address] = egress.get(address, 0) + window.tool_egress
                    ingress[address] = ingress.get(address, 0) + window.tool_ingress

    found = []
    for address, principals in reached_by.items():
        out, back = egress.get(address, 0), ingress.get(address, 0)
        if out < MIN_BYTES or out / max(back, 1) < MIN_RATIO:
            continue
        blind = blind_reached_by.get(address, set())
        if not blind:
            # Every workload reaching this already talks to a model provider we
            # recognise, so their traffic here is tool calls. Nothing hidden.
            continue
        found.append(Candidate(
            address=address, egress=out, ingress=back,
            principals=tuple(sorted(principals)),
            blind_principals=tuple(sorted(blind)),
        ))

    found.sort(key=lambda c: (-len(c.blind_principals), -c.egress, c.address))
    return found[:limit]


def blind_reach(found: list[Candidate]) -> dict[str, tuple[str, ...]]:
    """Which workloads reach an undeclared candidate without any model traffic.

    The inverse of the candidate list, and the join nobody was making. A
    gateway question and a review candidate were being shown on the same
    screen with no indication that they were about the same workload:

        "Is 10.0.7.40 a model gateway?"
        "deploy-remediation might be an agent, confidence 0.52."

    Those are one question. A workload that resembles an agent, has no model
    traffic we recognise, and sends a transcript-shaped stream at an address
    nobody has declared is not two weak signals — it is the specific shape of
    an agent behind a gateway, which is the case the whole declaration
    mechanism exists for.

    On the stress corpus this names `deploy-remediation`, which is a labelled
    agent that the classifier can only reach the review band on. It is the one
    workload in that corpus a customer most needs to be asked about.
    """
    reach: dict[str, set[str]] = {}
    for candidate in found:
        for principal in candidate.blind_principals:
            reach.setdefault(principal, set()).add(candidate.address)
    return {p: tuple(sorted(a)) for p, a in sorted(reach.items())}
