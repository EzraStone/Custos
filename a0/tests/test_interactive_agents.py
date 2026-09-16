"""Where the classifier's boundary actually is for interactive agents.

The product's core signal is that an agent decides on its own to call a model.
That is true of an autonomous workload and false of an interactive one: an IDE
assistant, a copilot, an agent behind a chat box all start every trajectory
with a person typing, so `inbound_decoupling` reads ~0.01 for them and for a
chatbot alike.

This is not a corner of the market. Interactive assistants that run tool loops
are a large and growing share of what companies deploy, and the register is
supposed to contain them.

These tests measure what is left to separate them on, and the answer is
uncomfortable enough to be worth a file of its own.
"""

from __future__ import annotations

import pytest

from custos_a0.evaluate import run_hard
from custos_a0.trace import Label

COUPLED = 0.9
"""Above this a workload's model calls are answering inbound requests almost
every time — the regime where the strongest signal in the system says nothing."""


@pytest.fixture(scope="module")
def coupled():
    """Coupled workloads that also call tools.

    A coupled workload with no tools at all is separated by `tool_interleave`
    and is not what this file is about — a workload that calls no tools has
    nothing to act through and is not an agent by this product's definition.
    The hard set is the one that looks the same from every angle: coupled,
    interleaving, reaching internal services.
    """
    result = run_hard(gateway_declared=True)
    return [
        r for r in result.rows
        if r.verdict.features.inbound_coupling >= COUPLED
        and r.verdict.features.tool_interleave > 0.5
    ]


def test_the_corpus_has_coupled_workloads_of_both_kinds(coupled):
    labels = {r.label for r in coupled}
    assert labels == {Label.AGENT, Label.NOT_AGENT}, [r.workload for r in coupled]


def test_byte_asymmetry_does_not_separate_them_and_points_the_wrong_way(coupled):
    """The headline signal is not merely weak here. It is inverted.

    The interactive agent's egress-to-ingress ratio is lower than two of the
    chatbots it sits beside — 4.6 against 7.1 and 6.6. A classifier leaning on
    it harder would be more confident about the chatbots.
    """
    agents = [r for r in coupled if r.label is Label.AGENT]
    negatives = [r for r in coupled if r.label is not Label.AGENT]

    worst_agent = min(r.verdict.features.egress_ratio for r in agents)
    best_negative = max(r.verdict.features.egress_ratio for r in negatives)
    assert worst_agent < best_negative, (worst_agent, best_negative)


def test_tool_interleave_does_not_separate_them_either(coupled):
    """Every one of them alternates model calls with tool calls, because that
    is what retrieval-augmented generation looks like as well as what a tool
    loop looks like. Within a hundredth of each other."""
    values = [r.verdict.features.tool_interleave for r in coupled]
    assert max(values) - min(values) < 0.01, values


def test_the_count_of_tools_does_not_separate_them(coupled):
    """The agent reaches two distinct tool destinations and so does one of the
    chatbots. Reaching more things is what an agent does eventually, not what
    it does within one request."""
    agents = [r for r in coupled if r.label is Label.AGENT]
    negatives = [r for r in coupled if r.label is not Label.AGENT]

    assert max(r.verdict.features.distinct_tool_addresses for r in negatives) >= min(
        r.verdict.features.distinct_tool_addresses for r in agents
    )


def test_mcp_is_the_only_thing_that_separates_any_of_them(coupled):
    """Which is the finding.

    Every other feature overlaps or inverts, so the verdict on an interactive
    workload rests entirely on whether it speaks a protocol designed for giving
    models tools. That is decent evidence and it is not the same question.
    """
    separated = [r for r in coupled if r.verdict.features.mcp_windows > 0]
    assert separated, "nothing in the coupled set uses MCP"
    for r in separated:
        assert r.label is Label.AGENT, r.workload


def test_a_coupled_agent_without_mcp_is_not_merely_uncertain_but_dismissed(coupled):
    """The blind spot, as a number.

    A support copilot driving ordinary HTTP APIs has every observable a
    retrieval-augmented chatbot has. It does not land in the review band where
    a human would see it — it lands below the floor, which means it is recorded
    for the next scan's baseline and reported to nobody.

    That is the correct behaviour for the evidence and the wrong outcome for
    the account, and the gap between those two is what this file exists to
    keep visible.
    """
    blind = [
        r for r in coupled
        if r.label is Label.AGENT and r.verdict.features.mcp_windows == 0
    ]
    assert blind, "the corpus no longer contains an interactive agent without MCP"
    for r in blind:
        assert r.verdict.disposition.value == "not_agent", (
            r.workload, r.verdict.confidence, r.verdict.disposition
        )


def test_the_blind_agent_scores_below_a_chatbot_it_sits_beside(coupled):
    """Not a near miss. It is ranked below workloads that are not agents, so no
    amount of moving the threshold recovers it without taking them too."""
    blind = [
        r for r in coupled
        if r.label is Label.AGENT and r.verdict.features.mcp_windows == 0
    ]
    negatives = [r for r in coupled if r.label is not Label.AGENT]
    assert min(r.verdict.confidence for r in blind) < max(
        r.verdict.confidence for r in negatives
    )
