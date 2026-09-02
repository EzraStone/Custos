"""What to call a destination, when an operator has to approve it.

The register's scope is what an operator reads before conferring authority.
Until this module existed it read `10.0.4.23`, `52.216.10.7` — which is what
the flow log carries, and which nobody can make a decision about.

Two things are available and were being discarded:

**The AWS service annotation.** Flow Logs v5 carries `pkt-dst-aws-service`, and
the classifier already reads it to decide *class*. It also says *what the thing
is*, so a scope can say `s3` instead of an address in an S3 edge range.

**The port.** A private address on 5432 is Postgres. That is not a guess; it is
the same table `classify` already uses to decide the destination is a datastore
at all.

A naming decision that matters more than it looks: **a named destination is
keyed by its name, not its address.** Three S3 edge IPs collapse to one `s3`
entry. That is not a display convenience — AWS service addresses rotate, so an
approval recorded against `52.216.10.7` is stale within days and would have to
be re-granted for traffic that did not change. Approving `s3` is a claim that
stays true.

Where nothing is known the address is returned unchanged. An honest address
beats an invented name, and `10.0.4.23` at least tells an operator which
network they are looking at.

There is deliberately no way to turn a label back into an address. Anything
that needs to re-classify a destination keeps the address it started with; a
reverse mapping would be a guess, and a guess about which host an approval
covers is the wrong place to have one.
"""

from __future__ import annotations

from .catalog import DestinationClass, classify

# Ports whose service is unambiguous enough to put in front of an approver.
# Same table `classify` uses to decide a private address is a datastore; naming
# it costs nothing beyond what has already been assumed.
PORT_NAMES: dict[int, str] = {
    5432: "postgres",
    3306: "mysql",
    6379: "redis",
    27017: "mongodb",
    6333: "qdrant",
    9200: "opensearch",
    8123: "clickhouse",
    5439: "redshift",
}


def describe(addr: str, port: int = 0, aws_service: str = "") -> str:
    """Name one destination, or return the address when nothing is known."""
    if aws_service:
        # The annotation is the strongest signal available and is the only one
        # that survives an address change.
        return aws_service.strip().lower()

    cls = classify(addr, port, aws_service)
    if cls is DestinationClass.DATASTORE and port in PORT_NAMES:
        return f"{PORT_NAMES[port]} {addr}"
    if cls is DestinationClass.MCP:
        return f"mcp {addr}"
    return addr

