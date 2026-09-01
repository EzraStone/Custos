#!/usr/bin/env python3
"""Stand the whole product up and drive it in a browser.

    make smoke

This is the only test that exercises the built bundle. Everything under
console/src mocks fetch, which cannot catch a request shape the server rejects
or a static mount that shadows an API route.

Skips rather than fails when the pieces are not present: no built console, no
playwright-core, no browser. It is a verification you run, not a gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stack import ROOT, Missing, run_node, serving


def main() -> int:
    try:
        with serving() as stack:
            print("smoke: driving the console\n")
            return run_node(ROOT / "console" / "e2e" / "smoke.mjs", stack)
    except Missing as absent:
        print(f"smoke: skipped — {absent}")
        return 0
    except RuntimeError as failure:
        print(f"smoke: {failure}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
