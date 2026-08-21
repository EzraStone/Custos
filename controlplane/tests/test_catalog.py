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

def test_a_self_hosted_gateway_can_be_declared_a_model_endpoint():
    """Many teams front every provider behind one internal endpoint, which
    otherwise classifies as an internal API and takes all its model traffic
    with it."""
    import importlib

    from custos import catalog

    importlib.reload(catalog)
    assert catalog.classify("10.9.9.9", 443) is not catalog.DestinationClass.MODEL

    catalog.extend(["10.9.9.0/24"])
    assert catalog.classify("10.9.9.9", 443) is catalog.DestinationClass.MODEL
    importlib.reload(catalog)


def test_extension_does_not_remove_built_in_ranges():
    """A security tool that can be configured blind is worse than one that
    cannot be configured at all."""
    import importlib

    from custos import catalog

    importlib.reload(catalog)
    catalog.extend(["10.9.9.0/24"])
    assert catalog.is_model_endpoint("160.79.104.10")
    importlib.reload(catalog)


def test_invalid_ranges_are_refused_with_the_offending_value():
    import importlib

    import pytest

    from custos import catalog

    importlib.reload(catalog)
    with pytest.raises(ValueError, match="not-a-cidr"):
        catalog.extend(["not-a-cidr"])
    importlib.reload(catalog)


def test_configured_ranges_reflect_extensions():
    """A customer who extended the catalogue should see that in the report
    rather than reading the built-in revision and assuming it was all we used."""
    import importlib

    from custos import catalog

    importlib.reload(catalog)
    before = len(catalog.configured_ranges())
    catalog.extend(["10.9.9.0/24"])
    assert len(catalog.configured_ranges()) == before + 1
    assert "10.9.9.0/24" in catalog.configured_ranges()
    importlib.reload(catalog)
