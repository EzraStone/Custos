"""TCP connection reuse, and the framing overhead that rides on it.

Two effects are modelled here, both of which change what a flow log can show.

**Connection reuse.** Every model provider SDK pools connections. Go's default
transport keeps an idle connection for 90 seconds; Python's `httpx` and
`requests` with an adapter behave comparably. A sequence of calls to the same
endpoint therefore shares one 5-tuple, and a flow log keyed on the 5-tuple
cannot tell them apart. A new connection appears only after the pool goes idle
past the keep-alive, or when concurrency exceeds the pool.

This is the mechanism that erases per-call timing. It is not an edge case; it is
the default behaviour of every client in production.

**Framing overhead.** Application payload is not what appears in the `bytes`
field. TLS adds per-record overhead, TCP segments the stream, and every packet
carries IP and TCP headers. The reverse direction carries pure ACKs. A new
connection additionally pays a handshake, which is large and asymmetric — the
certificate chain dominates and arrives inbound.

The handshake matters for a reason that is not obvious: it puts a floor under
the inbound byte count of any short-lived connection, which compresses the
egress-to-ingress ratio that the classifier reads. Ignoring it would make agents
look more separable than they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

MSS = 1460
"""Maximum segment size on a standard 1500-byte MTU path."""

IP_TCP_HEADER = 40
"""IPv4 plus TCP header on every packet."""

TLS_RECORD_MAX = 16_384
TLS_RECORD_OVERHEAD = 29
"""AEAD nonce, tag, and record header per TLS record."""

HANDSHAKE_OUT = 1_800
"""ClientHello, key share, Finished."""

HANDSHAKE_IN = 5_200
"""ServerHello plus a typical certificate chain. Dominates the handshake."""

DEFAULT_KEEPALIVE = timedelta(seconds=90)
"""Go's default idle connection timeout, and a reasonable stand-in for the
Python HTTP clients used by the same SDKs."""


def framed(payload: int) -> tuple[int, int]:
    """Return (wire_bytes, packets) for a payload sent in one direction."""
    if payload <= 0:
        return 0, 0
    tls = payload + ((payload + TLS_RECORD_MAX - 1) // TLS_RECORD_MAX) * TLS_RECORD_OVERHEAD
    packets = max(1, (tls + MSS - 1) // MSS)
    return tls + packets * IP_TCP_HEADER, packets


def ack_traffic(data_packets: int) -> tuple[int, int]:
    """Return (wire_bytes, packets) for the pure ACKs answering `data_packets`.

    Delayed ACK: roughly one acknowledgement per two data segments.
    """
    acks = max(1, data_packets // 2) if data_packets else 0
    return acks * (IP_TCP_HEADER + 12), acks


@dataclass(slots=True)
class Connection:
    """One TCP connection, identified in a flow log by its source port."""

    srcport: int
    dstaddr: str
    dstport: int
    opened_at: datetime
    last_used: datetime
    is_new: bool = True
    """True until the first flush. Drives the SYN flag on the first record."""


@dataclass(slots=True)
class ConnectionPool:
    """Per-destination connection pool with keep-alive reuse."""

    keepalive: timedelta = DEFAULT_KEEPALIVE
    max_per_host: int = 4
    _next_port: int = 32_768
    _pools: dict[tuple[str, int], list[Connection]] = field(default_factory=dict)

    def acquire(self, dstaddr: str, dstport: int, at: datetime) -> Connection:
        """Return a connection for this destination, reusing one if warm."""
        key = (dstaddr, dstport)
        pool = self._pools.setdefault(key, [])

        # Evict connections that idled past the keep-alive.
        pool[:] = [c for c in pool if at - c.last_used <= self.keepalive]

        if pool:
            # Round-robin over the warm pool, oldest-used first. Real clients
            # pick the most recently returned; the difference does not affect
            # anything a flow log can see.
            conn = min(pool, key=lambda c: c.last_used)
            conn.last_used = at
            return conn

        conn = Connection(
            srcport=self._next_port, dstaddr=dstaddr, dstport=dstport,
            opened_at=at, last_used=at,
        )
        self._next_port += 1
        if self._next_port > 60_999:
            self._next_port = 32_768
        pool.append(conn)
        if len(pool) > self.max_per_host:
            pool.pop(0)
        return conn
