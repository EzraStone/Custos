from custos.spend import (
    PRICES_REVISION,
    estimate_monthly_usd,
    estimate_tokens,
    provider_for,
)


def test_tokens_scale_with_bytes_and_discount_framing():
    tin, tout = estimate_tokens(4_000_000, 400_000)
    assert 900_000 < tin < 1_000_000
    assert 90_000 < tout < 100_000


def test_monthly_extrapolation_scales_from_the_window():
    three_days = estimate_monthly_usd(10**9, 10**8, observed_days=3)
    thirty_days = estimate_monthly_usd(10**10, 10**9, observed_days=30)
    assert abs(three_days - thirty_days) / thirty_days < 0.01


def test_zero_window_yields_zero_rather_than_dividing_by_zero():
    assert estimate_monthly_usd(10**9, 10**8, observed_days=0) == 0.0


def test_output_tokens_cost_more_than_input():
    output_heavy = estimate_monthly_usd(0, 10**8, observed_days=3)
    input_heavy = estimate_monthly_usd(10**8, 0, observed_days=3)
    assert output_heavy > input_heavy


def test_unknown_provider_falls_back_rather_than_failing():
    assert estimate_monthly_usd(10**8, 10**7, 3, provider="nonesuch") > 0


def test_provider_detection():
    assert provider_for("160.79.104.10") == "anthropic"
    assert provider_for("104.18.7.192") == "openai"
    assert provider_for("10.0.0.1", "BEDROCK") == "bedrock"
    assert provider_for("93.184.216.34") == "unknown"


def test_prices_are_flagged_as_unverified_until_someone_verifies_them():
    """Guards against a placeholder figure reaching a customer unlabelled.

    When the prices are verified, set PRICES_REVISION to the date and update
    this test to assert the new value.
    """
    assert PRICES_REVISION == "unverified-placeholder"
