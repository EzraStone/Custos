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
from custos.pipeline import to_scan_input

from . import corpus as corpus_mod
from .batchbridge import build_batch
from .corpus import CorpusSpec
from .scenarios.hard import GATEWAY


@dataclass(frozen=True, slots=True)
class Asked:
    """One question, with the ground truth the detector could not see."""

    candidate: Candidate
    real: bool
    """Whether this address is the corpus's actual model gateway."""

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

    inp = to_scan_input(build_batch(corpus_mod.build(spec)))
    telemetry = sessionize(
        inp.records, inp.principal_by_eni, inp.address_by_eni, inp.requests,
        origin=inp.start, interval=inp.interval,
    )
    # No limit here: the detector's own cut is what `limit` measures, so the
    # scoring has to see the candidates it dropped.
    found = candidates(telemetry, limit=1000)
    return QuestionResult(
        asked=tuple(Asked(candidate=c, real=c.address == GATEWAY.ip) for c in found),
        limit=limit,
    )
