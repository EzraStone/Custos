"""The numbers in `CANDIDATES`, recomputed.

`custos.classify.signals.CANDIDATES` holds signals that were measured, look
promising, and have not earned a weight. The category exists so that knowledge
is not lost between "shipped" and "rejected" — and the entry in it quotes six
figures that nothing in this repository produces. They were measured once, by
hand, and written into a docstring.

That is the failure mode this codebase has found more of than any other: a
claim in one place with nothing obliging a second place to match. A rejected
signal's reasons can afford to be prose, because nobody is going to act on
them. A candidate's cannot — the whole point of the category is that somebody
will pick it up later and decide, and they will decide against these numbers.

So: compute it, print it, and let a test hold the docstring to it.

The measurement is the coefficient of variation of model egress per inbound
request, across a principal's model-active windows. The idea is that a chatbot
does a fixed amount of work per request — embed, retrieve, generate, stop —
while an agent loops until it decides it is done.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from custos.classify import classify_all, sessionize
from custos.classify.episodes import PrincipalTelemetry
from custos.classify.features import looks_interactive

from . import corpus as corpus_mod
from .evaluate import SCENARIOS, stress_declarations
from .trace import Label
from .wire import aggregate


def work_per_request_variance(t: PrincipalTelemetry) -> float:
    """Coefficient of variation of model egress per inbound request.

    Zero when there is nothing to vary — fewer than two model-active windows,
    or a mean of zero. A coefficient of variation divides by the mean, and a
    denominator that reaches zero here does not raise, it returns a confident
    number at the edge of its range.
    """
    windows = t.model_windows
    if len(windows) < 2:
        return 0.0

    interval = t.interval
    origin = t.windows[0].start if t.windows else None
    per_window: dict = {}
    for r in t.inbound:
        start = r.at - ((r.at - origin) % interval) if origin else r.at
        per_window[start] = per_window.get(start, 0) + 1

    work = [w.model_egress / max(per_window.get(w.start, 0), 1) for w in windows]
    mean = statistics.fmean(work)
    if mean <= 0:
        return 0.0
    return statistics.pstdev(work) / mean


@dataclass(frozen=True, slots=True)
class Row:
    workload: str
    label: Label
    variance: float
    interactive: bool


def measure(hard: bool = True) -> list[Row]:
    """Every workload in a corpus, with its variance and whether it is scoped in."""
    corpus = corpus_mod.build(corpus_mod.CorpusSpec(hard=hard))
    meta = {w.principal: w for w in corpus.workloads}
    scenario = SCENARIOS[0]

    capture = aggregate(corpus, scenario.config)
    telemetry = sessionize(
        capture.records, capture.principal_by_eni, capture.address_by_eni,
        capture.requests, origin=corpus.start, interval=scenario.config.interval,
        inbound_logs_available=scenario.have_alb_logs,
        declared=stress_declarations() if hard else None,
    )
    by_principal = {t.principal: t for t in telemetry}

    rows = []
    for verdict in classify_all(telemetry):
        t = by_principal[verdict.principal]
        rows.append(Row(
            workload=meta[verdict.principal].name,
            label=meta[verdict.principal].label,
            variance=work_per_request_variance(t),
            interactive=looks_interactive(verdict.features),
        ))
    rows.sort(key=lambda r: -r.variance)
    return rows


def separation(rows: list[Row]) -> float:
    """Lowest agent minus highest non-agent, over whatever rows are passed.

    The same statistic the classifier is judged on, so a candidate measured
    here and a signal measured there are answering the same question.
    """
    agents = [r.variance for r in rows if r.label is Label.AGENT]
    others = [r.variance for r in rows if r.label is not Label.AGENT]
    if not agents or not others:
        return 0.0
    return min(agents) - max(others)
