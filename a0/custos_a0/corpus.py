"""Corpus assembly.

One seed produces one corpus, deterministically. Each workload draws from its
own derived seed so that adding, removing, or reordering a scenario does not
perturb the others — without that, every corpus change would invalidate every
previously recorded result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random

from .scenarios import GENERATORS, HARD
from .trace import Corpus


@dataclass(frozen=True, slots=True)
class CorpusSpec:
    seed: int = 20260816
    start: datetime = datetime(2026, 8, 10, tzinfo=UTC)  # a Monday
    days: int = 3

    hard: bool = False
    """Include the workloads that break the clean coupled/decoupled split.

    Off by default so the headline G0 result stays measured against the corpus
    it was originally measured against. Turning it on is how the cost of the
    harder cases becomes a number rather than a silent shift."""

    @property
    def end(self) -> datetime:
        return self.start + timedelta(days=self.days)


DEFAULT = CorpusSpec()


def build(spec: CorpusSpec = DEFAULT) -> Corpus:
    """Generate the labelled corpus."""
    corpus = Corpus(start=spec.start, end=spec.end)

    generators = [*GENERATORS, *HARD] if spec.hard else list(GENERATORS)

    for i, gen in enumerate(generators):
        # Derived per-generator seed: stable under reordering.
        rng = Random(spec.seed + i * 7919)
        w = gen(rng, spec.start, spec.end)
        w.src_ip = f"10.0.{20 + i}.{11 + (i * 37) % 200}"
        w.eni = f"eni-0{0x1a2b3c + i * 1117:09x}"
        w.subnet = f"subnet-0{0xab12 + i:08x}"
        w.sort()
        corpus.workloads.append(w)

    return corpus
