"""The collector's record limit and the control plane's size cap are one limit.

They are set in different languages, in different repositories' worth of
reasoning, and they describe the same thing from opposite ends: how much one
collection window is allowed to be. If the collector's limit rises above what
the control plane accepts, every batch from a busy account is refused — and the
symptom is a customer whose scans silently stop, which is indistinguishable
from an account that went quiet.

So the arithmetic is checked rather than remembered. This is the same reasoning
as `test_wire_contract.py`: the duplication is acceptable because a test reads
both sides.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custos.api.compression import MAX_DECOMPRESSED

COLLECTOR = Path(__file__).resolve().parents[2] / "collector"
CLOUDWATCH = COLLECTOR / "internal" / "ingest" / "cloudwatch.go"


def max_events_per_run() -> int:
    source = CLOUDWATCH.read_text()
    match = re.search(r"const MaxEventsPerRun = ([\d_]+)", source)
    if match is None:
        pytest.skip("MaxEventsPerRun is no longer a constant in cloudwatch.go")
    return int(match.group(1).replace("_", ""))


def bytes_per_record() -> float:
    """Measured from the wire shape rather than remembered as a constant, so
    that a field added to FlowRecord moves this number too."""
    at = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    record = {
        "account_id": "447120043318", "interface_id": "eni-0a1b2c3d",
        "srcaddr": "10.0.1.5", "dstaddr": "160.79.104.10",
        "srcport": 41000, "dstport": 443, "protocol": 6,
        "packets": 40, "bytes": 140000,
        "start": at.isoformat(), "end": (at + timedelta(seconds=59)).isoformat(),
        "action": "ACCEPT", "log_status": "OK", "vpc_id": "vpc-0a1b2c3d",
        "subnet_id": "subnet-0ab12345", "direction": "egress",
        "src_aws_service": "", "dst_aws_service": "", "tcp_flags": 19,
    }
    return len(json.dumps(record)) + 1  # the comma between records


def test_a_full_window_fits_inside_what_the_control_plane_accepts():
    """The failure this prevents: a customer whose scans stop arriving, which
    looks exactly like an account that went quiet."""
    largest = max_events_per_run() * bytes_per_record()
    assert largest < MAX_DECOMPRESSED, (
        f"the collector will ship up to {largest / 1e6:.0f}MB and the control "
        f"plane refuses above {MAX_DECOMPRESSED / 1e6:.0f}MB"
    )


def test_the_cap_is_not_so_generous_that_it_is_not_a_cap():
    """It also bounds what one anonymous request can cost. Validating a full
    window already takes 2.7GB, so room for several is not a limit."""
    largest = max_events_per_run() * bytes_per_record()
    assert 2 * largest > MAX_DECOMPRESSED


def test_the_measurement_behind_the_deployment_memory_figure_still_holds():
    """deploy/README.md and compose.yaml both quote 225MB for a full window and
    size the container from it. If the wire shape grows, that figure is stale
    and the container is undersized before anyone notices."""
    largest_mb = max_events_per_run() * bytes_per_record() / 1e6
    assert 200 < largest_mb < 260, (
        f"a full window is now {largest_mb:.0f}MB; deploy/README.md and "
        "deploy/compose.yaml say 225MB and size the container from it"
    )
