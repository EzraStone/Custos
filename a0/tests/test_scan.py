"""The scanner, end to end, against the labelled corpus."""

import pytest
from custos.register.model import BlastRadius, Status

from custos_a0.scanbridge import scan


@pytest.fixture(scope="module")
def result():
    return scan()


def test_every_discovered_agent_is_a_real_agent(result):
    from custos_a0 import corpus
    labels = {w.principal: w.label for w in corpus.build().workloads}
    for agent in result.register.agents.values():
        assert labels[agent.identity.principal] == "agent", agent.identity.principal


def test_all_five_agents_are_discovered(result):
    assert len(result.register.agents) == 5


def test_discovered_agents_are_never_sanctioned(result):
    """SEC-17 at the pipeline level, not just at the store level."""
    for agent in result.register.agents.values():
        assert agent.status is Status.DISCOVERED
        assert agent.imprimatur is None
        assert agent.unsanctioned


def test_headline_names_the_write_capable_agents(result):
    assert "unsanctioned agent" in result.headline
    assert "writes to production" in result.headline


def test_blast_radius_comes_from_iam_not_from_traffic(result):
    """ops-automation holds s3:* and must read as destructive even though its
    observed traffic is indistinguishable from a read-only agent's."""
    ops = next(
        a for a in result.register.agents.values() if "ops-automation" in a.identity.principal
    )
    assert ops.reach.blast_radius is BlastRadius.DESTRUCTIVE


def test_findings_carry_arguable_evidence(result):
    """Every finding must come with sentences the workload owner can dispute."""
    for agent in result.register.agents.values():
        assert agent.provenance.evidence
        assert all(len(e) > 40 for e in agent.provenance.evidence)


def test_attribution_resolves_through_multiple_methods(result):
    teams = {a.identity.owner_team for a in result.register.agents.values()}
    # Resource tags, IAM path, and the name heuristic must all be exercised.
    assert {"support-platform", "developer-experience", "finance", "platform"} <= teams


def test_review_band_holds_the_ambiguous_workloads_and_nothing_else(result):
    reviewed = {v.principal.rsplit("/", 1)[-1] for v in result.review_candidates}
    assert reviewed == {"doc-batch", "ci-runner"}


def test_unsanctioned_set_is_ordered_by_blast_radius_first(result):
    ranks = [a.reach.blast_radius.rank for a in result.register.unsanctioned]
    assert ranks == sorted(ranks, reverse=True)


def test_rescanning_does_not_duplicate_or_promote(result):
    from custos_a0.scanbridge import scan as run
    first = run()
    ids = set(first.register.agents)
    again = run()
    assert set(again.register.agents) == ids
    assert all(a.status is Status.DISCOVERED for a in again.register.agents.values())
