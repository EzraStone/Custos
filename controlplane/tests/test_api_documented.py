"""Every route the API serves appears in docs/API.md, and nothing else does.

The API has three consumers — the collector, the console, and whoever is
integrating it — and serves no OpenAPI schema on purpose: a generated schema
documents shapes and this document has to explain what a field means and why a
response is the way it is.

The cost of that choice is drift, and it is silent in both directions. A route
added and not written down is a capability nobody can use; a route documented
and removed is worse, because someone builds against it.

Three endpoints were added in a single afternoon before this test existed. Each
was documented, but only because it was remembered.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custos.api import TokenStore, create_app
from custos.store.db import open_database

ROOT = Path(__file__).resolve().parents[2]
API_DOC = ROOT / "docs" / "API.md"

# Routes FastAPI adds for itself, and the static console mount.
NOT_OURS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/"}

# `## `GET /v1/register`` and friends. The backticks are what separates a
# heading that documents a route from a heading that mentions one.
_DOCUMENTED = re.compile(r"^## `(GET|POST|PUT|DELETE|PATCH) ([^`]+)`", re.MULTILINE)


def _served() -> set[tuple[str, str]]:
    app = create_app(conn=open_database(), tokens=TokenStore({"t": "1"}))
    out = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or path in NOT_OURS:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            out.add((method, path))
    return out


def _documented() -> set[tuple[str, str]]:
    if not API_DOC.exists():
        pytest.skip("docs/API.md not present")
    # The document writes path parameters as {id}; FastAPI names them in full.
    return {
        (method, re.sub(r"\{[^}]+\}", "{}", path))
        for method, path in _DOCUMENTED.findall(API_DOC.read_text())
    }


def _normalise(routes: set[tuple[str, str]]) -> set[tuple[str, str]]:
    return {(m, re.sub(r"\{[^}]+\}", "{}", p)) for m, p in routes}


def test_every_route_is_documented() -> None:
    undocumented = _normalise(_served()) - _documented()
    assert not undocumented, (
        "routes the API serves that docs/API.md does not describe: "
        f"{sorted(undocumented)}"
    )


def test_nothing_is_documented_that_does_not_exist() -> None:
    """The worse direction. An absent route documented as present is something
    an integrator builds against and discovers at runtime."""
    phantom = _documented() - _normalise(_served())
    assert not phantom, (
        f"documented but not served: {sorted(phantom)}"
    )
