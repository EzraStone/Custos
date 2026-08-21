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


def extend(ranges: list[str], aws_services: list[str] | None = None) -> None:
    """Add model endpoints the built-in catalogue does not know about.

    Three cases this exists for, all of which produce an invisible agent
    otherwise:

      A self-hosted gateway. Many teams front every provider behind one
      internal endpoint, which classifies as an internal API and takes all its
      model traffic with it.

      A provider we have not added. The catalogue goes stale between releases
      and a customer may be the first to use something.

      A private endpoint. Bedrock over a VPC endpoint appears on a private
      address, and pkt-dst-aws-service does not always carry through.

    Deliberately additive and never subtractive. Removing a range would let a
    customer hide an agent from their own report, and a security tool that can
    be configured blind is worse than one that cannot be configured at all.

    Caches are cleared, because a classification made before the extension
    would otherwise outlive it.
    """
    global _MODEL_NETS, _EXTRA_AWS_SERVICES

    parsed = []
    for entry in ranges:
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise ValueError(f"not a valid network: {entry!r}") from exc

    _MODEL_NETS = _MODEL_NETS + tuple(parsed)
    if aws_services:
        _EXTRA_AWS_SERVICES = _EXTRA_AWS_SERVICES | frozenset(aws_services)
    clear_caches()


def reset() -> None:
    """Restore the built-in catalogue, discarding every extension.

    Exists for tests and for a control plane that reloads configuration
    without restarting. Deliberately not implemented by reloading the module:
    other modules import these functions by reference at import time, so a
    reload leaves them pointing at pre-reload objects whose caches still hold
    the extended answers — which silently corrupts classification for
    everything that imported the catalogue before the reload.
    """
    global _MODEL_NETS, _EXTRA_AWS_SERVICES

    _MODEL_NETS = tuple(ipaddress.ip_network(c) for c in MODEL_RANGES)
    _EXTRA_AWS_SERVICES = frozenset()
    clear_caches()


def clear_caches() -> None:
    """Drop memoised destination lookups.

    Both caches, always. A classification memoised before an extension would
    otherwise outlive it, and a stale 'not a model endpoint' is an agent that
    stays invisible after the customer told us where to look.
    """
    is_model_endpoint.cache_clear()
    is_private.cache_clear()


def configured_ranges() -> tuple[str, ...]:
    """Every model range currently in effect, for the report's provenance.

    A finding is only as good as the catalogue that produced it, and a customer
    who extended the catalogue should see that reflected rather than reading
    the built-in revision and assuming it was all we used.
    """
    return tuple(str(net) for net in _MODEL_NETS)


_EXTRA_AWS_SERVICES: frozenset[str] = frozenset()
"""AWS services declared as model endpoints by a customer's configuration."""


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
