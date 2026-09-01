"""The stress corpus.

G0 was defined against the base corpus and is measured there. This answers a
different question: how much headroom is left when the clean coupled/decoupled
split does not hold. Conflating the two would quietly restate the gate.
"""

import pytest

from custos_a0.evaluate import run_hard
from custos_a0.trace import Label


@pytest.fixture(scope="module")
def built_in():
    return run_hard(gateway_declared=False)


@pytest.fixture(scope="module")
def extended():
    return run_hard(gateway_declared=True)


def _row(result, workload):
    return next(r for r in result.rows if r.workload == workload)


# Partial coupling dilutes the strongest signal rather than removing it. This
# shape is common in exactly the workflows customers most want governed.
def test_a_human_in_the_loop_agent_is_still_caught(built_in):
    row = _row(built_in, "refund-approval-agent")
    assert row.label is Label.AGENT
    assert row.correct, row.verdict.confidence


def test_partial_coupling_costs_confidence_without_costing_the_verdict(built_in):
    """It should score lower than a fully decoupled agent — if it did not, the
    coupling signal would not be doing anything."""
    partial = _row(built_in, "refund-approval-agent").verdict.confidence
    decoupled = _row(built_in, "autofix-coding-agent").verdict.confidence
    assert partial < decoupled
    assert partial >= 0.80


# A classifier separating on schedule gets exactly one of these two wrong.
def test_an_agentic_batch_job_separates_from_a_non_agentic_one(built_in):
    agentic = _row(built_in, "nightly-reconciliation-agent")
    plain = _row(built_in, "nightly-doc-summariser")

    assert agentic.label is Label.AGENT and agentic.correct
    assert plain.label is Label.NOT_AGENT and plain.correct
    assert agentic.verdict.confidence > plain.verdict.confidence


def test_a_function_calling_chatbot_is_not_an_agent(built_in):
    """Tool interleave alone must not carry a verdict."""
    row = _row(built_in, "ops-assistant-web")
    assert row.label is Label.NOT_AGENT
    assert row.correct


# The expected miss, recorded rather than hidden. A model endpoint we cannot
# recognise is an agent we cannot see, and no amount of classifier tuning fixes
# that — only telling the classifier where to look does.
def test_an_agent_behind_an_unknown_gateway_is_missed(built_in):
    row = _row(built_in, "deploy-remediation-agent")
    assert row.label is Label.AGENT
    assert not row.correct
    assert row.in_review, "it must still surface for review rather than vanish"


def test_declaring_the_gateway_recovers_it(extended):
    row = _row(extended, "deploy-remediation-agent")
    assert row.correct, "catalog.extend is the remedy and must work"


def test_no_false_positives_on_the_stress_corpus(built_in, extended):
    """Precision is the property that must not degrade. A missed agent is a
    gap; a chatbot reported as an agent is a reason to stop reading."""
    assert built_in.false_positives == 0
    assert extended.false_positives == 0


def test_the_stress_margin_is_recorded_and_narrower(extended):
    """The honest number.

    The base corpus separates by 0.26. This corpus separates by roughly half
    that. Accuracy holds and every verdict is correct, but the headroom is
    materially smaller — which is what the first real capture will eat into.

    Pinned so a change that narrows it further has to be noticed.
    """
    margin = extended.separation_margin
    assert margin > 0, "the classes must still separate"
    assert 0.10 < margin < 0.20, f"stress margin moved to {margin:.3f}"


# --- the CLI ------------------------------------------------------------------

def test_stress_command_prints_both_margins(capsys):
    """The number that flatters is the one that gets used by accident, so the
    tool that prints it says which to quote."""
    from custos_a0.cli import main

    assert main(["stress"]) == 0
    out = capsys.readouterr().out
    assert "separation margin" in out
    assert "0.260" in out, "the base corpus number must appear for comparison"
    assert "Quote this number instead" in out


def test_hiding_the_gateway_reproduces_the_miss_on_demand(capsys):
    """Being able to demonstrate the one real failure is more useful than
    describing it."""
    from custos_a0.cli import main

    assert main(["stress", "--hide-gateway"]) == 0
    out = capsys.readouterr().out
    assert "missed: deploy-remediation-agent" in out
    assert "gateway" in out
