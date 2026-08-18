"""Destination catalogue for synthetic workloads.

Only three properties matter to the classifier: the address it can see in a flow
log, the port, and what class of thing lives there. Host names are recorded for
report legibility but the classifier is never given them — in a real capture we
resolve class from destination address, port, and `pkt-dst-aws-service`, and the
synthetic corpus must not hand it an advantage it will not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EndpointClass(StrEnum):
    """What kind of thing sits at a destination."""

    MODEL = "model"
    """A model provider inference endpoint."""

    MCP = "mcp"
    """An MCP server. Near-deterministic evidence of tool use when present."""

    INTERNAL_API = "internal_api"
    """An internal service API."""

    DATASTORE = "datastore"
    """A database, object store, or vector index."""

    INGRESS = "ingress"
    """The load balancer in front of the workload. Inbound, not a destination."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    ip: str
    port: int
    cls: EndpointClass
    aws_service: str = ""
    writes: bool = False
    """Ground truth for reach validation: does the principal hold write access here."""


# Model providers.
ANTHROPIC = Endpoint("api.anthropic.com", "160.79.104.10", 443, EndpointClass.MODEL)
OPENAI = Endpoint("api.openai.com", "104.18.7.192", 443, EndpointClass.MODEL)
BEDROCK = Endpoint(
    "bedrock-runtime.us-east-1.amazonaws.com", "52.94.236.10", 443,
    EndpointClass.MODEL, aws_service="BEDROCK",
)

# MCP servers.
MCP_GITHUB = Endpoint(
    "mcp-github.svc.internal", "10.0.5.11", 8931, EndpointClass.MCP, writes=True
)
MCP_FILES = Endpoint(
    "mcp-filesystem.svc.internal", "10.0.5.12", 8931, EndpointClass.MCP, writes=True
)

# Internal APIs.
BILLING_API = Endpoint(
    "billing-api.svc.internal", "10.0.4.21", 8080, EndpointClass.INTERNAL_API, writes=True
)
TICKET_API = Endpoint(
    "ticketing.svc.internal", "10.0.4.22", 8080, EndpointClass.INTERNAL_API, writes=True
)
DEPLOY_API = Endpoint(
    "deploy-ctl.svc.internal", "10.0.4.23", 8443, EndpointClass.INTERNAL_API, writes=True
)

# Datastores.
ORDERS_DB = Endpoint(
    "orders.cluster-ro.rds.amazonaws.com", "10.0.9.44", 5432, EndpointClass.DATASTORE
)
BILLING_DB = Endpoint(
    "billing.cluster.rds.amazonaws.com", "10.0.9.45", 5432, EndpointClass.DATASTORE, writes=True
)
VECTOR_DB = Endpoint("vectors.svc.internal", "10.0.6.30", 6333, EndpointClass.DATASTORE)
ARTIFACTS_S3 = Endpoint(
    "artifacts.s3.us-east-1.amazonaws.com", "52.216.10.7", 443,
    EndpointClass.DATASTORE, aws_service="S3", writes=True,
)

# The load balancer. Shared by every inbound-facing workload.
ALB = Endpoint("alb-prod.elb.amazonaws.com", "10.0.1.5", 443, EndpointClass.INGRESS)

MODEL_ENDPOINTS: frozenset[str] = frozenset({ANTHROPIC.ip, OPENAI.ip, BEDROCK.ip})
