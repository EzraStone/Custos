"""Estimated model spend, derived from byte counts.

A dollar figure per agent does something no other field does: it gets the
report forwarded to someone with a budget. "This unregistered agent costs about
$1,400 a month" reaches a different reader than "this unregistered agent exists."

The estimate is deliberately crude and labelled as such wherever it is
displayed. Bytes are not tokens, tokenisation is model-specific, and cached
input is billed differently. What the estimate is good for is ranking agents
against each other and separating a $50-a-month experiment from a $5,000-a-month
one. It is not an invoice and must never be presented as reconciling to one.

The right fix for that is not for us to guess better. A customer knows what
they pay — enterprise agreements, committed-use discounts, provisioned
throughput — and their own rate is better than any list we could verify. So
`Rates` carries an account's own pricing when they have supplied it, and the
built-in table is what gets used until they do, labelled as unverified
wherever it appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PRICES_REVISION = "unverified-placeholder"
"""The provenance of the built-in table. Deliberately not a date.

These figures are order-of-magnitude placeholders carried so the pipeline is
complete and testable, and every surface that renders a figure derived from
them labels it. An account that supplies its own rates gets its own revision
instead — see `Rates.revision`."""


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


PRICES: dict[str, Price] = {
    "anthropic": Price(input_per_mtok=3.00, output_per_mtok=15.00),
    "openai": Price(input_per_mtok=2.50, output_per_mtok=10.00),
    "bedrock": Price(input_per_mtok=3.00, output_per_mtok=15.00),
    "unknown": Price(input_per_mtok=3.00, output_per_mtok=15.00),
}

BYTES_PER_TOKEN = 4.0
"""Rough for English JSON payloads. Wrong for code, wrong for other languages,
and right enough for ranking."""

FRAMING_OVERHEAD = 0.06
"""TLS records, TCP/IP headers, and handshakes inflate wire bytes above payload
bytes. Subtracted before converting to tokens so the estimate does not drift
upward with connection churn."""

STREAMED_BYTES_PER_TOKEN = 175.0
"""Wire bytes per output token when the response is streamed.

Not a fitted number. A streaming model API flushes after every token — that is
the whole reason to stream — so each token becomes its own Server-Sent Events
frame, its own TLS record and its own TCP segment. Four bytes of text, 114 of
SSE envelope, 29 of record header, 40 of packet headers, less what the
constant-rate ACK accounting on the same records already covers.

The size of the error it fixes is the reason it exists. Dividing a streamed
response by four overstates output tokens by around forty-four times on the A0
corpus, and output tokens are priced at five times input — so a report could
be telling a budget owner that an agent costs ten thousand dollars a month
when it costs a few hundred, with nothing anywhere saying which.

`a0/tests/test_conversion.py` measures it and `test_limits_agree.py` holds this
constant against the corpus's own arithmetic."""

ACK_BYTES = 52.0
"""A pure acknowledgement: IP and TCP headers plus timestamps."""

STREAMED_PACKET_BYTES = 600.0
"""Mean size of an inbound data packet above which responses were not streamed.

The discriminator, and the reason the conversion above can be applied at all —
a flow record does not say whether a response streamed, and every other part of
this module would otherwise be guessing which of two answers forty-four times
apart to give.

It sits in measured empty space. On the A0 corpus the mean inbound data packet
is 1,444 to 2,740 bytes when responses arrive whole and 232 to 288 when they
are streamed, with nothing between. That gap is a consequence of the protocol
rather than of the corpus: a sender filling segments produces packets near the
MSS, and a sender flushing per token produces packets the size of one SSE
frame.

What the corpus cannot establish is the mixture. An account whose agents
stream and whose chatbots do not is two regimes in one number, and this
decides per principal, which is the finest grain a flow log supports."""


