"""`make check` has to run what CI runs, or it is not a gate.

CI ran `npm run lint` on the console and the Makefile did not, so a warning
that fails the build arrived only after a push — which is the worst moment for
it, because the contributor has already moved on and the branch is already red.

Nothing connects the two files. This does.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Steps CI runs that the local gate deliberately does not. `build` produces an
# artifact rather than checking one, and `make console` is the target for that.
EXPECTED_ONLY_IN_CI = {"build"}


def _npm_scripts(text: str) -> set[str]:
    return set(re.findall(r"npm run ([a-z-]+)", text))


def test_make_runs_every_console_script_ci_runs():
    ci = _npm_scripts((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    make = _npm_scripts((ROOT / "Makefile").read_text())

    missing = ci - make - EXPECTED_ONLY_IN_CI
    assert not missing, (
        f"CI runs {sorted(missing)} on the console and `make check` does not. "
        "A check that only exists in CI arrives after a push, when the "
        "contributor has moved on and the branch is already red."
    )


def test_plain_npm_test_is_in_both():
    """`npm test` has no `run`, so the pattern above cannot see it."""
    for name in ("ci.yml", "Makefile"):
        path = ROOT / (".github/workflows/" + name if name.endswith("yml") else name)
        assert "npm test" in path.read_text(), f"{name} does not run the console tests"


def test_every_script_the_gate_runs_exists():
    """A target running `npm run lont` fails only for the person who has the
    console installed, and `make lint` skips the whole block when it is not."""
    import json

    scripts = set(json.loads((ROOT / "console" / "package.json").read_text())["scripts"])
    for name in _npm_scripts((ROOT / "Makefile").read_text()):
        assert name in scripts, f"the Makefile runs `npm run {name}`, which does not exist"
