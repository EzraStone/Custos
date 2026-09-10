"""The served report describes an account, so it may not be built from one scan.

A scan is one region's window. The register the report renders spans every
region the account is collected in, and every figure printed beside that
register — how much of the flow log parsed, which fields it carried, how many
AWS reads failed, how much of the approval scope was readable — has to span
them too.

Four separate defects of exactly this shape have been fixed: the region label
naming one region while listing agents from two, a flow log format from one
region described as the account's, an incomplete-coverage banner suppressed by
a healthy region, and a principal count from one region printed beside a count
of agents from all of them. Each was invisible in a single-region test account,
which is every account we have.

So this is a test about the source rather than about behaviour: it fails when
the report route reads a per-scan figure off the latest scan again, whether or
not anyone has written the two-region test that would have caught it.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "custos" / "api" / "app.py"

# What the latest scan is legitimately for in the report route: identifying
# which scan it is, so the diff has a baseline to compare against, and whether
# there is one at all. Everything else about a scan is one region's answer.
ALLOWED = {"id", "started_at"}


def _report_route() -> ast.FunctionDef:
    tree = ast.parse(APP.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_report":
            return node
    raise AssertionError("the report route is not called get_report any more")


def test_the_report_route_reads_no_per_scan_figures():
    route = _report_route()
    read = {
        node.attr
        for node in ast.walk(route)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "latest"
    }
    assert read <= ALLOWED, (
        f"the report route reads {sorted(read - ALLOWED)} off the latest scan. "
        "That is one region's answer to a question the report asks about the "
        "whole account — take it from latest_scan_per_region instead."
    )


def test_the_route_asks_for_every_regions_scan():
    """The other half: reading nothing off `latest` would also pass if the
    route stopped saying anything about coverage at all."""
    route = _report_route()
    called = {
        node.func.attr
        for node in ast.walk(route)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "latest_scan_per_region" in called