def responses_streamed(
    ingress_bytes: int, ingress_packets: int, egress_packets: int
) -> bool:
    """Whether this principal's model responses arrived streamed.

    Inbound packets are not all response data: roughly one in two outbound
    segments is answered by a pure ACK travelling inbound, and for an agent
    sending an accumulating transcript those dominate. Subtracting them is what
    makes the mean packet size mean anything — without it an agent's inbound
    average is dragged to 84 bytes by its own acknowledgements and reads as
    streaming whether it is or not.

    Conservative when it cannot tell. No packet counts, or nothing left after
    the ACKs, returns False — the assumption that produces the larger figure,
    which is the one this product has always given and the one that overstates
    rather than hides a cost.
    """
    if ingress_packets <= 0 or ingress_bytes <= 0:
        return False
    acks = max(0, egress_packets // 2)
    data_packets = ingress_packets - acks
    if data_packets <= 0:
        return False
    data_bytes = max(0.0, ingress_bytes - acks * ACK_BYTES)
    return data_bytes / data_packets < STREAMED_PACKET_BYTES


def estimate_tokens(
    egress_bytes: int, ingress_bytes: int, streamed: bool = False
) -> tuple[float, float]:
    """Return (input_tokens, output_tokens) implied by observed wire bytes.

    The request is one body whichever way the reply comes back, so only the
    output side depends on `streamed`.
    """
    payload_out = max(0.0, egress_bytes * (1 - FRAMING_OVERHEAD))
    payload_in = max(0.0, ingress_bytes * (1 - FRAMING_OVERHEAD))
    per_output = STREAMED_BYTES_PER_TOKEN if streamed else BYTES_PER_TOKEN
    return payload_out / BYTES_PER_TOKEN, payload_in / per_output


@dataclass(frozen=True, slots=True)
class Rates:
    """What one account pays, or the built-in table when they have not said.

    A customer's own rate beats anything we could verify: they have the
    contract. This exists so the dollar figure in a report is theirs rather
    than our approximation of theirs, and so the label above it can say which.
    """

    prices: dict[str, Price] = field(default_factory=lambda: dict(PRICES))
    revision: str = PRICES_REVISION

    @property
    def verified(self) -> bool:
        """Whether these came from someone who knows what they pay."""
        return self.revision != PRICES_REVISION

    def for_provider(self, provider: str) -> Price:
        # An account that supplied rates for Anthropic and not for Bedrock
        # gets its own Anthropic rate and the placeholder for Bedrock, rather
        # than a KeyError or a silent zero.
        return self.prices.get(provider) or PRICES.get(provider, PRICES["unknown"])


DEFAULT_RATES = Rates()


def estimate_monthly_usd(
    egress_bytes: int,
    ingress_bytes: int,
    observed_days: float,
    provider: str = "unknown",
    rates: Rates | None = None,
    streamed: bool = False,
) -> float:
    """Extrapolate a monthly figure from an observation window.

    Extrapolating three days to thirty multiplies any sampling error by ten.
    That is acceptable for ranking and is why every surface that renders this
    number also renders the observation window beside it.
    """
    if observed_days <= 0:
        return 0.0
    price = (rates or DEFAULT_RATES).for_provider(provider)
    tokens_in, tokens_out = estimate_tokens(egress_bytes, ingress_bytes, streamed)
    window_cost = (
        tokens_in / 1_000_000 * price.input_per_mtok
        + tokens_out / 1_000_000 * price.output_per_mtok
    )
    return window_cost * (30.0 / observed_days)


def provider_for(address: str, aws_service: str = "", endpoint_service: str = "") -> str:
    """Best-effort provider label from what a flow log and an ENI carry.

    Three sources, because a model endpoint can be reached three ways. A public
    provider range is recognisable from the address. AWS's own inference
    endpoints are not — 52.94.236.10 is in no range we publish — and are named
    by the flow log's service annotation instead. And an interface VPC endpoint
    has neither: it is a private address in the customer's subnet with no
    annotation at all, and the only thing that says what it is, is the endpoint
    service the collector resolved.

    The label matters beyond the label. It picks the rate card, so an address
    nothing could attribute is an agent whose spend is estimated at a fallback
    rate — and spend is the number on the report that a budget owner acts on.
    """
    if aws_service in ("BEDROCK", "SAGEMAKER"):
        return "bedrock"
    if endpoint_service:
        last = endpoint_service.rsplit(".", 1)[-1]
        if last in ("bedrock-runtime", "bedrock-agent-runtime", "sagemaker-runtime"):
            return "bedrock"
    if address.startswith("160.79."):
        return "anthropic"
    if address.startswith("104.18."):
        return "openai"
    return "unknown"
