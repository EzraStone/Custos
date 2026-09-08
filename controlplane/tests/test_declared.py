"""Endpoints a customer declared, scoped to their account."""

from __future__ import annotations

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
