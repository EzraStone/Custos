
from custos.catalog import DestinationClass, classify, is_model_endpoint, is_private


def test_aws_service_annotation_identifies_bedrock():
    assert is_model_endpoint("10.0.0.5", "BEDROCK")
    assert classify("10.0.0.5", 443, "BEDROCK") is DestinationClass.MODEL


def test_published_ranges_identify_providers():
    assert is_model_endpoint("160.79.104.10")
    assert is_model_endpoint("104.18.7.192")


def test_unknown_public_address_is_external_not_model():
    """A false positive here manufactures an agent finding out of unrelated
    traffic, which is the fastest way to lose trust in an entire report."""
    assert not is_model_endpoint("93.184.216.34")
    assert classify("93.184.216.34", 443) is DestinationClass.EXTERNAL


def test_internal_ports_separate_service_classes():
    assert classify("10.0.9.44", 5432) is DestinationClass.DATASTORE
    assert classify("10.0.5.11", 8931) is DestinationClass.MCP
    assert classify("10.0.4.21", 8080) is DestinationClass.INTERNAL_API


def test_shared_ports_do_not_claim_mcp():
    """8080 and 3000 are far too common to assert MCP on port alone."""
    assert classify("10.0.4.22", 8080) is not DestinationClass.MCP
    assert classify("10.0.4.22", 3000) is not DestinationClass.MCP


def test_s3_is_a_datastore_not_external():
    assert classify("52.216.10.7", 443, "S3") is DestinationClass.DATASTORE


def test_private_detection():
    assert is_private("10.0.0.1")
    assert is_private("192.168.1.1")
    assert not is_private("8.8.8.8")
    assert not is_private("not-an-address")


# --- extension ----------------------------------------------------------------


# --- IPv6, which the catalogue cannot speak to --------------------------------


def test_the_model_ranges_are_all_ipv4():
    """Not an assertion about correctness — an assertion that the blind spot
    the report describes is the blind spot that exists. If a v6 range is ever
    added, the report's claim becomes false and this fails first."""
    import ipaddress

    from custos.catalog import MODEL_RANGES

    for cidr in MODEL_RANGES:
        assert ipaddress.ip_network(cidr).version == 4, cidr


def test_a_provider_reached_over_ipv6_is_not_recognised():
    """The blind spot itself, stated as a test so it cannot be forgotten. An
    agent whose model calls go over IPv6 produces no finding at all."""
    from custos.catalog import is_model_endpoint

    assert not is_model_endpoint("2606:4700::6810:85e5")


def test_ipv6_is_a_question_about_form_not_a_classification():
    from custos.catalog import is_ipv6

    assert is_ipv6("2606:4700::6810:85e5")
    assert is_ipv6("fd00::1")
    assert not is_ipv6("160.79.104.10")
    assert not is_ipv6("not-an-address")


def test_private_ipv6_is_private():
    """A dual-stack VPC's internal traffic must not be counted as an
    unclassifiable public destination — it classifies fine."""
    from custos.catalog import DestinationClass, classify, is_private

    assert is_private("fd00::1")
    assert classify("fd00::1", 8931) is DestinationClass.MCP
