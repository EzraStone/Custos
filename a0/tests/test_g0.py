"""The G0 result, pinned.

This is a regression test on a business decision. The numbers here were the
basis for proceeding past the gate, so a change that moves them is a change to
the finding and needs to be noticed rather than absorbed.
"""

import pytest

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


def test_ambiguous_workloads_land_in_review(results):
    """SEC-17 in practice.

    The batch summariser and the CI generator are decoupled and high volume but
    are not agents. They must be surfaced for review, never auto-registered and
    never silently dropped.
    """
    r = next(x for x in results if x.scenario.interval_seconds == 60 and x.scenario.have_alb_logs)
    reviewed = {row.workload for row in r.review_band}
    assert reviewed == {"nightly-doc-summariser", "ci-test-generator"}, reviewed


def test_losing_alb_logs_costs_recall_and_not_precision(results):
    """Quantifies what to ask a customer for, and what it buys them."""
    degraded = [r for r in results if not r.scenario.have_alb_logs]
    assert all(r.precision == 1.0 for r in degraded)
    assert all(r.recall < 1.0 for r in degraded)
    # Every missed agent must still surface as a review candidate.
    for r in degraded:
        assert all(row.in_review for row in r.missed_agents)
