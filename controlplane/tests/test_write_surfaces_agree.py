"""Both write surfaces bound their free text the same way.

There are two ways into the audit trail: the API, which the console uses, and
the CLI, which whoever is running an onboarding uses. They write the same rows
and are read from the same table, and only one of them was bounded first.

A bound on one is worth nothing if the other is the path a person actually
takes — and the CLI is the path during onboarding, which is when the most
careless value gets typed.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custos"


def _limits_used(path: Path) -> set[str]:
    """Names from `custos.text` referenced in a file, as `text.OPERATOR`."""
    used = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "text"
        ):
            used.add(node.attr)
    return used


def test_the_cli_bounds_what_the_api_bounds():
    api = _limits_used(ROOT / "api" / "app.py")
    cli = _limits_used(ROOT / "cli.py")

    # VALUE is the API's alone: the CLI's equivalents are positional arguments
    # parsed as a CIDR or a service name, which fail on anything long rather
    # than storing a prefix of it.
    missing = api - cli - {"VALUE"}
    assert not missing, (
        f"the API bounds {sorted(missing)} and the CLI does not. Both write the "
        "same audit rows, and the CLI is the path taken during onboarding — "
        "which is when the most careless value gets typed."
    )


def test_both_use_the_one_cleaner():
    """Two implementations of "collapse whitespace and cut" would disagree on
    the first value that mattered."""
    for path in (ROOT / "api" / "app.py", ROOT / "cli.py"):
        assert "one_line" in path.read_text(), f"{path.name} cleans its own way"


def test_every_limit_is_used_by_somebody():
    """A limit nothing applies is a bound that exists only in a docstring."""
    from custos import text

    declared = {
        name for name in vars(text)
        if name.isupper() and isinstance(getattr(text, name), int)
    }
    used = _limits_used(ROOT / "api" / "app.py") | _limits_used(ROOT / "cli.py")
    assert declared <= used, f"nothing applies {sorted(declared - used)}"
