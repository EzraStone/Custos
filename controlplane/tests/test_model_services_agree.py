"""Which AWS endpoint services are model inference, in both languages.

The collector's preflight says "this account's model calls go over PrivateLink
and the agents behind it will be found". The control plane is what finds them.
If the two lists drift, `--check` promises something the scan does not deliver
— and the customer who heard the promise is the one who turned PrivateLink on
deliberately and is watching for exactly this.

Duplicated because preflight runs before a control plane is in the picture at
all, and checked because the duplication is only acceptable if something reads
both sides. Same reasoning as `test_wire_contract.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

GO = (
    Path(__file__).resolve().parents[2]
    / "collector" / "internal" / "preflight" / "check.go"
)


def _go_services() -> set[str]:
    source = GO.read_text()
    block = re.search(
        r"var modelEndpointServices = map\[string\]bool\{(.*?)\n\}", source, re.S
    )
    assert block, "modelEndpointServices is not in check.go any more"
    return set(re.findall(r'"([a-z0-9-]+)":\s*true', block.group(1)))


def test_both_sides_recognise_the_same_services():
    from custos.catalog import MODEL_ENDPOINT_SERVICES

    assert _go_services() == set(MODEL_ENDPOINT_SERVICES)


def test_the_list_is_not_empty_in_either_language():
    """A regex that stopped matching would make the test above pass by
    comparing two empty sets, which is the way this kind of check dies."""
    from custos.catalog import MODEL_ENDPOINT_SERVICES

    assert _go_services()
    assert MODEL_ENDPOINT_SERVICES


def test_the_control_plane_reads_the_last_segment_the_same_way():
    """Go slices after the last dot; Python calls rsplit. Two spellings of one
    rule, and a service name with no dot at all is the case that separates
    them."""
    from custos.catalog import is_model_endpoint_service

    assert is_model_endpoint_service("com.amazonaws.eu-west-1.bedrock-runtime")
    assert not is_model_endpoint_service("bedrock-runtime")
    assert not is_model_endpoint_service("")
