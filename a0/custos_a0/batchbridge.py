"""Turn the synthetic corpus into a wire batch.

The corpus is still the only realistic end-to-end input available before a
customer exists, so it is fed through the real API schema and the real
persistence path rather than through fixtures written to make them pass.
"""

from __future__ import annotations

from datetime import timedelta

from custos.batch import (
    Attachment,
    Batch,
    Collection,
    Destination,
    FlowRecord,
    InboundRequest,
    PrincipalFacts,
)

from . import corpus as corpus_mod
from . import endpoints
from .scanbridge import CAPABILITIES, FACTS, _short
from .trace import Corpus
from .wire import AggregationConfig, aggregate


def build_batch(
    corpus: Corpus | None = None,
    interval_seconds: int = 60,
    have_alb_logs: bool = True,
    with_attribution: bool = True,
) -> Batch:
    """Produce a batch in exactly the shape the collector ships."""
    c = corpus if corpus is not None else corpus_mod.build()
    capture = aggregate(
        c, AggregationConfig(interval=timedelta(seconds=interval_seconds),
                             have_alb_logs=have_alb_logs)
    )

    flows = [
        FlowRecord(
            account_id=r.account_id, interface_id=r.interface_id,
            srcaddr=r.srcaddr, dstaddr=r.dstaddr, srcport=r.srcport,
            dstport=r.dstport, protocol=r.protocol, packets=r.packets,
            bytes=r.bytes, start=r.start, end=r.end, action=r.action,
            log_status=r.log_status, vpc_id=r.vpc_id, subnet_id=r.subnet_id,
            direction=str(r.direction),
            src_aws_service=r.src_aws_service,
            dst_aws_service=r.dst_aws_service,
            tcp_flags=r.tcp_flags,
        )
        for r in capture.records
    ]

    requests = [
        InboundRequest(at=req.at, target=req.target,
                       sent_bytes=req.sent_bytes, received_bytes=req.received_bytes)
        for reqs in capture.requests.values()
        for req in reqs
    ]

    attachments: list[Attachment] = []
    principals: list[PrincipalFacts] = []
    if with_attribution:
        for w in c.workloads:
            attachments.append(Attachment(
                interface_id=w.eni, principal=w.principal, address=w.src_ip,
                subnet_id=w.subnet, compute=w.compute,
            ))
            short = _short(w.principal)
            extra = FACTS.get(short, {})
            principals.append(PrincipalFacts(
                principal=w.principal,
                account_id=capture.config.account_id,
                iam_path=extra.get("iam_path", "/"),
                compute=w.compute,
                role_tags=extra.get("role_tags", {}),
                resource_tags=extra.get("resource_tags", {}),
                actions=sorted(CAPABILITIES.get(short, set())),
            ))

    # What the collector's ENI lookup would have found. Only the endpoints a
    # real account would have named: the ones behind a load balancer, the ones
    # someone tagged, and RDS, which AWS describes itself. The rest arrive as
    # addresses, which is what the register then shows.
    destinations = [
        Destination(address=ep.ip, name=ep.eni_name, kind=ep.eni_kind)
        for ep in endpoints.ALL
        if ep.eni_name
    ]

    return Batch(
        account_id=capture.config.account_id,
        region="us-east-1",
        window_start=c.start,
        window_end=c.end,
        collector_version="a0-synthetic",
        # Synthetic telemetry is lossless by construction: every generated
        # record is one the collector would have parsed. Saying so explicitly
        # keeps the coverage banner honest rather than leaving it to a default.
        collection=Collection(
            lines_read=len(flows),
            lines_parsed=len(flows),
            have_access_logs=have_alb_logs,
        ),
        flows=flows,
        requests=requests,
        principals=principals,
        attachments=attachments,
        destinations=destinations,
    )
