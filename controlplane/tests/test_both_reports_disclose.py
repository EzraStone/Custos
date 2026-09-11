"""Whatever one report discloses, the other discloses too.

There are two paths to a report. `custos scan` renders one from the scan it
just ran; `GET /v1/report` renders one from the store, a week later, for a
customer who is forwarding it to their security team.

Every caveat in a report exists because its absence would let the document
overstate what was looked at. Two of them were in the first document and not
the second — the IPv6 blind spot and the records AWS dropped before we read
them — for the same reason both times: the figure was computed at scan time,
used immediately, and never stored. Nothing failed. The disclosure simply did
not exist in the copy anybody keeps.

So this compares the two construction sites rather than the two outputs. A
field set on one and not the other is a caveat that exists in one document and
not in the other, and there is no behavioural test that would notice.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custos"

# Fields that legitimately differ, with the reason. Anything else is an
# omission rather than a decision.
EXPECTED_ONLY_IN_SERVED = {
    # Assembled from the latest scan of each region, which a single scan's
    # render has no notion of: it is one region by construction.
    "missing_in",
    "parse_by_region",
}
EXPECTED_ONLY_IN_CLI: set[str] = set()


def _kwargs(path: Path, function: str, called: str) -> set[str]:
    """Every keyword passed to a `called(...)` call inside `function`."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function:
            continue
        fields: set[str] = set()
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == called
            ):
                fields |= {kw.arg for kw in call.keywords if kw.arg}
        return fields
    raise AssertionError(f"{function} is not in {path.name} any more")


def _coverage_kwargs(path: Path, function: str) -> set[str]:
    return _kwargs(path, function, "Coverage")


# What each report is rendered with. Not the same question as the coverage
# fields above: this is whole sections of the document rather than caveats
# inside one. `drift` was missing from the served report for months — the
# section that says an agent started doing something it had not done before,
# absent from the only copy that exists a week later.
_CLI_RENDER = ("_write_report", "render")
_SERVED_RENDER = ("get_report", "render")

RENDER_ONLY_IN_CLI: set[str] = set()
RENDER_ONLY_IN_SERVED: set[str] = set()


def test_the_served_report_discloses_everything_the_cli_one_does():
    cli = _coverage_kwargs(ROOT / "pipeline.py", "_coverage")
    served = _coverage_kwargs(ROOT / "api" / "app.py", "get_report")

    missing = cli - served - EXPECTED_ONLY_IN_CLI
    assert not missing, (
        f"the served report does not carry {sorted(missing)}. That caveat is "
        "in the document the CLI prints and absent from the one a customer "
        "keeps, which is the copy that gets forwarded."
    )


def test_the_cli_report_discloses_everything_the_served_one_does():
    cli = _coverage_kwargs(ROOT / "pipeline.py", "_coverage")
    served = _coverage_kwargs(ROOT / "api" / "app.py", "get_report")

    missing = served - cli - EXPECTED_ONLY_IN_SERVED
    assert not missing, (
        f"the CLI report does not carry {sorted(missing)}. It is the document "
        "somebody reads during onboarding, which is when a caveat about "
        "coverage changes what they do next."
    )


def test_every_coverage_field_is_set_by_at_least_one_of_them():
    """A field on Coverage that neither path fills is a caveat that renders
    for nobody — dead weight in the one class whose entire purpose is to make
    the report's silences legible."""
    from custos.report import Coverage

    fields = {f for f in Coverage.__dataclass_fields__ if not f.startswith("_")}
    filled = (
        _coverage_kwargs(ROOT / "pipeline.py", "_coverage")
        | _coverage_kwargs(ROOT / "api" / "app.py", "get_report")
    )
    assert fields <= filled, f"nothing ever sets {sorted(fields - filled)}"


def test_both_reports_are_rendered_with_the_same_sections():
    """A section passed to one render and not the other is a part of the
    document that exists in one copy and not the other. Nothing fails when it
    is missing: the section simply does not appear, in the copy a customer
    forwards."""
    cli = _kwargs(ROOT / "cli.py", *_CLI_RENDER)
    served = _kwargs(ROOT / "api" / "app.py", *_SERVED_RENDER)

    assert not (cli - served - RENDER_ONLY_IN_CLI), (
        f"the served report has no {sorted(cli - served - RENDER_ONLY_IN_CLI)}"
    )
    assert not (served - cli - RENDER_ONLY_IN_SERVED), (
        f"the CLI report has no {sorted(served - cli - RENDER_ONLY_IN_SERVED)}"
    )


def test_every_section_the_report_can_render_is_passed_by_someone():
    """A parameter on `render` that neither path fills is a section that never
    appears anywhere. It is not dead code — the function still renders it, for
    a caller that does not exist — so nothing flags it and reading the module
    suggests the report has a section it has never had."""
    import inspect

    from custos.report import render

    optional = {
        name
        for name, p in inspect.signature(render).parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    filled = (
        _kwargs(ROOT / "cli.py", *_CLI_RENDER)
        | _kwargs(ROOT / "api" / "app.py", *_SERVED_RENDER)
    )
    assert optional <= filled, f"nothing ever passes {sorted(optional - filled)}"
