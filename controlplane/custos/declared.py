"""Model endpoints a customer told us about, scoped to their account.

`catalog.extend()` has existed since the catalogue did, with a careful
docstring explaining the three cases it handles, and nothing has ever called
it. There was no configuration path: a customer running every model call
through a self-hosted gateway had no way to say so, and their agents were
invisible. `docs/STATUS.md` names that as the single most likely reason a real
scan comes back emptier than it should.

**Per account, not per process.** `extend()` mutates module globals, which is
the wrong shape for a control plane holding several customers. Declaring one
account's gateway would make that address a model endpoint for every other
account in the same process — so a coincidental address collision in another
customer's VPC would manufacture agents out of unrelated traffic. That is the
fastest way to lose trust in a whole report, and it is not a hypothetical:
10.0.0.0/8 is where everyone's internal services live.

So a declaration is a value carried alongside the account's telemetry, and the
built-in catalogue stays a module-level constant that nothing writes to.

**Additive only, in effect.** Declaring an endpoint can only make more traffic
classify as model traffic. It cannot hide an agent, which is why a customer is
allowed to write to it at all — a security tool whose configuration can
suppress a finding is worse than one with no configuration.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from .catalog import DestinationClass, classify

_Net = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True, slots=True)
class Declaration:
    """One endpoint a customer declared, and why they said it was there."""

    value: str
    kind: str
    """`range` for a CIDR or address, `aws_service` for a service name."""
    note: str = ""
    """What the customer called it. Shown wherever the declaration explains a
    finding, because "we classified this as a model because you told us to" is
    only useful if it also says what they told us it was."""


@dataclass(frozen=True, slots=True)
class Declared:
    """An account's declarations, ready to classify against.

    Empty is the common case and costs nothing: `applies` short-circuits, so
    an account that declared nothing pays one boolean per destination.
    """

    nets: tuple[_Net, ...] = ()
    services: frozenset[str] = frozenset()
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.nets and not self.services

    def covers(self, addr: str, aws_service: str = "") -> bool:
        """Whether this account declared this destination a model endpoint."""
        if self.empty:
            return False
        if aws_service and aws_service in self.services:
            return True
        if not self.nets:
            return False
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        return any(ip in net for net in self.nets)

    def note_for(self, addr: str, aws_service: str = "") -> str:
        """What the customer called whatever covers this destination."""
        if aws_service and aws_service in self.services:
            return self.notes.get(aws_service, "")
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return ""
        for net in self.nets:
            if ip in net:
                return self.notes.get(str(net), "")
        return ""


def build(declarations: list[Declaration]) -> Declared:
    """Parse declarations into something classification can use.

    An unparseable entry raises rather than being skipped. A declaration that
    silently did nothing would leave a customer believing their gateway was
    covered while their agents stayed invisible — the exact failure this whole
    mechanism exists to prevent.
    """
    nets: list[_Net] = []
    services: set[str] = set()
    notes: dict[str, str] = {}

    for entry in declarations:
        if entry.kind == "range":
            try:
                net = ipaddress.ip_network(entry.value, strict=False)
            except ValueError as exc:
                raise ValueError(f"not a valid network: {entry.value!r}") from exc
            nets.append(net)
            notes[str(net)] = entry.note
        elif entry.kind == "aws_service":
            services.add(entry.value)
            notes[entry.value] = entry.note
        else:
            raise ValueError(
                f"unknown declaration kind {entry.kind!r}: expected 'range' or 'aws_service'"
            )

    return Declared(nets=tuple(nets), services=frozenset(services), notes=notes)


def classify_with(
    declared: Declared, addr: str, port: int, aws_service: str = ""
) -> DestinationClass:
    """Classify a destination, honouring this account's declarations.

    The declaration wins. A customer saying "this is our model gateway" is a
    stronger claim than any inference we make from a port number, and the
    built-in answer for a private address on 443 — an internal API — is
    precisely the wrong one for the case this exists to fix.
    """
    if declared.covers(addr, aws_service):
        return DestinationClass.MODEL
    return classify(addr, port, aws_service)
