"""The batch schema, mirroring the collector's wire types.

Lives beside `telemetry.py` rather than under `api/` because it is a contract
with the collector, not an API concern. The pipeline needs it, the API needs
it, and the A0 experiment produces it — putting it inside the API package made
`pipeline` depend on `api`, which made `api` depend on `pipeline`, which was a
circular import waiting for the first module that imported them in the other
order.

This file and `collector/internal/wire/wire.go` describe the same bytes. They
are in different languages, so they cannot share a definition, which makes drift
between them a real risk — and a silent one, because a field the API ignores
looks exactly like a field the collector never sent.

`test_wire_contract.py` parses the Go source and asserts the two agree, field
for field. That test is the reason this duplication is acceptable.

SEC-18 applies here as it does on the Go side: no model in this module has a
field capable of holding a payload, and the API rejects unknown fields outright
rather than ignoring them. A collector that started sending prompts would get an
error, not a silent accept.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Rejecting unknown fields is the point. The default — ignore and continue —
# would let a modified collector ship anything it liked and have the API
# silently drop it, which reads as compliance while being nothing of the kind.
STRICT = ConfigDict(extra="forbid", frozen=True)


class FlowRecord(BaseModel):
    model_config = STRICT

    account_id: str = ""
    interface_id: str = ""
    srcaddr: str = ""
    dstaddr: str = ""
    srcport: int = 0
    dstport: int = 0
    protocol: int = 0
    packets: int = 0
    bytes: int = 0
    start: datetime
    end: datetime
    action: str = ""
    log_status: str = ""
    vpc_id: str = ""
    subnet_id: str = ""
    direction: Literal["egress", "ingress"]
    dst_aws_service: str = ""
    tcp_flags: int = 0


class InboundRequest(BaseModel):
    model_config = STRICT

    at: datetime
    target: str = ""
    sent_bytes: int = 0
    received_bytes: int = 0


class PrincipalFacts(BaseModel):
    model_config = STRICT

    principal: str
    account_id: str = ""
    iam_path: str = "/"
    compute: str = ""
    role_tags: dict[str, str] = Field(default_factory=dict)
    resource_tags: dict[str, str] = Field(default_factory=dict)
    actions: list[str] = Field(default_factory=list)
    assumable_roles: list[str] = Field(default_factory=list)


class Attachment(BaseModel):
    model_config = STRICT

    interface_id: str
    principal: str = ""
    address: str = ""
    subnet_id: str = ""
    compute: str = ""


class Collection(BaseModel):
    """How the collection itself went, as opposed to what it found.

    Ships because the control plane cannot otherwise tell "this account is
    clean" from "this scan read a third of the traffic". Those produce
    identical findings and mean entirely different things.

    Nothing here describes customer infrastructure — they are counters about
    our own reading — so it adds no SEC-18 surface.
    """

    model_config = STRICT

    lines_read: int = 0
    lines_parsed: int = 0
    lines_malformed: int = 0
    records_skipped: int = 0
    truncated: bool = False
    have_access_logs: bool = False

    @property
    def parsed_fraction(self) -> float:
        if self.lines_read <= 0:
            # No lines read is not full coverage. Reporting 1.0 here would put
            # a green banner on a scan that read nothing.
            return 0.0
        return self.lines_parsed / self.lines_read


class Batch(BaseModel):
    """One collection window, as shipped.

    This is the complete set of things that ever leaves a customer account.
    """

    model_config = STRICT

    account_id: str
    region: str = ""
    window_start: datetime
    window_end: datetime
    collector_version: str = ""
    collection: Collection = Field(default_factory=Collection)
    flows: list[FlowRecord] = Field(default_factory=list)
    requests: list[InboundRequest] = Field(default_factory=list)
    principals: list[PrincipalFacts] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)

    @property
    def have_alb_logs(self) -> bool:
        """Whether the collector was configured with load balancer access logs.

        Inferred from the batch carrying any requests at all, which is the one
        place this inference is safe: the collector omits the field entirely
        when unconfigured, and a configured collector on an account with zero
        inbound traffic has nothing to classify anyway.
        """
        return self.collection.have_access_logs or bool(self.requests)


class BatchAccepted(BaseModel):
    """Response to a successful batch ingestion."""

    batch_id: int
    scan_id: int
    duplicate: bool
    agents_found: int
    review_candidates: int
    coverage_note: str = ""
