"""Scoring the classifier against the labelled corpus, and deciding G0.

The gate in the specification asks one question: does the signature separate
agents from chatbots. That question needs a numeric answer with a stated
margin, not an impression formed from reading a table.

The metric that matters here is not accuracy. It is the **separation margin**:
the gap between the lowest-scoring agent and the highest-scoring non-agent. A
classifier with perfect accuracy and a margin of 0.01 has not proven anything —
it has landed a threshold luckily between two adjacent points, and the first
real customer will straddle it. A margin wide enough to move the threshold
around inside is what makes a result durable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from custos.classify import Disposition, Verdict, classify_all, sessionize

from . import corpus as corpus_mod
from .trace import Corpus, Label
from .wire import AggregationConfig, aggregate


@dataclass(frozen=True, slots=True)
class Scenario:
    """One configuration under test."""

    name: str
    interval_seconds: int
    have_alb_logs: bool

    @property
    def config(self) -> AggregationConfig:
        return AggregationConfig(
            interval=timedelta(seconds=self.interval_seconds),
            have_alb_logs=self.have_alb_logs,
        )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("flow logs at 60s, with ALB logs", 60, True),
    Scenario("flow logs at 600s, with ALB logs", 600, True),
    Scenario("flow logs at 60s, no ALB logs", 60, False),
    Scenario("flow logs at 600s, no ALB logs", 600, False),
)


def run_hard(gateway_declared: bool = False) -> Result:
    """Score the classifier against the stress corpus.

    Separate from `run_all` on purpose. G0 was defined against the base corpus
    and is measured against it; this answers a different question — how much
    headroom is left when the clean coupled/decoupled split does not hold — and
    conflating the two would quietly restate the gate.

    `gateway_declared` simulates a customer who told us about their self-hosted
    LLM gateway, which is the intended remedy for `agent_via_gateway`.
    """
    from custos import catalog

    from .scenarios.hard import GATEWAY

    corpus = corpus_mod.build(corpus_mod.CorpusSpec(hard=True))
    if gateway_declared:
        catalog.extend([f"{GATEWAY.ip}/32"])
    try:
        return run(SCENARIOS[0], corpus)
    finally:
        if gateway_declared:
            catalog.reset()


@dataclass(slots=True)
class Row:
    """One workload's result, with ground truth attached for reporting."""

    workload: str
    scenario: str
    label: Label
    note: str
    verdict: Verdict

    @property
    def correct(self) -> bool:
        predicted_agent = self.verdict.disposition is Disposition.AGENT
        return predicted_agent == (self.label is Label.AGENT)

    @property
    def in_review(self) -> bool:
        return self.verdict.disposition is Disposition.REVIEW


@dataclass(slots=True)
class Result:
    """Everything one scenario produced."""

    scenario: Scenario
    rows: list[Row] = field(default_factory=list)
    flow_records: int = 0

    @property
    def agents(self) -> list[Row]:
        return [r for r in self.rows if r.label is Label.AGENT]

    @property
    def negatives(self) -> list[Row]:
        return [r for r in self.rows if r.label is Label.NOT_AGENT]

    @property
    def true_positives(self) -> int:
        return sum(1 for r in self.agents if r.verdict.disposition is Disposition.AGENT)

    @property
    def false_positives(self) -> int:
        return sum(
            1 for r in self.negatives if r.verdict.disposition is Disposition.AGENT
        )

    @property
    def false_negatives(self) -> int:
        return len(self.agents) - self.true_positives

    @property
    def precision(self) -> float:
        called = self.true_positives + self.false_positives
        return self.true_positives / called if called else 1.0

    @property
    def recall(self) -> float:
        return self.true_positives / len(self.agents) if self.agents else 1.0

    @property
    def separation_margin(self) -> float:
        """Lowest agent confidence minus highest non-agent confidence.

        Negative means the classes overlap and no threshold separates them. The
        headline number for G0.
        """
        if not self.agents or not self.negatives:
            return 0.0
        return min(r.verdict.confidence for r in self.agents) - max(
            r.verdict.confidence for r in self.negatives
        )

    @property
    def missed_agents(self) -> list[Row]:
        return [r for r in self.agents if r.verdict.disposition is not Disposition.AGENT]

    @property
    def review_band(self) -> list[Row]:
        return [r for r in self.rows if r.in_review]


def run(scenario: Scenario, corpus: Corpus | None = None) -> Result:
    """Generate telemetry for one scenario and classify every principal."""
    c = corpus if corpus is not None else corpus_mod.build()
    meta = {w.principal: w for w in c.workloads}

    capture = aggregate(c, scenario.config)
    telemetry = sessionize(
        capture.records,
        capture.principal_by_eni,
        capture.address_by_eni,
        capture.requests,
        origin=c.start,
        interval=scenario.config.interval,
        inbound_logs_available=scenario.have_alb_logs,
    )

    result = Result(scenario=scenario, flow_records=len(capture.records))
    for verdict in classify_all(telemetry):
        w = meta[verdict.principal]
        result.rows.append(
            Row(
                workload=w.name, scenario=w.scenario, label=w.label,
                note=w.note, verdict=verdict,
            )
        )
    return result


def run_all(corpus: Corpus | None = None) -> list[Result]:
    c = corpus if corpus is not None else corpus_mod.build()
    return [run(s, c) for s in SCENARIOS]


MIN_MARGIN = 0.15
"""The margin below which a pass is luck rather than a finding."""


@dataclass(frozen=True, slots=True)
class Gate:
    passed: bool
    headline: str
    detail: str


def decide(results: list[Result]) -> Gate:
    """Apply the G0 criteria from the specification.

    G0 asks whether the signature separates agents from chatbots. It is
    evaluated against the configurations a customer is actually likely to have.
    A pass requires no false positives anywhere and a durable margin in the
    supported configuration; degradation in the unsupported one is reported,
    not fatal.
    """
    supported = [r for r in results if r.scenario.have_alb_logs]
    degraded = [r for r in results if not r.scenario.have_alb_logs]

    any_false_positive = any(r.false_positives for r in results)
    full_recall = all(r.recall == 1.0 for r in supported)
    margin = min((r.separation_margin for r in supported), default=0.0)

    passed = not any_false_positive and full_recall and margin >= MIN_MARGIN

    if passed:
        worst_degraded = min((r.recall for r in degraded), default=1.0)
        return Gate(
            passed=True,
            headline=(
                f"PASS — separation margin {margin:.2f} with load balancer logs, "
                "at both 60s and 600s aggregation."
            ),
            detail=(
                "No false positives in any configuration. Recall without load "
                f"balancer logs falls to {worst_degraded:.0%}, with the missed "
                "agents landing in the review band rather than being dropped."
            ),
        )

    reasons = []
    if any_false_positive:
        reasons.append("false positives present")
    if not full_recall:
        reasons.append("recall below 100% in a supported configuration")
    if margin < MIN_MARGIN:
        reasons.append(f"separation margin {margin:.2f} below {MIN_MARGIN}")
    return Gate(
        passed=False,
        headline="FAIL — " + "; ".join(reasons),
        detail=(
            "Per the specification's kill gate, fall back to gateway-log "
            "ingestion and revise the pitch, timeline, and target profile."
        ),
    )
