"""Which signal is actually carrying the result?

The classifier sums five weighted signals. The weights were measured against
this corpus and are recorded in `docs/A0-FINDINGS.md`, but the weight of a
signal is not the same question as how much work it does: a signal can carry a
large weight and fire identically on both classes, in which case it contributes
nothing to the separation and the result rests on the others.

That is not hypothetical here. `egress_asymmetry` — the signal the
specification was rewritten around — was measured on wire bytes for a year, and
on a capture from an account whose clients stream it inverted: agents read below
1:1, the shape of a chatbot. The verdicts stayed correct because the other four
carried them, and every recorded number looked fine.

An ablation would have shown it. Remove a signal and re-measure: if the margin
does not move, that signal was not doing the work its weight implies, and the
reason is worth knowing before a customer finds it.

This measures rather than tunes. Nothing here changes a weight — refitting five
weights against eleven workloads is how a corpus becomes a model of itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from custos.classify import engine as engine_mod

from .evaluate import Result, Scenario, run
from .trace import Corpus


@dataclass(frozen=True, slots=True)
class Ablated:
    """What the classifier does with one signal taken out."""

    removed: str
    weight: float
    margin: float
    recall: float
    precision: float
    surfaced_recall: float
    dropped: tuple[str, ...]
    baseline_margin: float
    baseline_surfaced: float

    @property
    def margin_cost(self) -> float:
        """How much separation this signal was providing.

        Negative means removing it *helped*, which is a signal that is
        actively working against the result on this corpus."""
        return self.baseline_margin - self.margin

    @property
    def surfaced_cost(self) -> float:
        """The fraction of true agents that stop reaching a human without it.

        The column this table was missing, and the reason it was missing is
        instructive: margin, recall and precision are all measured at the
        register boundary, so a signal whose whole job is to hold a workload
        in the review queue does its job invisibly and reads as free.

        `mcp_fingerprint` was exactly that. It cost 0.000 of margin and 0.00
        of recall on both corpora, and removing it dropped an agent out of
        the queue entirely — a signal carrying a real verdict while the
        measurement built to find free signals called it free.
        """
        return self.baseline_surfaced - self.surfaced_recall

    @property
    def load_bearing(self) -> bool:
        """Whether anything depends on it: separation, a verdict, or a queue."""
        return (
            self.margin_cost > 0.01
            or self.surfaced_cost > 0.01
            or self.recall < 1.0
            or self.precision < 1.0
        )


def _without(table, signal_id: str):
    """`table` with one entry removed.

    Takes the table rather than reading the module's current one, which was the
    second bug in this file and the more dangerous of the two. Reading
    `engine.SIGNALS` inside the loop meant each iteration removed a signal from
    the previous iteration's table, so by the fifth pass the classifier had one
    signal left. It produced a confident table of plausible-looking numbers.

    Patched on `engine` rather than on `signals`, which is not a detail. The
    engine does `from .signals import SIGNALS`, so it holds its own reference
    and rebinding the name in `signals` changes nothing it reads. The first
    version of this module did exactly that and reported every signal as
    costing 0.000 — a clean table of nothing, which is what a broken
    measurement looks like when it has no way to say it failed.

    Patched rather than parameterised through `score`, because the point is to
    measure the shipping classifier: a scoring path only the measurement uses
    is a path the measurement is not measuring.
    """
    return tuple(s for s in table if s.id != signal_id)


def run_ablation(
    corpus: Corpus, scenario: Scenario, declared=None
) -> list[Ablated]:
    """Score the corpus once per signal, each time without that signal."""
    baseline: Result = run(scenario, corpus, declared)
    baseline_dismissed = {r.workload for r in baseline.dismissed_agents}
    original = engine_mod.SIGNALS

    out = []
    try:
        for signal in original:
            engine_mod.SIGNALS = _without(original, signal.id)
            result = run(scenario, corpus, declared)
            out.append(Ablated(
                removed=signal.id, weight=signal.weight,
                margin=result.separation_margin,
                recall=result.recall, precision=result.precision,
                surfaced_recall=result.surfaced_recall,
                dropped=tuple(
                    r.workload for r in result.dismissed_agents
                    if r.workload not in baseline_dismissed
                ),
                baseline_margin=baseline.separation_margin,
                baseline_surfaced=baseline.surfaced_recall,
            ))
    finally:
        engine_mod.SIGNALS = original

    out.sort(key=lambda a: (-a.surfaced_cost, -a.margin_cost))
    return out
