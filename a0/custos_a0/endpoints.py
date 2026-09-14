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
    eni_name: str = ""
    """What the collector would find on this address's ENI, or "" for nothing.

    Modelling the lookup as always succeeding would be the same mistake as
    annotating both ends of a flow record: a corpus more informative than a
    real account, hiding the case the product has to handle. Real accounts have
    ENIs nobody tagged, so some endpoints here have no name and appear in the
    register as addresses.
    """
    eni_kind: str = ""
    endpoint_service: str = ""
    """The AWS endpoint service this address is an interface endpoint for.

    Two endpoints in the corpus have one, and they are the two halves of the
    same blindness. An account reaching Bedrock over PrivateLink has model
    traffic going to a private address with nothing in the flow record to say
    so, and AWS will name the service. An account reaching a provider through
    a service another AWS account published has exactly the same traffic, and
    AWS will only say that somebody else published it."""


# Model providers.
ANTHROPIC = Endpoint("api.anthropic.com", "160.79.104.10", 443, EndpointClass.MODEL)
OPENAI = Endpoint("api.openai.com", "104.18.7.192", 443, EndpointClass.MODEL)
BEDROCK = Endpoint(
    "bedrock-runtime.us-east-1.amazonaws.com", "52.94.236.10", 443,
    EndpointClass.MODEL, aws_service="BEDROCK",
)

# MCP servers.
MCP_GITHUB = Endpoint(
    "mcp-github.svc.internal", "10.0.5.11", 8931, EndpointClass.MCP, writes=True,
    eni_name="mcp-github", eni_kind="tag",
)
MCP_FILES = Endpoint(
    "mcp-filesystem.svc.internal", "10.0.5.12", 8931, EndpointClass.MCP, writes=True,
    eni_name="mcp-filesystem", eni_kind="tag",
)

# Internal APIs.
BILLING_API = Endpoint(
    "billing-api.svc.internal", "10.0.4.21", 8080, EndpointClass.INTERNAL_API, writes=True,
    eni_name="billing-api", eni_kind="load-balancer",
)
TICKET_API = Endpoint(
    "ticketing.svc.internal", "10.0.4.22", 8080, EndpointClass.INTERNAL_API, writes=True,
    eni_name="ticketing", eni_kind="load-balancer",
)
# Deliberately unnamed. Every real account has ENIs nobody tagged, and this is
# the one that shows up in the register as a bare address.
DEPLOY_API = Endpoint(
    "deploy-ctl.svc.internal", "10.0.4.23", 8443, EndpointClass.INTERNAL_API, writes=True
)

# Datastores.
ORDERS_DB = Endpoint(
    "orders.cluster-ro.rds.amazonaws.com", "10.0.9.44", 5432, EndpointClass.DATASTORE,
    eni_name="rds", eni_kind="rds",
)
BILLING_DB = Endpoint(
    "billing.cluster.rds.amazonaws.com", "10.0.9.45", 5432,
    EndpointClass.DATASTORE, writes=True, eni_name="rds", eni_kind="rds",
)
# Named by the only label anybody gave it. Nobody tagged this ENI and somebody
# named the security group it sits in, which is the shape an account with poor
# tag hygiene has — and the one case in the corpus where the scope's label
# describes a group the host is in rather than the host.
VECTOR_DB = Endpoint(
    "vectors.svc.internal", "10.0.6.30", 6333, EndpointClass.DATASTORE,
    eni_name="vector-store", eni_kind="security-group",
)
ARTIFACTS_S3 = Endpoint(
    "artifacts.s3.us-east-1.amazonaws.com", "52.216.10.7", 443,
    EndpointClass.DATASTORE, aws_service="S3", writes=True,
)

