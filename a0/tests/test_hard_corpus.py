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


def test_the_missed_agent_is_not_scored_on_signals_it_has_no_traffic_for(built_in):
    """It used to land in the review band at 0.77, and that number was made of
    nothing: four of the five signals are ratios over the intervals containing
    model traffic, this workload has none, and over an empty set they do not
    read as neutral — they read as maximally incriminating.

    Those signals are now unavailable rather than zero, so the workload is not
    scored at all. That is the honest answer: our whole evidence base is model
    traffic. What surfaces it is `gateway.py` naming it as a workload reaching
    an undeclared address, which is a question somebody can answer."""
    verdict = _row(built_in, "deploy-remediation-agent").verdict

    assert verdict.features.model_windows == 0
    assert {"egress_asymmetry", "inbound_decoupling"} <= set(verdict.unavailable)
    assert verdict.confidence < 0.3, "unmeasurable is not the same as suspicious"
    assert not any(
        "intervals containing model traffic" in line for line in verdict.evidence
    ), "it must not claim a measurement over an empty set"


def test_declaring_the_gateway_recovers_it(extended):
    row = _row(extended, "deploy-remediation-agent")
    assert row.correct, "declaring the gateway is the remedy and must work"


def test_no_false_positives_on_the_stress_corpus(built_in, extended):
    """Precision is the property that must not degrade. A missed agent is a
    gap; a chatbot reported as an agent is a reason to stop reading."""
    assert built_in.false_positives == 0
    assert extended.false_positives == 0


def test_the_stress_margin_is_recorded_and_narrower(extended):
    """The honest number.

    The base corpus separates by 0.49. This corpus does not separate at all
    once the interactive blind spot is in it: a support copilot driving
    ordinary HTTP tools scores below a chatbot. Leaving that one workload out
    the rest separate by 0.18, with the IDE assistant in the review band at
    0.657.

    That is the corpus getting harder rather than the classifier getting
    worse. The workload was added because an ablation said mcp_fingerprint
    contributed nothing, which was a fact about a corpus where every MCP user
    already scored 0.995 on everything else. With the case it was carried for
    present, removing that signal collapses the classes into each other.

    Pinned so a change that narrows it further has to be noticed.
    """
    margin = extended.separation_margin
    assert margin < 0, "the interactive blind spot should make this negative"

    # One shape, not a failure across the board. Leaving out the workload the
    # classifier has no distinguishing evidence for, the rest separate.
    overlap = extended.overlap
    assert overlap is not None
    agent, _ = overlap
    assert agent.workload == "support-copilot-backend", agent.workload
    without = extended.margin_without(agent.workload)
    assert 0.15 < without < 0.24, f"stress margin without it moved to {without:.3f}"


# --- the CLI ------------------------------------------------------------------

def test_stress_command_prints_both_margins(capsys):
    """The number that flatters is the one that gets used by accident, so the
    tool that prints it says which to quote."""
    from custos_a0.cli import main

    assert main(["stress"]) == 0
    out = capsys.readouterr().out
    assert "separation margin" in out
    assert "0.485" in out, "the base corpus number must appear for comparison"
    assert "Quote this number instead" in out


def test_no_margin_is_quoted_when_a_workload_cannot_be_scored(built_in):
    """A margin measured across a workload nothing could score describes the
    absence of evidence, not the separation of classes. -0.523 reads as "the
    classes overlap", which is not what happened."""
    assert built_in.unscorable, "the undeclared-gateway agent has no model traffic"
    assert not built_in.margin_is_meaningful


def test_the_margin_is_meaningful_once_the_gateway_is_declared(extended):
    assert extended.unscorable == []
    assert extended.margin_is_meaningful


def test_the_stress_command_refuses_to_print_a_margin_it_cannot_defend(capsys):
    from custos_a0.cli import main

    assert main(["stress", "--hide-gateway"]) == 0
    out = capsys.readouterr().out
    assert "separation margin  n/a" in out
    assert "-0.5" not in out, "the number that reads as overlapping classes"
    assert "no model traffic to score" in out


def test_hiding_the_gateway_reproduces_the_miss_on_demand(capsys):
    """Being able to demonstrate the one real failure is more useful than
    describing it."""
    from custos_a0.cli import main

    assert main(["stress", "--hide-gateway"]) == 0
    out = capsys.readouterr().out
    assert "missed: deploy-remediation-agent" in out
    assert "gateway" in out


def test_the_hidden_gateway_is_offered_as_a_candidate():
    """The remedy for the corpus's hardest case, found rather than guessed.

    `agent_via_gateway` is invisible to the classifier by construction: its
    model calls go to an internal address, so it has no model traffic and is
    not a finding of any kind. Declaring the gateway fixes it — but only if
    somebody knows to. This asserts the detector puts the real gateway at the
    top of the list of things to ask about.
    """
    from custos.classify import sessionize
    from custos.gateway import candidates
    from custos.pipeline import to_scan_input

    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch
    from custos_a0.scenarios.hard import GATEWAY

    batch = build_batch(corpus.build(corpus.CorpusSpec(hard=True)))
    inp = to_scan_input(batch)
    telemetry = sessionize(
        inp.records, inp.principal_by_eni, inp.address_by_eni, inp.requests,
        origin=inp.start, interval=inp.interval,
    )

    found = candidates(telemetry)
    assert found, "the hidden gateway was not offered as a candidate at all"
    assert found[0].address == GATEWAY.ip, (
        f"the real gateway did not rank first: {[c.address for c in found]}"
    )


