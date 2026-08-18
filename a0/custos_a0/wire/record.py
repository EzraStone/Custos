"""Re-export of the telemetry contract.

The record types are a product interface and live in `custos.telemetry`. The
experiment synthesises them; it does not define them. Importing through this
module keeps the A0 code reading naturally without forking the definition.
"""

from custos.telemetry import (
    ACK,
    FIN,
    LOG_FORMAT,
    PSH,
    RST,
    SYN,
    Direction,
    FlowRecord,
    InboundRequest,
    write_lines,
)

__all__ = [
    "ACK",
    "FIN",
    "LOG_FORMAT",
    "PSH",
    "RST",
    "SYN",
    "Direction",
    "FlowRecord",
    "InboundRequest",
    "write_lines",
]
