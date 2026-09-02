"""Naming a destination for the person who has to approve it."""

from __future__ import annotations

import pytest

from custos.catalog import DATASTORE_PORTS
from custos.naming import PORT_NAMES, describe


def test_the_aws_annotation_wins():
    assert describe("52.216.10.7", 443, "S3") == "s3"


def test_a_private_address_keeps_its_identity_even_when_the_service_is_known():
    # Two RDS instances are two things to approve. Collapsing them to `rds`
    # would hide one behind the other — the same mistake as showing a rotating
    # S3 edge address, in the opposite direction.
    assert describe("10.0.9.44", 5432, "RDS") == "rds 10.0.9.44"
    assert describe("10.0.9.45", 5432, "RDS") == "rds 10.0.9.45"
    assert len({describe(a, 5432, "RDS") for a in ("10.0.9.44", "10.0.9.45")}) == 2


def test_rotating_service_addresses_collapse_to_one_entry():
    # The reason a name beats an address. S3 edge addresses rotate, so an
    # approval recorded against one is stale within days and would have to be
    # re-granted for traffic that did not change.
    scope = {describe(a, 443, "S3") for a in ("52.216.10.7", "52.217.4.19", "3.5.28.100")}
    assert scope == {"s3"}


def test_a_private_datastore_is_named_by_its_port():
    assert describe("10.0.9.44", 5432) == "postgres 10.0.9.44"
    assert describe("10.0.9.45", 6379) == "redis 10.0.9.45"


def test_the_address_stays_on_a_port_named_destination():
    # Two Postgres instances are two things to approve. Collapsing them the way
    # S3 collapses would hide one behind the other, and unlike a service edge
    # these addresses are stable.
    scope = {describe(a, 5432) for a in ("10.0.9.44", "10.0.9.45")}
    assert scope == {"postgres 10.0.9.44", "postgres 10.0.9.45"}


def test_an_mcp_server_says_so():
    assert describe("10.0.5.11", 8931) == "mcp 10.0.5.11"


def test_an_unknown_destination_is_returned_unchanged():
    # An honest address beats an invented name.
    assert describe("10.0.4.23", 443) == "10.0.4.23"
    assert describe("10.0.4.23") == "10.0.4.23"


def test_a_public_address_with_no_annotation_is_not_named():
    assert describe("203.0.113.9", 443) == "203.0.113.9"


@pytest.mark.parametrize("port", sorted(DATASTORE_PORTS))
def test_every_datastore_port_has_a_name(port: int):
    """The port table decides a destination is a datastore. Having decided
    that, refusing to say which kind withholds what was already assumed."""
    assert port in PORT_NAMES, f"port {port} classifies as a datastore but has no name"


def test_naming_never_invents_a_service_for_an_address_it_cannot_place():
    for addr in ("10.0.4.23", "192.168.1.1", "172.16.0.5"):
        assert describe(addr, 443) == addr
