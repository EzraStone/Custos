"""Endpoints a customer declared, scoped to their account."""

from __future__ import annotations

from datetime import timedelta

import pytest

from custos.catalog import DestinationClass
from custos.declared import Declaration, Declared, build, classify_with


def gateway(note: str = "llm-gateway") -> Declared:
    return build([Declaration("10.0.7.0/24", "range", note)])


def test_a_declared_range_classifies_as_a_model_endpoint():
    assert classify_with(gateway(), "10.0.7.9", 443) is DestinationClass.MODEL


def test_the_declaration_beats_the_built_in_answer():
    """The case this exists for. A private address on 443 is an internal API by
    every rule we have, and that is exactly the wrong answer for a gateway a
    customer just told us about."""
    assert classify_with(build([]), "10.0.7.9", 443) is DestinationClass.INTERNAL_API
    assert classify_with(gateway(), "10.0.7.9", 443) is DestinationClass.MODEL


def test_an_undeclared_address_is_unaffected():
    assert classify_with(gateway(), "10.0.8.9", 443) is DestinationClass.INTERNAL_API


def test_declaring_nothing_changes_nothing():
    empty = build([])
    assert empty.empty
    for addr, port in (("10.0.4.23", 443), ("52.216.10.7", 443), ("160.79.104.10", 443)):
        assert classify_with(empty, addr, port) is not None


def test_one_accounts_declaration_is_not_anothers():
    """The reason this is a value rather than a module global.

    10.0.0.0/8 is where every customer's internal services live. A declaration
    that leaked between accounts would manufacture agents out of an unrelated
    customer's traffic on a coincidental address collision.
    """
    theirs = gateway()
    ours = build([])
    assert classify_with(theirs, "10.0.7.9", 443) is DestinationClass.MODEL
    assert classify_with(ours, "10.0.7.9", 443) is DestinationClass.INTERNAL_API


def test_an_aws_service_can_be_declared():
    d = build([Declaration("SAGEMAKER_RUNTIME", "aws_service", "our fine-tuned model")])
    assert classify_with(d, "10.0.9.1", 443, "SAGEMAKER_RUNTIME") is DestinationClass.MODEL
    assert classify_with(d, "10.0.9.1", 443, "S3") is DestinationClass.DATASTORE


def test_a_single_address_is_a_valid_range():
    d = build([Declaration("10.0.7.9", "range", "the gateway")])
    assert classify_with(d, "10.0.7.9", 443) is DestinationClass.MODEL
    assert classify_with(d, "10.0.7.10", 443) is DestinationClass.INTERNAL_API


def test_an_unparseable_declaration_raises_rather_than_being_skipped():
    """Skipping it would leave a customer believing their gateway was covered
    while their agents stayed invisible — the exact failure this prevents."""
    with pytest.raises(ValueError, match="not a valid network"):
        build([Declaration("10.0.7.0/99", "range", "typo")])


def test_an_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown declaration kind"):
        build([Declaration("10.0.7.0/24", "cidr", "wrong word")])


def test_a_declaration_carries_what_the_customer_called_it():
    # "We classified this as a model because you told us to" is only useful if
    # it also says what they told us it was.
    assert gateway("vllm cluster").note_for("10.0.7.9") == "vllm cluster"
    assert gateway().note_for("10.0.8.9") == ""


def test_a_host_bit_in_a_range_is_accepted_rather_than_refused():
    # Someone will type 10.0.7.5/24. Refusing it teaches them nothing and
    # loses the declaration; strict=False takes the network they meant.
    d = build([Declaration("10.0.7.5/24", "range", "gateway")])
    assert classify_with(d, "10.0.7.200", 443) is DestinationClass.MODEL


def _gateway_traffic(gateway_addr: str):
    """An agent whose every model call goes through an internal gateway.

    The shape the classifier looks for, aimed at a private address on 443:
    heavy egress, light return, no inbound request, a tool call alongside.
    """
    from datetime import UTC, datetime, timedelta

    from custos.telemetry import Direction, FlowRecord

    start = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    out = []
    for minute in range(40):
        at = start + timedelta(minutes=minute)
        out.append(FlowRecord(
            account_id="1", interface_id="eni-1", srcaddr="10.0.1.5",
            dstaddr=gateway_addr, srcport=41000 + minute, dstport=443,
            protocol=6, packets=40, bytes=140_000,
            start=at, end=at + timedelta(seconds=30),
            direction=Direction.EGRESS, tcp_flags=2,
        ))
        out.append(FlowRecord(
            account_id="1", interface_id="eni-1", srcaddr=gateway_addr,
            dstaddr="10.0.1.5", srcport=443, dstport=41000 + minute,
            protocol=6, packets=8, bytes=9_000,
            start=at, end=at + timedelta(seconds=30),
            direction=Direction.INGRESS, tcp_flags=16,
        ))
    return start, out


def test_an_agent_behind_a_gateway_is_invisible_until_it_is_declared():
    """The finding STATUS names as the most likely reason a real scan comes
    back empty, and the reason this module exists.

    Undeclared, every model call looks like traffic to an internal API and the
    workload has no model traffic at all — so it is not an agent, not a review
    candidate, not anything.
    """
    from custos.classify import Disposition
    from custos.scan import ScanInput, run

    start, records = _gateway_traffic("10.0.7.9")
    common = dict(
        account_id="1", start=start, end=start + timedelta(hours=1),
        records=records, principal_by_eni={"eni-1": "arn:aws:iam::1:role/agent"},
    )

    blind = run(ScanInput(**common))
    assert not [v for v in blind.verdicts if v.disposition is Disposition.AGENT]

    told = run(ScanInput(
        **common,
        declared=build([Declaration("10.0.7.0/24", "range", "llm-gateway")]),
    ))
    found = [v for v in told.verdicts if v.disposition is Disposition.AGENT]
    assert found, "declaring the gateway did not make the agent visible"
