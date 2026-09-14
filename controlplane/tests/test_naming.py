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


# --- where a name came from ---------------------------------------------------
#
# Seven sources name a destination now. Five of them name the thing at the
# address: a tag on its interface, a description AWS wrote for it, the service
# an endpoint is for. Two name something it belongs to, and both can be shared.

def test_a_name_from_a_security_group_says_so():
    """A group called `web-tier` is attached to every web server in the
    account. The label earns its place — an account with no ENI tags has
    nothing else — but an approver should be able to tell it from a label that
    names this host."""
    from custos.naming import describe

    assert describe(
        "10.0.4.50", 443, known={"10.0.4.50": "orders-service"},
        kinds={"10.0.4.50": "security-group"},
    ) == "orders-service 10.0.4.50 (security group)"


def test_a_name_from_an_instance_says_so():
    """An instance can carry several interfaces doing different jobs."""
    from custos.naming import describe

    assert describe(
        "10.0.14.1", 443, known={"10.0.14.1": "checkout-api"},
        kinds={"10.0.14.1": "instance"},
    ) == "checkout-api 10.0.14.1 (instance name)"


def test_a_name_from_the_thing_itself_is_unqualified():
    """Five of the seven sources name the host. Qualifying those would put four
    words on every line of the scope to say nothing."""
    from custos.naming import describe

    for kind in ("tag", "load-balancer", "vpc-endpoint", "ecs", "rds"):
        assert describe(
            "10.0.4.21", 443, known={"10.0.4.21": "billing-api"},
            kinds={"10.0.4.21": kind},
        ) == "billing-api 10.0.4.21"


def test_a_name_with_no_recorded_source_is_unqualified():
    """An older collector sends no kind, and inventing a qualifier for a
    source we do not know would be a claim about provenance we cannot make."""
    from custos.naming import describe

    assert describe(
        "10.0.4.21", 443, known={"10.0.4.21": "billing-api"},
    ) == "billing-api 10.0.4.21"
