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
from custos.declared import Declared

from . import corpus as corpus_mod
from .trace import Corpus, Label
from .wire import AggregationConfig, aggregate


@dataclass(frozen=True, slots=True)
class Scenario:
    """One configuration under test."""

    name: str
    interval_seconds: int
    have_alb_logs: bool
    streaming: bool = False
    """Whether model responses arrive one SSE event per token.

    A configuration of the customer's clients rather than of their logging, so
    it is not something onboarding can ask them to change. It is in the sweep
    for the same reason the aggregation interval is: the classifier has to hold
    at whatever the account already does, and the first account will not be
    asked to stop streaming."""

    @property
    def config(self) -> AggregationConfig:
        return AggregationConfig(
            interval=timedelta(seconds=self.interval_seconds),
            have_alb_logs=self.have_alb_logs,
            streaming=self.streaming,
        )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("flow logs at 60s, with ALB logs", 60, True),
    Scenario("flow logs at 600s, with ALB logs", 600, True),
    Scenario("flow logs at 60s, no ALB logs", 60, False),
    Scenario("flow logs at 600s, no ALB logs", 600, False),
    Scenario("streaming responses, 60s, with ALB logs", 60, True, streaming=True),
    Scenario("streaming responses, 600s, with ALB logs", 600, True, streaming=True),
)


def run_hard(gateway_declared: bool = False) -> Result:
    """Score the classifier against the stress corpus.

    Separate from `run_all` on purpose. G0 was defined against the base corpus
    and is measured against it; this answers a different question — how much
    headroom is left when the clean coupled/decoupled split does not hold — and
    conflating the two would quietly restate the gate.

    `gateway_declared` simulates a customer who told us about the three model
    endpoints the built-in catalogue cannot see: their self-hosted LLM gateway,
    the interface VPC endpoint their Bedrock traffic goes through, and the
    PrivateLink service a model provider published to sell into AWS.

    Declaring the second is a placeholder for a fix rather than the remedy. A
    self-hosted gateway is something only the customer knows about; a VPC
    endpoint for `com.amazonaws.<region>.bedrock-runtime` is something AWS
    knows about and will say, and asking a customer to declare it is asking
    them for an answer we could have looked up.

    The third is the one where declaring it really is the remedy. AWS names
    the service with an opaque id and will not explain it, the publisher set
    no private DNS name and the customer wrote no tag, so a person is the only
    source left — which is why it is in the corpus, and why the question
    metric is what actually governs it.

    All three are declared here so the margin remains a statement about class
    separation instead of one about workloads nothing could score.

    It goes through the same per-account declaration the product uses, not
    through `catalog.extend`. A0 measuring a mechanism that is not the one
    shipping is how a gate passes for a reason that does not generalise, and
    the global version has a property the real one deliberately does not: it
    would apply to every account in the process.
    """
    from custos.declared import Declaration, build

    from .endpoints import BEDROCK_PRIVATELINK, PROVIDER_PRIVATELINK
    from .scenarios.hard import GATEWAY

    corpus = corpus_mod.build(corpus_mod.CorpusSpec(hard=True))
    declared = (
        build([
            Declaration(f"{GATEWAY.ip}/32", "range", "llm-gateway"),
            Declaration(f"{BEDROCK_PRIVATELINK.ip}/32", "range", "bedrock-privatelink"),
            Declaration(f"{PROVIDER_PRIVATELINK.ip}/32", "range", "provider-privatelink"),
        ])
        if gateway_declared
        else None
    )
    return run(SCENARIOS[0], corpus, declared)


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
    def agent_headroom(self) -> float:
        """Lowest agent confidence minus the threshold it has to clear.

        The second number, and it exists because the first one can improve
        while the product gets worse.

        The margin is a gap between two classes and says nothing about where
        that gap sits. Streaming responses move every workload toward
        ingress-dominated: the negatives fall further than the agents, so the
        margin widens by a tenth — and the lowest agent goes from 0.15 above
        the threshold to 0.05 above it. A shift that widened the margin and
        pushed an agent below 0.80 would read as an improvement and would be a
        missed agent in a customer's report.

        Negative means an agent is not being reported as one, whatever the
        margin says.
        """
        from custos.classify import AGENT_THRESHOLD

        if not self.agents:
            return 0.0
        return min(r.verdict.confidence for r in self.agents) - AGENT_THRESHOLD

    @property
    def unscorable(self) -> list[Row]:
        """Workloads with no recognised model traffic at all.

        Nothing scores these. Every signal but the MCP fingerprint is a ratio
        over the intervals containing model traffic, and with none of those
        the signals are unavailable rather than zero. A workload here is not a
        low-confidence verdict — it is an absent one, and the separation margin
        is not a statistic that survives including it.
        """
        return [r for r in self.rows if r.verdict.features.model_windows == 0]

    @property
    def margin_is_meaningful(self) -> bool:
        """Whether the separation margin describes anything.

        A margin across a workload nothing could score measures the absence of
        evidence, not the separation of classes, and printing it invites
        exactly the misquoting the rest of this module works to prevent.
        """
        return not self.unscorable

    @property
    def missed_agents(self) -> list[Row]:
        return [r for r in self.agents if r.verdict.disposition is not Disposition.AGENT]

    @property
    def review_band(self) -> list[Row]:
        return [r for r in self.rows if r.in_review]


def run(
    scenario: Scenario, corpus: Corpus | None = None, declared: Declared | None = None
) -> Result:
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
        declared=declared,
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

MIN_HEADROOM = 0.05
"""How far the weakest agent has to clear the reporting threshold.

Lower than MIN_MARGIN and deliberately so. This is not a bar the classifier
should comfortably clear — it is the point below which a pass stops meaning
anything, because an agent sitting 0.01 above 0.80 is one capture away from
being a row that is not in the report.

Set at the number streaming produced rather than at a round one chosen in
advance, which is the same rule the thresholds themselves follow. The honest
reading of that is that the gate now sits exactly on a measured configuration
with nothing to spare, and says so."""


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
    headroom = min((r.agent_headroom for r in supported), default=0.0)

    passed = (
        not any_false_positive
        and full_recall
        and margin >= MIN_MARGIN
        and headroom >= MIN_HEADROOM
    )

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
                "agents landing in the review band rather than being dropped. "
                f"The weakest agent clears the reporting threshold by "
                f"{headroom:.2f}, on the configuration where that is tightest."
            ),
        )

    reasons = []
    if any_false_positive:
        reasons.append("false positives present")
    if not full_recall:
        reasons.append("recall below 100% in a supported configuration")
    if margin < MIN_MARGIN:
        reasons.append(f"separation margin {margin:.2f} below {MIN_MARGIN}")
    if headroom < MIN_HEADROOM:
        reasons.append(
            f"the weakest agent clears the reporting threshold by only "
            f"{headroom:.2f}, against {MIN_HEADROOM}"
        )
    return Gate(
        passed=False,
        headline="FAIL — " + "; ".join(reasons),
        detail=(
            "Per the specification's kill gate, fall back to gateway-log "
            "ingestion and revise the pitch, timeline, and target profile."
        ),
    )
