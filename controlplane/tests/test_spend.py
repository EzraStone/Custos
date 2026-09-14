import pytest

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


def test_an_account_rate_beats_the_built_in_table():
    """A customer knows what they pay — enterprise agreements, committed use,
    provisioned throughput — and their own rate is better than any list we
    could verify."""
    from custos.spend import Price, Rates, estimate_monthly_usd

    ours = estimate_monthly_usd(10_000_000, 1_000_000, 30, "anthropic")
    theirs = estimate_monthly_usd(
        10_000_000, 1_000_000, 30, "anthropic",
        rates=Rates(
            prices={"anthropic": Price(input_per_mtok=1.50, output_per_mtok=7.50)},
            revision="customer-supplied 2026-09-08",
        ),
    )
    assert theirs == pytest.approx(ours / 2, rel=1e-6)


def test_a_partial_rate_falls_back_per_provider():
    """An account that priced Anthropic and not Bedrock gets its own rate for
    one and the placeholder for the other, rather than a KeyError or a zero."""
    from custos.spend import PRICES, Price, Rates

    rates = Rates(
        prices={"anthropic": Price(input_per_mtok=1.50, output_per_mtok=7.50)},
        revision="customer-supplied 2026-09-08",
    )
    assert rates.for_provider("anthropic").input_per_mtok == 1.50
    assert rates.for_provider("bedrock") == PRICES["bedrock"]


def test_rates_say_whether_anyone_verified_them():
    """Every surface renders this figure differently depending on the answer,
    so it has to be askable rather than inferred from the numbers."""
    from custos.spend import Rates

    assert Rates().verified is False
    assert Rates(revision="customer-supplied 2026-09-08").verified is True


def test_the_built_in_table_is_still_unverified():
    """Pinned. If this ever reads like a date, somebody has claimed these were
    checked against a provider's pricing page, and that claim needs to be
    deliberate."""
    from custos.spend import PRICES_REVISION

    assert PRICES_REVISION == "unverified-placeholder"


# --- which provider, and therefore whose rate ---------------------------------

def test_bedrock_is_recognised_from_the_flow_logs_annotation():
    """AWS's inference endpoints are in no range Custos publishes. The flow
    log's service annotation is the only thing that names them, and it was
    being dropped — so every Bedrock agent's spend was estimated at the rate
    for a provider nobody could name."""
    from custos.spend import provider_for

    assert provider_for("52.94.236.10", "BEDROCK") == "bedrock"
    assert provider_for("52.94.236.10", "SAGEMAKER") == "bedrock"
    assert provider_for("52.94.236.10") == "unknown"


def test_bedrock_is_recognised_through_an_interface_endpoint():
    """A private address in the customer's own subnet, with no annotation at
    all. The endpoint service the collector resolved is the only source."""
    from custos.spend import provider_for

    assert provider_for(
        "10.0.15.20", "", "com.amazonaws.us-east-1.bedrock-runtime"
    ) == "bedrock"
    assert provider_for(
        "10.0.15.20", "", "com.amazonaws.us-east-1.s3"
    ) == "unknown"


def test_a_customers_bedrock_rate_reaches_a_bedrock_agent():
    """The point of the label. The built-in table happens to price bedrock and
    unknown identically, so the figure does not move on placeholder rates —
    what moves is that an account which supplied its own Bedrock pricing gets
    it applied instead of the fallback."""
    from custos.spend import Price, Rates, estimate_monthly_usd

    theirs = Rates(
        prices={"bedrock": Price(input_per_mtok=0.30, output_per_mtok=1.50)},
        revision="customer-supplied 2026-09-14",
    )
    cheap = estimate_monthly_usd(10_000_000, 1_000_000, 3.0, "bedrock", theirs)
    fallback = estimate_monthly_usd(10_000_000, 1_000_000, 3.0, "unknown", theirs)
    assert cheap < fallback / 5


# --- whether the response streamed -----------------------------------------
#
# Forty-four times separates the two answers, and a flow record does not say
# which. These pin the discriminator that decides it, including the cases
# where it must decline to.


def test_a_whole_response_is_not_read_as_streamed():
    from custos.spend import responses_streamed

    # 2,000 inbound packets near the MSS, and a request small enough that its
    # acknowledgements are a rounding error.
    assert not responses_streamed(
        ingress_bytes=2_000 * 1_400, ingress_packets=2_000, egress_packets=100
    )


def test_a_streamed_response_is():
    from custos.spend import responses_streamed

    # Every token its own frame: 2,000 packets of about 190 bytes.
    assert responses_streamed(
        ingress_bytes=2_000 * 190, ingress_packets=2_000, egress_packets=100
    )


def test_an_agents_own_acknowledgements_do_not_make_it_look_streamed():
    """The case the ACK subtraction exists for, and the one that would have
    been wrong silently.

    An agent resends its accumulated transcript, so it sends far more segments
    than it receives responses. One ACK per two outbound segments travels
    inbound, and those ACKs are 52 bytes: enough of them drag the inbound mean
    below the threshold on their own, and the principal reads as streaming
    whatever its responses actually did.
    """
    from custos.spend import responses_streamed

    data_packets, data_bytes = 400, 400 * 1_400
    ack_packets = 20_000

    assert not responses_streamed(
        ingress_bytes=data_bytes + ack_packets * 52,
        ingress_packets=data_packets + ack_packets,
        egress_packets=ack_packets * 2,
    )
    # Without the correction the same numbers say the opposite, which is the
    # whole point of asserting it here.
    naive = (data_bytes + ack_packets * 52) / (data_packets + ack_packets)
    assert naive < 600


def test_a_record_with_no_packet_counts_is_not_read_as_streamed():
    """An older collector, or a flow log format without the field. The answer
    that overstates a cost is the safe one: it is what this product has always
    said, and a figure that is too high gets questioned while one that is too
    low gets believed."""
    from custos.spend import responses_streamed

    assert not responses_streamed(ingress_bytes=5_000_000, ingress_packets=0,
                                  egress_packets=0)


def test_the_streamed_estimate_is_far_smaller_and_the_input_side_is_untouched():
    from custos.spend import estimate_tokens

    whole_in, whole_out = estimate_tokens(1_000_000, 1_000_000, streamed=False)
    stream_in, stream_out = estimate_tokens(1_000_000, 1_000_000, streamed=True)

    assert whole_in == stream_in, "a request is one body either way"
    assert whole_out / stream_out > 40


def test_the_cheaper_reading_is_the_streamed_one():
    """Stated as money rather than tokens, because that is the number a budget
    owner acts on and the direction of the error matters more than its size."""
    from custos.spend import estimate_monthly_usd

    whole = estimate_monthly_usd(10_000_000, 10_000_000, 3.0, "anthropic")
    streamed = estimate_monthly_usd(10_000_000, 10_000_000, 3.0, "anthropic",
                                    streamed=True)
    assert streamed < whole
    assert whole - streamed > 100
