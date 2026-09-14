"""Scoring the gateway detector: are the questions it asks worth answering?

The classifier has a gate with a number attached. The gateway detector has
neither, and it is the part of the product a customer touches most directly —
it interrupts a person and asks them to go and check something.

Its failure mode is not a wrong verdict. It is a customer who gets a handful of
questions a week about their log collector, learns that the answer is always
no, and stops reading them — at which point the one question that mattered is
indistinguishable from the rest. That failure is invisible in accuracy terms:
every question is honest, the detector is working exactly as designed, and the
customer is no longer looking.

So the metric is not precision alone. It is:

    asked     how many questions a customer would be shown
    right     how many of those are the destination that is actually a gateway
    rank      where the real one sits in the list they read top-down

A detector that finds the gateway and asks six other things has not found it.
"""

from __future__ import annotations

from dataclasses import dataclass

from custos.classify import sessionize
from custos.gateway import Candidate, candidates
from custos.pipeline import _published_endpoints, to_scan_input

from . import corpus as corpus_mod
from .batchbridge import build_batch
from .corpus import CorpusSpec
from .endpoints import BEDROCK_PRIVATELINK, PROVIDER_PRIVATELINK
from .scenarios.hard import GATEWAY

MODEL_ADDRESSES = frozenset({
    GATEWAY.ip, BEDROCK_PRIVATELINK.ip, PROVIDER_PRIVATELINK.ip,
})
"""Addresses in the corpus that really do carry model traffic.

Three, and only two of them can ever reach the list. The Bedrock interface
endpoint is resolved by the collector before any question is asked, and stays
in this set so that a regression which stops resolving it — and starts asking
about it instead — scores as the near miss it is rather than as a clean
result."""


@dataclass(frozen=True, slots=True)
class Asked:
    """One question, with the ground truth the detector could not see."""

    candidate: Candidate
    real: bool
    """Whether this address really is carrying model traffic.

    Three addresses in the corpus do, and two of them reach this list: the
    self-hosted gateway the mechanism was built for, and the PrivateLink
    service a model provider published, which AWS names with an opaque id and
    will not explain. The third — a Bedrock interface VPC endpoint — is
    resolved by the collector before any question is asked."""

    @property
    def address(self) -> str:
        return self.candidate.address


@dataclass(frozen=True, slots=True)
class QuestionResult:
    asked: tuple[Asked, ...]
    limit: int
    """How many questions a customer is actually shown. A candidate below this
    was computed and never seen, which for this metric is the same as not
    having been found."""

    @property
    def shown(self) -> tuple[Asked, ...]:
        return self.asked[: self.limit]

    @property
    def rank(self) -> int | None:
        """Where the real gateway sits, one-indexed, or None if never asked."""
        for i, a in enumerate(self.asked, start=1):
            if a.real:
                return i
        return None

    @property
    def found(self) -> bool:
        """Whether a customer reading the list they are shown would see it."""
        return any(a.real for a in self.shown)

    @property
    def precision(self) -> float:
        """Of the questions shown, the fraction worth asking."""
        if not self.shown:
            return 1.0
        return sum(1 for a in self.shown if a.real) / len(self.shown)

    @property
    def wasted(self) -> int:
        return sum(1 for a in self.shown if not a.real)


def run(spec: CorpusSpec | None = None, limit: int = 5) -> QuestionResult:
    """Ask the detector about a corpus and score what it asked.

    Defaults to the corpus this measurement exists for: the hard workloads,
    which contain the one real gateway, plus the noise, which contains seven
    ordinary services with the same traffic shape.
    """
    if spec is None:
        spec = CorpusSpec(hard=True, noise=True)

    batch = build_batch(corpus_mod.build(spec))
    inp = to_scan_input(batch)
    # With whatever the pipeline already resolved. An interface VPC endpoint
    # AWS named is not a question any more, and a metric that still counted it
    # as one would reward asking about something we had already answered.
    telemetry = sessionize(
        inp.records, inp.principal_by_eni, inp.address_by_eni, inp.requests,
        origin=inp.start, interval=inp.interval, declared=inp.declared,
    )
    # No limit here: the detector's own cut is what `limit` measures, so the
    # scoring has to see the candidates it dropped.
    found = candidates(
        telemetry, limit=1000,
        # Through the pipeline's own helper rather than a list written here.
        # A0 measuring a mechanism that is not the one shipping is how a gate
        # passes for a reason that does not generalise, and this one decides
        # the order a customer reads the questions in.
        published=_published_endpoints(batch),
    )
    return QuestionResult(
        asked=tuple(
            Asked(candidate=c, real=c.address in MODEL_ADDRESSES) for c in found
        ),
        limit=limit,
    )