def test_a_healthy_corpus_produces_no_gateway_questions():
    """Every agent in the base corpus reaches a provider we recognise, so
    there is nothing hidden and nothing to ask about.

    A detector that asked anyway would be noise on a working account, which is
    how a prompt gets ignored on the account where it matters.
    """
    from custos.classify import sessionize
    from custos.gateway import candidates
    from custos.pipeline import to_scan_input

    from custos_a0.batchbridge import build_batch

    inp = to_scan_input(build_batch())
    telemetry = sessionize(
        inp.records, inp.principal_by_eni, inp.address_by_eni, inp.requests,
        origin=inp.start, interval=inp.interval,
    )
    assert candidates(telemetry) == []


def test_declaring_the_gateway_uses_the_mechanism_that_ships():
    """A0 measuring something other than the product is how a gate passes for
    a reason that does not generalise.

    The global `catalog.extend` this used to call has a property the shipping
    mechanism deliberately does not: it applies to every account in the
    process. Measuring the remedy through it would have been measuring a
    remedy nobody can use.
    """
    import inspect

    from custos_a0 import evaluate

    # The docstring explains what it no longer does, so read the code only.
    # A source-text assertion that its own explanation trips is a test that
    # fails for the opposite of its reason.
    def body(fn) -> str:
        # The docstrings explain what these no longer do, so read the code
        # only. A source-text assertion its own explanation trips is a test
        # that fails for the opposite of its reason.
        source = inspect.getsource(fn)
        return source[source.index('"""', source.index('"""') + 3) + 3:]

    assert "catalog.extend" not in body(evaluate.run_hard), (
        "run_hard is simulating the remedy through global state again"
    )
    # The declarations moved into their own function so the ablation could use
    # the same ones. Follow them there rather than asserting on where they
    # used to be: what matters is that they are still built through the
    # per-account mechanism the product ships.
    built = body(evaluate.stress_declarations)
    assert "catalog.extend" not in built
    assert "Declaration" in built and "build(" in built


def test_the_mcp_only_agent_is_offered_rather_than_missed(extended):
    """The hard positive added to test a signal, and what happens to it.

    An IDE assistant is inbound-coupled — a person types, then it works — and
    its trajectories are two or three steps, so neither strong signal has much
    to go on. It lands at 0.657: not confirmed, and not dropped either.

    That is SEC-17 working rather than failing. The alternative readings are
    worse in both directions: confirming it on this evidence would be guessing,
    and dropping it silently is the failure mode the review band exists to
    prevent.
    """
    row = _row(extended, "ide-assistant-backend")
    assert row.label is Label.AGENT
    assert row.verdict.disposition.value == "review", row.verdict.confidence
    assert 0.55 < row.verdict.confidence < 0.75, row.verdict.confidence


@pytest.fixture(scope="module")
def stress_ablation():
    from custos_a0 import corpus as corpus_mod
    from custos_a0.ablation import run_ablation
    from custos_a0.evaluate import SCENARIOS, stress_declarations

    return run_ablation(
        corpus_mod.build(corpus_mod.CorpusSpec(hard=True)),
        SCENARIOS[0],
        stress_declarations(),
    )


def test_the_mcp_fingerprint_is_load_bearing_once_the_corpus_needs_it(stress_ablation):
    """The measurement that justifies the signal's weight.

    Named for what it costs rather than for `load_bearing`, which is what
    this test used to assert and what made it stop measuring anything. The
    two assertions were `margin < 0` and `load_bearing`, and once the stress
    corpus gained a workload that pushed the baseline margin negative on its
    own, both held for every signal in the table — including one removed
    with no effect whatsoever. A test that passes for a signal that does
    nothing is not evidence about a signal that does something.

    What the signal actually does is keep ide-assistant-backend in the
    review queue. It buys no separation and no register row; it is the
    difference between an operator being asked to look at that workload and
    never hearing about it.
    """
    mcp = next(a for a in stress_ablation if a.removed == "mcp_fingerprint")
    assert mcp.dropped == ("ide-assistant-backend",), mcp.dropped
    assert mcp.surfaced_cost > 0.01, mcp.surfaced_cost


def test_the_mcp_fingerprint_buys_no_separation_and_the_table_says_so(
    stress_ablation,
):
    """The other half of the same measurement, asserted so it stays honest.

    The signal's contribution is entirely in the review queue. If a future
    change gives it a margin cost as well, that is a different signal doing
    a different job and the finding written about it no longer describes it.
    """
    mcp = next(a for a in stress_ablation if a.removed == "mcp_fingerprint")
    assert abs(mcp.margin_cost) < 0.001, mcp.margin_cost
    assert mcp.recall == 5 / 6, mcp.recall
    assert mcp.precision == 1.0, mcp.precision


def test_the_surfaced_column_discriminates(stress_ablation):
    """Guards against the failure the previous test died of.

    An assertion is only evidence if something could fail it. Two of the
    four signals cost nothing in surfaced recall on this corpus, so
    asserting that one of them does is a claim about that signal rather
    than a property of the table.
    """
    free = [a.removed for a in stress_ablation if a.surfaced_cost <= 0.01]
    assert free, "every signal holds the queue up; the assertion proves nothing"
