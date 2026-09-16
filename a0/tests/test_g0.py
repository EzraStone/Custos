"""The G0 result, pinned.

This is a regression test on a business decision. The numbers here were the
basis for proceeding past the gate, so a change that moves them is a change to
the finding and needs to be noticed rather than absorbed.
"""

import pytest
from custos.classify import AGENT_THRESHOLD

from custos_a0.evaluate import MIN_MARGIN, SCENARIOS, decide, run, run_all
from custos_a0.trace import Label


@pytest.fixture(scope="module")
def results():
    return run_all()


def test_g0_passes(results):
    assert decide(results).passed


def test_no_false_positives_in_any_configuration(results):
    for r in results:
        assert r.false_positives == 0, (r.scenario.name, r.false_positives)


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.have_alb_logs])
def test_full_recall_with_alb_logs(scenario):
    r = run(scenario)
    assert r.recall == 1.0, [x.workload for x in r.missed_agents]


def test_separation_margin_is_durable(results):
    supported = [r for r in results if r.scenario.have_alb_logs]
    assert min(r.separation_margin for r in supported) >= MIN_MARGIN


def test_classifier_is_invariant_to_aggregation_interval(results):
    """The commercial point of the whole aggregation model.

    If results changed between 60s and 600s, onboarding would have to include
    'reconfigure your flow logs', which is a change request against a
    production VPC and a week of delay per customer.
    """
    by_interval = {
        r.scenario.interval_seconds: r
        for r in results
        if r.scenario.have_alb_logs
    }
    a, b = by_interval[60], by_interval[600]
    assert a.recall == b.recall
    assert a.precision == b.precision
    assert abs(a.separation_margin - b.separation_margin) < 0.02


def test_the_recall_case_is_caught(results):
    """finance-close-agent: three runs a day, write reach to billing.

    The low-volume agent is the one that matters commercially, and a classifier
    tuned on busy workloads will miss it. If this test fails, the product finds
    the agents nobody was worried about and misses the one they should be.
    """
    for r in results:
        if not r.scenario.have_alb_logs:
            continue
        row = next(x for x in r.rows if x.workload == "finance-close-agent")
        assert row.correct, (r.scenario.name, row.verdict.confidence)


def test_the_hard_negatives_are_not_called_agents(results):
    """The multi-turn chatbot and the RAG assistant.

    Both are built to defeat a naive signal. A false positive on either would
    mean the classifier is pattern-matching on context growth or tool
    interleave alone.
    """
    for r in results:
        for name in ("sales-copilot-web", "kb-assistant"):
            row = next(x for x in r.rows if x.workload == name)
            assert row.label is Label.NOT_AGENT
            assert row.correct, (r.scenario.name, name, row.verdict.confidence)


def test_the_ambiguous_workload_lands_in_review(results):
    """SEC-17: a workload the classifier cannot decide is offered to a human
    rather than guessed at.

    One rather than two since `offhours_activity` was removed. The CI pipeline
    that used to sit at 0.430 now sits at 0.390, a hundredth below the review
    threshold, and that is the cost of that removal rather than a better
    verdict — it is the same workload, no more decidable than it was.
    """
    primary = next(
        r for r in results if r.scenario.have_alb_logs and r.scenario.interval_seconds == 60
    )
    review = {
        row.workload for row in primary.rows
        if row.verdict.disposition.value == "review"
    }
    assert review == {"nightly-doc-summariser"}, review


def test_losing_alb_logs_costs_recall_and_not_precision(results):
    """Quantifies what to ask a customer for, and what it buys them."""
    degraded = [r for r in results if not r.scenario.have_alb_logs]
    assert all(r.precision == 1.0 for r in degraded)
    assert all(r.recall < 1.0 for r in degraded)
    # Every missed agent must still surface as a review candidate.
    for r in degraded:
        assert all(row.in_review for row in r.missed_agents)


def test_the_gate_fails_when_an_agent_sits_on_the_threshold(results):
    """The failure the headroom bar exists for, constructed rather than waited
    for.

    Every agent at 0.81 and every negative at 0.10: a margin of 0.71, which is
    nearly three times what the corpus actually produces, and a weakest agent
    one hundredth above the line at which it stops being reported. Every other
    criterion in this gate passes. The margin is not merely uninformative here,
    it is actively reassuring, which is why the second number had to exist.
    """
    from dataclasses import replace

    from custos_a0.evaluate import decide

    def flatten(row):
        confidence = 0.81 if row.label is Label.AGENT else 0.10
        return replace(row, verdict=replace(row.verdict, confidence=confidence))

    weakened = [replace(r, rows=[flatten(row) for row in r.rows]) for r in results]

    gate = decide(weakened)
    assert not gate.passed
    # The only reason. A wide margin cannot rescue it and must not mask it.
    assert gate.headline == (
        "FAIL — the weakest agent clears the reporting threshold by only "
        "0.01, against 0.05"
    ), gate.headline


def test_streaming_moves_nothing(results):
    """The property this product needs and did not have.

    Whether a customer's model client streams is a line in their code. It is
    not a logging choice onboarding can ask them to change, so the classifier
    has to read the same either way — the same requirement Finding 3 settled
    for the aggregation interval.

    It used to move the margin from 0.260 to 0.371 and the headroom from 0.151
    to 0.054, which looked in the sweep like an improvement and was an agent
    being pushed toward the threshold at which it stops being reported.
    """
    plain = next(
        r for r in results if r.scenario.name.startswith("flow logs at 60s, with")
    )
    stream = next(
        r for r in results if r.scenario.streaming and r.scenario.interval_seconds == 60
    )

    assert abs(stream.separation_margin - plain.separation_margin) < 0.02
    assert abs(stream.agent_headroom - plain.agent_headroom) < 0.02
    assert stream.recall == 1.0 and stream.precision == 1.0