# Internal services that ordinary infrastructure sends far more to than it
# gets back. Every one of these is a real thing a real account runs, and every
# one of them has the traffic shape the gateway detector looks for: a private
# address, a lot of egress, an acknowledgement coming back. They exist so that
# "does this ask questions about ordinary internal APIs" is a measurement
# rather than a hope.
LOG_COLLECTOR = Endpoint(
    "logs-ingest.svc.internal", "10.0.8.10", 24224, EndpointClass.INTERNAL_API,
    writes=True, eni_name="logs-ingest", eni_kind="tag",
)
BACKUP_SVC = Endpoint(
    "backup.svc.internal", "10.0.8.11", 8443, EndpointClass.INTERNAL_API, writes=True,
)
METRICS_PUSH = Endpoint(
    "pushgateway.svc.internal", "10.0.8.12", 9091, EndpointClass.INTERNAL_API,
    eni_name="pushgateway", eni_kind="tag",
)
ARTIFACT_REGISTRY = Endpoint(
    "artifacts.svc.internal", "10.0.8.13", 443, EndpointClass.INTERNAL_API, writes=True,
)
EVENT_PROXY = Endpoint(
    "kafka-rest.svc.internal", "10.0.8.14", 8082, EndpointClass.INTERNAL_API,
    writes=True, eni_name="kafka-rest", eni_kind="load-balancer",
)
# The two that a ratio ceiling does not separate. Both send something large and
# get something substantial back, which is what a model gateway does.
THUMBNAILER = Endpoint(
    "imaging.svc.internal", "10.0.8.15", 8443, EndpointClass.INTERNAL_API,
    eni_name="imaging", eni_kind="load-balancer",
)
DOC_EXTRACT = Endpoint(
    "doc-extract.svc.internal", "10.0.8.16", 8443, EndpointClass.INTERNAL_API,
)

# Bedrock reached over PrivateLink.
#
# An interface VPC endpoint puts an ENI in the customer's own subnet, so every
# model call goes to a private address in their VPC. The flow log's
# `pkt-dst-aws-service` annotation is for AWS's published address ranges and
# this is not one, so nothing in the record says what it is — the traffic looks
# exactly like an internal API.
#
# The same blindness as a self-hosted gateway, arrived at from the other
# direction, and with one difference that matters: nobody has to be asked. AWS
# knows this ENI is the endpoint for com.amazonaws.us-east-1.bedrock-runtime
# and will say so.
BEDROCK_PRIVATELINK = Endpoint(
    "bedrock-runtime.us-east-1.amazonaws.com", "10.0.15.20", 443,
    EndpointClass.INTERNAL_API,
    eni_name="bedrock-runtime", eni_kind="vpc-endpoint",
    endpoint_service="com.amazonaws.us-east-1.bedrock-runtime",
)

# A model provider selling into AWS. The customer's agents reach it over
# PrivateLink, so the traffic never touches the public internet and the
# destination is a private address in their own subnet.
#
# The difference from BEDROCK_PRIVATELINK is the whole point. That one AWS
# explains: the service is com.amazonaws.us-east-1.bedrock-runtime and every
# agent behind it is found with nobody asked. This one AWS names
# com.amazonaws.vpce.us-east-1.vpce-svc-0a1b2c3d and will say no more — no
# private DNS name, because this publisher did not configure one, and no tag,
# because this customer did not write one.
#
# So it is the residue: the exact case that is left after everything that can
# be looked up has been. It stays a question for a human, and the reason it is
# in the corpus is to check that the question actually gets asked.
PROVIDER_PRIVATELINK = Endpoint(
    "api.modelprovider.example", "10.0.15.21", 443,
    EndpointClass.INTERNAL_API,
    eni_name="vpce-svc-0a1b2c3d", eni_kind="vpc-endpoint",
    endpoint_service="com.amazonaws.vpce.us-east-1.vpce-svc-0a1b2c3d",
)

# The load balancer. Shared by every inbound-facing workload.
ALB = Endpoint("alb-prod.elb.amazonaws.com", "10.0.1.5", 443, EndpointClass.INGRESS)

MODEL_ENDPOINTS: frozenset[str] = frozenset({ANTHROPIC.ip, OPENAI.ip, BEDROCK.ip})

ALL: tuple[Endpoint, ...] = (
    ANTHROPIC, OPENAI, BEDROCK,
    MCP_GITHUB, MCP_FILES,
    BILLING_API, TICKET_API, DEPLOY_API,
    ORDERS_DB, BILLING_DB, VECTOR_DB, ARTIFACTS_S3,
    LOG_COLLECTOR, BACKUP_SVC, METRICS_PUSH, ARTIFACT_REGISTRY, EVENT_PROXY,
    THUMBNAILER, DOC_EXTRACT, BEDROCK_PRIVATELINK, PROVIDER_PRIVATELINK,
    ALB,
)
"""Every endpoint the corpus can generate traffic to.

Explicit rather than derived by walking the module, so adding an endpoint and
forgetting to include it is a visible omission rather than an invisible one.
"""
