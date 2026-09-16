"""Every number in the STATUS gate block, recomputed.

The block in `docs/STATUS.md` is the page anyone asking what is real reads
first, and until this file existed nothing connected it to the code. It
drifted, which was predictable: the numbers were copied out of a terminal by
hand, and the corpus they came from kept changing underneath them.

What it had drifted to is the argument for the file. It claimed the stress
corpus separates by 0.18 with one agent in review. By then the corpus had
gained a workload that pushes the margin to -0.202 and is dismissed outright,
so the document said the classifier works on the corpus built to show where it
does not. A stale number that flatters is not the same kind of error as a
stale number that does not.

This does not check formatting or prose. It recomputes each measurement and
asserts the printed value appears in the block, so a number can only be wrong
here by being wrong everywhere.

The gateway detector and scope readability are not recomputed: the first is
covered by `test_questions.py` against the same corpus, and the second is a Go
test. Both are named here so the gap is a decision rather than an oversight.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custos_a0 import corpus as corpus_mod
from custos_a0.ablation import run_ablation
from custos_a0.evaluate import SCENARIOS, run, run_all, stress_declarations

STATUS = Path(__file__).resolve().parents[2] / "docs" / "STATUS.md"


@pytest.fixture(scope="module")
def block() -> str:
    """The fenced block under the `make gates` line, and only that one."""
    text = STATUS.read_text()
    marker = "in one run:"
    assert marker in text, "the sentence introducing the gate block moved"
    after = text.split(marker, 1)[1]
    fences = re.findall(r"```\n(.*?)```", after, re.DOTALL)
    assert fences, "no fenced block follows the `make gates` line"
    return fences[0]


@pytest.fixture(scope="module")
def base():
    return run_all()


@pytest.fixture(scope="module")
def stress():
    return run(
        SCENARIOS[0],
        corpus_mod.build(corpus_mod.CorpusSpec(hard=True)),
        stress_declarations(),
    )


def _quoted(block: str, value: float, places: int = 3) -> bool:
    return f"{value:.{places}f}" in block or f"{abs(value):.{places}f}" in block


def test_the_base_corpus_numbers(base, block):
    supported = [r for r in base if r.scenario.have_alb_logs]
    assert _quoted(block, min(r.separation_margin for r in supported))
    assert _quoted(block, min(r.agent_headroom for r in supported))
    assert _quoted(block, min(r.review_clearance for r in supported))


def test_the_stress_corpus_numbers(stress, block):
    assert f"{stress.separation_margin:+.3f}" in block, stress.separation_margin
    assert f"recall {stress.recall:.2f}" in block, stress.recall
    assert f"surfaced {stress.surfaced_recall:.2f}" in block, stress.surfaced_recall


def test_the_margin_without_the_workload_it_cannot_separate(stress, block):
    """The number that stops -0.202 being read as "none of this works".

    Quoted next to the negative one deliberately, and asserted here so it
    cannot outlive the workload it is computed without.
    """
    assert stress.overlap, "the stress corpus no longer overlaps"
    agent, _ = stress.overlap
    assert _quoted(block, stress.margin_without(agent.workload))


def test_the_ablation_numbers(block):
    rows = run_ablation(
        corpus_mod.build(corpus_mod.CorpusSpec(hard=True)),
        SCENARIOS[0],
        stress_declarations(),
    )
    by_id = {a.removed: a for a in rows}
    assert _quoted(block, by_id["tool_interleave"].margin_cost)
    assert _quoted(block, by_id["mcp_fingerprint"].margin_cost)


def test_the_block_claims_no_number_it_cannot_defend(block, base, stress):
    """The direction the other tests cannot check.

    Asserting each measured value appears catches a number that went stale.
    It does not catch one that was never measured — a plausible decimal typed
    into the block stays there forever, because nothing looks for it.

    So: collect every decimal in the block and require each to be a value
    something in this repo produces. The allow-list is the point of the test,
    not an exemption from it; each entry names where the number comes from.
    """
    supported = [r for r in base if r.scenario.have_alb_logs]
    agent, _ = stress.overlap
    measured = {
        f"{min(r.separation_margin for r in supported):.3f}",
        f"{min(r.agent_headroom for r in supported):.3f}",
        f"{min(r.review_clearance for r in supported):.3f}",
        f"{abs(stress.separation_margin):.3f}",
        f"{stress.recall:.2f}",
        f"{stress.surfaced_recall:.2f}",
        f"{stress.margin_without(agent.workload):.3f}",
        "0.226",  # tool_interleave margin cost, asserted above
        "0.000",  # mcp_fingerprint margin cost, asserted above
        "4.12", "4.29",  # `make conversion`, test_conversion.py
    }
    found = set(re.findall(r"\d+\.\d+", block))
    assert found <= measured, found - measured