def test_the_review_threshold_clearance_is_recorded_not_asserted(results):
    """The third number, pinned where it is so a change to it is deliberate.

    It is 0.010 on every supported configuration: one workload sits a
    hundredth from the line at which a human is asked to look. That is not
    good, and the honest treatment is to print it rather than to move the
    threshold until it looks better — moving a threshold to put a workload
    where you would like it is fitting to the corpus, and the workload in
    question is genuinely undecidable either side of it.

    Bounded above as well as below. A clearance that grew would mean the
    negatives had moved away from the band, which is worth noticing for the
    same reason a margin that grew is.
    """
    supported = [r for r in results if r.scenario.have_alb_logs]
    for r in supported:
        assert 0.005 < r.review_clearance < 0.05, (
            r.scenario.name, r.review_clearance
        )


def test_the_threshold_comments_match_the_corpus(results):
    """Two places that have to agree: the figures written beside the thresholds
    and what the corpus actually produces.

    The predecessor of this comment said "every agent above 0.95, every clear
    negative below 0.31, the two ambiguous workloads at 0.52 and 0.69". Three
    of those four numbers were stale and nothing noticed, because a comment is
    not checked by anything.
    """
    import re

    from custos.classify import engine

    primary = next(
        r for r in results if r.scenario.have_alb_logs and r.scenario.interval_seconds == 60
    )
    agents = [row.verdict.confidence for row in primary.agents]
    negatives = [row.verdict.confidence for row in primary.negatives]

    doc = _doc(engine, "AGENT_THRESHOLD")
    claimed_floor = float(re.search(r"at or above ([0-9]+\.[0-9]+)", doc).group(1))
    claimed_ceiling = float(re.search(r"highest negative sits at ([0-9]+\.[0-9]+)", doc).group(1))

    assert min(agents) >= claimed_floor, (min(agents), claimed_floor)
    assert abs(max(negatives) - claimed_ceiling) < 0.01, (max(negatives), claimed_ceiling)
    assert claimed_ceiling < AGENT_THRESHOLD < claimed_floor


def _doc(module, name: str) -> str:
    """The docstring attached to a module-level constant.

    Constants do not carry `__doc__`, so it has to be read out of the source.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(module))
    found = False
    for node in tree.body:
        if found and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return node.value.value
        found = (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == name for t in node.targets)
        )
    raise AssertionError(f"{name} has no docstring")


def test_recall_and_surfaced_recall_agree_when_nothing_is_missed(results):
    """On the base corpus the two metrics are the same number.

    Which is why the distinction was invisible for as long as it was: a
    metric only earns its place on the corpus that can tell it apart from
    the one it refines.
    """
    for r in results:
        if r.recall == 1.0:
            assert r.surfaced_recall == 1.0
            assert r.dismissed_agents == []


def test_degraded_recall_costs_the_register_and_not_the_queue(results):
    """The claim `decide` rests on, as a number rather than a sentence.

    Without load balancer logs recall falls, and the gate reports that as
    survivable because the agents it drops still reach a human. That is
    exactly `surfaced_recall == 1.0`, so assert the number the prose means.
    """
    degraded = [r for r in results if not r.scenario.have_alb_logs]
    assert degraded
    assert all(r.recall < 1.0 for r in degraded)
    assert all(r.surfaced_recall == 1.0 for r in degraded)


def test_surfaced_recall_is_never_below_recall(results):
    """A definitional property, asserted because it is easy to break.

    `surfaced` counts a superset of what `recall` counts. If a refactor ever
    makes this fail, one of the two is counting the wrong dispositions and
    the more useful of them is the one that would be believed.
    """
    for r in results:
        assert r.surfaced_recall >= r.recall


def test_the_gate_fails_when_a_degraded_configuration_drops_an_agent(results):
    """The failure the surfaced bar exists for, constructed rather than waited
    for.

    The supported configurations are left exactly as they are: full recall, a
    margin of 0.485, the weakest agent well clear of the threshold. Every
    number the gate printed before this criterion existed is unchanged and
    every one of them is good.

    What changes is the configuration nobody is asked to pass. Without load
    balancer logs the agents that fall out of the register are pushed below
    the review threshold as well, so an operator on that account is told
    nothing about them at all. The old gate passed this and printed a sentence
    saying those agents land in the review band, which would now be false.
    """
    from dataclasses import replace

    from custos.classify import Disposition

    from custos_a0.evaluate import decide

    def drop(row):
        if row.label is not Label.AGENT or row.verdict.disposition is Disposition.AGENT:
            return row
        return replace(
            row,
            verdict=replace(
                row.verdict, confidence=0.10, disposition=Disposition.NOT_AGENT
            ),
        )

    weakened = [
        r if r.scenario.have_alb_logs
        else replace(r, rows=[drop(row) for row in r.rows])
        for r in results
    ]

    gate = decide(weakened)
    assert not gate.passed
    assert "are dismissed, not queued" in gate.headline, gate.headline


def test_the_pass_narrative_quotes_the_number_it_rests_on(results):
    """The sentence and the criterion cannot drift apart.

    The claim that degraded recall is survivable is the reason the gate
    tolerates it. If that ever becomes decoration again — a sentence with no
    number behind it — this fails.
    """
    from custos_a0.evaluate import decide

    gate = decide(results)
    assert gate.passed
    degraded = [r for r in results if not r.scenario.have_alb_logs]
    surfaced = min(r.surfaced_recall for r in degraded)
    assert f"({surfaced:.0%} surfaced)" in gate.detail, gate.detail
