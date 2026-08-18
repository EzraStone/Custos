"""The lossy transform from real network activity to VPC Flow Logs.

This package exists because of one detail that the specification does not
account for, and that detail decides whether G0 passes.

VPC Flow Logs aggregate over a fixed interval — 60 seconds by default, 600 in
the cheaper configuration — per 5-tuple. Modern HTTP clients reuse TCP
connections: every model provider SDK keeps a pooled connection alive for tens
of seconds. The two facts compose badly. Twenty sequential model API calls made
over one pooled connection inside one aggregation window do not appear as twenty
records. They appear as ONE record, with the byte counts summed and the timing
gone.

The specification's headline signal is "burst of sequential model calls,
monotonically growing payload size, sub-second gaps," sourced from "flow log
timing + byte counts." Under aggregation, all three of those are erased for
exactly the workloads that matter most — the busy ones.

Simulating the erasure here costs an afternoon. Discovering it in week two of a
customer capture costs the wedge.
"""

from .aggregate import AggregationConfig, Capture, aggregate
from .record import Direction, FlowRecord, InboundRequest

__all__ = [
    "AggregationConfig",
    "Capture",
    "Direction",
    "FlowRecord",
    "InboundRequest",
    "aggregate",
]
