"""The figures in `CANDIDATES` are held to what `make candidates` prints.

The category exists so a measured-and-promising signal is not lost between
"shipped" and "rejected", and the whole value of it is that somebody picks the
entry up later and decides against its numbers. Numbers measured once by hand
and typed into a docstring cannot support that: nothing obliges them to stay
true, and one of the six was already wrong when this file was written.

Deliberately not a test that the entry says something. It is a test that every
number in it is a number the repository produces, which is the only version of
this check that catches the failure it exists for.
"""

from __future__ import annotations

import re

import pytest
from custos.classify.signals import CANDIDATES

from custos_a0.candidates import measure, separation

ENTRY = "work_per_request_variance"


@pytest.fixture(scope="module")
def rows():
    return measure()


@pytest.fixture(scope="module")
def text() -> str:
    return CANDIDATES[ENTRY]


def _by(rows, workload: str):
    return next(r for r in rows if r.workload == workload)


def test_the_scoped_separation_is_what_it_says(rows, text):
    scoped = [r for r in rows if r.interactive]
    assert f"{separation(scoped):+.3f}" in text, separation(scoped)


def test_the_unscoped_separation_is_what_it_says(rows, text):
    """The number that says what the availability predicate is buying.

    Quoted beside the scoped one on purpose. A candidate described only by the
    figure it produces inside its own fence is a candidate described by its
    best case.
    """
    assert f"{separation(rows):+.3f}" in text, separation(rows)


@pytest.mark.parametrize(
    "workload",
    [
        "support-copilot-backend",
        "kb-assistant",
        "ops-assistant-web",
        "ide-assistant-backend",
        "search-embedder",
        "sales-copilot-web",
    ],
)
def test_each_quoted_variance_is_measured(rows, text, workload):
    assert f"{_by(rows, workload).variance:.3f}" in text, (
        f"{workload} reads {_by(rows, workload).variance:.3f}"
    )


def test_the_entry_claims_no_number_it_cannot_defend(rows, text):
    """The direction the tests above cannot check.

    Asserting each measured value appears catches a figure that went stale. It
    does nothing about a plausible decimal that was never measured, because
    nothing goes looking for it — which is exactly how 0.30 sat in this entry
    beside a workload that reads 0.310.
    """
    scoped = [r for r in rows if r.interactive]
    measured = {f"{r.variance:.3f}" for r in rows} | {
        f"{abs(separation(rows)):.3f}",
        f"{abs(separation(scoped)):.3f}",
    }
    found = set(re.findall(r"\d+\.\d+", text))
    assert found <= measured, found - measured


def test_the_ide_assistant_is_outside_the_scope(rows):
    """The correction that motivated this file.

    The entry described "the coupled, tool-calling workloads" as four. The
    predicate that actually scopes the signal excludes anything reaching an
    MCP server — the classifier already has evidence for those — so it is
    three, and one of the four numbers was for a workload the signal would
    never be asked about.
    """
    assert not _by(rows, "ide-assistant-backend").interactive
    assert sum(1 for r in rows if r.interactive) == 3


def test_the_scope_is_not_doing_all_the_work(rows):
    """A separation across three workloads is thin. Say how thin.

    Two of the three are negatives and one is the agent, so the +0.154 rests
    on a single positive. That is worth knowing before anyone weights it, and
    it is the reason the entry still says "not adopted".
    """
    scoped = [r for r in rows if r.interactive]
    agents = [r for r in scoped if str(r.label) == "agent"]
    assert len(agents) == 1, [r.workload for r in agents]
