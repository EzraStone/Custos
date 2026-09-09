"""Destination classification from what a flow log actually contains.

A flow record has an address, a port, and sometimes `pkt-dst-aws-service`. It
has no hostname and no SNI. So every claim the classifier makes about what a
destination *is* has to come from those three fields.

Three mechanisms, in descending order of reliability:

1. **AWS service annotation.** `pkt-dst-aws-service` names the service for AWS
   destinations. BEDROCK and SAGEMAKER are model endpoints and AWS tells us so.
   This is exact, free, and covers the fastest-growing slice of model traffic.

2. **Published provider address ranges.** Anthropic, OpenAI, and the other
   providers front their APIs on CDN and cloud ranges. We maintain the prefix
   list. It goes stale, which is why `RANGES_REVISION` is stamped into every
   report — a finding is only as good as the catalogue that produced it.

3. **Address locality.** RFC 1918 destinations are internal. Combined with port,
   that separates internal APIs, datastores, and MCP servers.

The honest limitation, recorded here because it will be asked in diligence: a
model endpoint fronted by a customer's own gateway on a private address is
invisible to mechanism 1 and 2, and appears as an internal API. That is the
gateway-log ingestion path in the specification's fallback, and it is why the
fallback is a supplement rather than a competitor to this approach.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from functools import lru_cache

RANGES_REVISION = "2026-08-18"
"""Stamped into every report. A finding is only as current as this list."""


class DestinationClass(StrEnum):
    MODEL = "model"
    MCP = "mcp"
    INTERNAL_API = "internal_api"
    DATASTORE = "datastore"
    EXTERNAL = "external"
    """Reached the internet but matched no known provider. Not evidence of
    anything on its own, and never counted as model traffic."""


# Mechanism 1: AWS services that are model inference endpoints.
MODEL_AWS_SERVICES = frozenset({"BEDROCK", "SAGEMAKER"})

# Mechanism 2: published provider ranges. Deliberately narrow — a false positive
# here manufactures an agent finding out of unrelated traffic, which is the
# fastest way to lose a customer's trust in the whole report.
MODEL_RANGES: tuple[str, ...] = (
    "160.79.104.0/23",   # Anthropic
    "104.18.0.0/16",     # OpenAI via Cloudflare
    "52.94.236.0/24",    # Bedrock runtime regional
)
"""IPv4 only, and that is a blind spot rather than an oversight.

The providers are reachable over IPv6 and we have no published v6 ranges we can
verify. Guessing one would be worse than having none: a false positive here
manufactures an agent out of unrelated traffic, and the whole catalogue is
narrow for that reason.

So an agent that reaches a provider over IPv6 is invisible, in exactly the way
an agent behind an undeclared gateway is. The remedy is the same — the report
counts an account's public IPv6 destinations and says what it could not
classify — and it matters more each year, because AWS began charging for public
IPv4 addresses in 2024 and dual-stack VPCs are the response.
"""

# Mechanism 3: ports that identify a service class on internal addresses.
MCP_PORTS = frozenset({8931, 3000, 8080})
"""MCP servers are conventionally on 8931; 3000 and 8080 are common but shared
with ordinary HTTP services, so port alone never decides MCP — see `classify`."""

DATASTORE_PORTS = frozenset({
    5432,   # PostgreSQL
    3306,   # MySQL
    6379,   # Redis
    27017,  # MongoDB
    6333,   # Qdrant
    9200,   # OpenSearch / Elasticsearch
    8123,   # ClickHouse
    5439,   # Redshift
})

_STORAGE_AWS_SERVICES = frozenset({"S3", "DYNAMODB", "RDS", "ELASTICACHE"})

_MODEL_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(c) for c in MODEL_RANGES
)


def is_ipv6(address: str) -> bool:
    """Whether an address is IPv6. Not a classification, a question about form.

    Used to count what the catalogue cannot speak to, since MODEL_RANGES is
    IPv4 only.
    """
    try:
        return ipaddress.ip_address(address).version == 6
    except ValueError:
        return False


def clear_caches() -> None:
    """Drop memoised destination lookups.

    Both caches, always. Nothing in the running system mutates this catalogue —
    a customer's declarations live in `declared.py` and are carried with their
    account rather than written here — so this exists for tests and for a
    process that reloads a new built-in catalogue at startup.
    """
    is_model_endpoint.cache_clear()
    is_private.cache_clear()


_EXTRA_AWS_SERVICES: frozenset[str] = frozenset()
"""Reserved for a future built-in addition. A customer's declared services live
in `declared.py`, scoped to their account."""


@lru_cache(maxsize=8192)
def is_model_endpoint(addr: str, aws_service: str = "") -> bool:
    """True if this destination is a model inference endpoint."""
    if aws_service in MODEL_AWS_SERVICES or aws_service in _EXTRA_AWS_SERVICES:
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _MODEL_NETS)


@lru_cache(maxsize=8192)
def is_private(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_private
    except ValueError:
        return False


def classify(addr: str, port: int, aws_service: str = "") -> DestinationClass:
    """Classify one destination from address, port, and AWS service annotation."""
    if is_model_endpoint(addr, aws_service):
        return DestinationClass.MODEL
    if aws_service in _STORAGE_AWS_SERVICES:
        return DestinationClass.DATASTORE
    if not is_private(addr):
        return DestinationClass.EXTERNAL
    if port in DATASTORE_PORTS:
        return DestinationClass.DATASTORE
    if port == 8931:
        # The conventional MCP port. The shared ports in MCP_PORTS are not
        # sufficient on their own and fall through to internal_api.
        return DestinationClass.MCP
    return DestinationClass.INTERNAL_API


def is_tool_destination(cls: DestinationClass) -> bool:
    """Tool reach: anything an agent could act through, excluding models."""
    return cls in (
        DestinationClass.MCP,
        DestinationClass.INTERNAL_API,
        DestinationClass.DATASTORE,
    )
