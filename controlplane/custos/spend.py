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


def estimate_tokens(egress_bytes: int, ingress_bytes: int) -> tuple[float, float]:
    """Return (input_tokens, output_tokens) implied by observed wire bytes."""
    payload_out = max(0.0, egress_bytes * (1 - FRAMING_OVERHEAD))
    payload_in = max(0.0, ingress_bytes * (1 - FRAMING_OVERHEAD))
    return payload_out / BYTES_PER_TOKEN, payload_in / BYTES_PER_TOKEN


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
) -> float:
    """Extrapolate a monthly figure from an observation window.

    Extrapolating three days to thirty multiplies any sampling error by ten.
    That is acceptable for ranking and is why every surface that renders this
    number also renders the observation window beside it.
    """
    if observed_days <= 0:
        return 0.0
    price = (rates or DEFAULT_RATES).for_provider(provider)
    tokens_in, tokens_out = estimate_tokens(egress_bytes, ingress_bytes)
    window_cost = (
        tokens_in / 1_000_000 * price.input_per_mtok
        + tokens_out / 1_000_000 * price.output_per_mtok
    )
    return window_cost * (30.0 / observed_days)


def provider_for(address: str, aws_service: str = "") -> str:
    """Best-effort provider label from what a flow log carries."""
    if aws_service in ("BEDROCK", "SAGEMAKER"):
        return "bedrock"
    if address.startswith("160.79."):
        return "anthropic"
    if address.startswith("104.18."):
        return "openai"
    return "unknown"
