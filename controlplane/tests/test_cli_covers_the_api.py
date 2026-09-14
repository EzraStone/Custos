"""Everything the API can do, the CLI can do.

Not because the two surfaces are meant to be identical, but because of who
uses which. The control plane is deployed when a customer wants continuous
monitoring; before that — through the whole entry motion, which is where every
customer starts — there is no API at all. Anything only the API can do is
something nobody can do during onboarding.

Three gaps were found one at a time, each after the previous was fixed:
retiring an agent, reading the audit trail, and asking about an agent's drift
after the scan that found it. All three had been in the console since it
existed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custos"

# API routes with no CLI equivalent, and why each is not a gap.
EXEMPT = {
    # A liveness probe for whatever is running the process. There is nothing
    # for a person to do with it.
    "/healthz": "a probe, not a question anybody asks",
    # The CLI reads a database directly; there is no token to enumerate.
    "/v1/accounts": "`custos accounts`, from the database rather than a token",
}

# Where a route's capability lives in the CLI, when the names differ.
EQUIVALENT = {
    "/v1/batches": "scan",
    "/v1/register": "register",
    "/v1/scans": "history",
    "/v1/report": "scan",
    "/v1/fleet": "accounts",
    "/v1/gateway-candidates": "gateways",
    "/v1/agents/{agent_id}/imprimatur": "grant",
    "/v1/agents/{agent_id}/status": "status",
    "/v1/agents/{agent_id}/audit": "audit",
    "/v1/agents/{agent_id}/drift": "drift",
    "/v1/endpoints": "endpoints",
    "/v1/reviews": "reviews",
    "/v1/rates": "rates",
    "/v1/diff": "diff",
}


def _routes() -> set[str]:
    source = (ROOT / "api" / "app.py").read_text()
    return set(re.findall(r'@app\.(?:get|post)\("([^"]+)"', source))


def _subcommands() -> set[str]:
    source = (ROOT / "cli.py").read_text()
    return set(re.findall(r'sub\.add_parser\("([a-z-]+)"', source))


def test_every_route_has_a_cli_equivalent_or_a_reason():
    missing = []
    commands = _subcommands()
    for route in sorted(_routes()):
        if route in EXEMPT:
            continue
        command = EQUIVALENT.get(route)
        if command is None:
            missing.append(f"{route}: no equivalent named")
        elif command not in commands:
            missing.append(f"{route}: maps to `{command}`, which does not exist")
    assert not missing, (
        "the API can do things the CLI cannot:\n  " + "\n  ".join(missing)
        + "\nThe control plane is deployed when a customer wants continuous "
        "monitoring. Before that there is no API, and the entry motion is "
        "where every customer starts."
    )


def test_the_map_does_not_name_routes_that_are_gone():
    """A mapping entry for a route that no longer exists makes the test above
    pass by describing a surface nobody has."""
    routes = _routes()
    stale = sorted((set(EQUIVALENT) | set(EXEMPT)) - routes)
    assert not stale, f"the map names routes that do not exist: {stale}"


def test_every_transition_the_api_offers_the_cli_offers():
    """The narrower version of the same failure, and the one that happened:
    the CLI could retire an agent and not un-retire it, while the console
    offered all three."""
    from custos.cli import TRANSITIONS

    source = (ROOT / "api" / "app.py").read_text()
    tree = ast.parse(source)
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in (
            "RETIRED", "PENDING_REVIEW", "DISCOVERED",
        ):
            allowed.add(node.attr.lower())

    assert allowed <= set(TRANSITIONS), (
        f"the API can set {sorted(allowed - set(TRANSITIONS))} and the CLI cannot"
    )
