#!/usr/bin/env python3
"""Regenerate the console screenshots in the README.

    make screenshots

Screenshots in a README rot silently: the UI moves, the images do not, and the
first person to notice is a reader who trusts them. This makes them a build
output rather than something someone once pasted in, so a stale image is one
command away from being current.

They are taken against the A0 corpus, not a customer account. Nothing in them
is real traffic, and the README says so where they appear.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stack import ROOT, Missing, run_node, serving

OUT = ROOT / "docs" / "images"


def main() -> int:
    try:
        with serving() as stack:
            OUT.mkdir(parents=True, exist_ok=True)
            print(f"shots: writing to {OUT.relative_to(ROOT)}\n")
            code = run_node(
                ROOT / "console" / "e2e" / "screenshots.mjs", stack, {"OUT": str(OUT)}
            )
            if code == 0:
                for image in sorted(OUT.glob("*.png")):
                    print(f"       {image.name}  {image.stat().st_size // 1024} kB")
            return code
    except Missing as absent:
        print(f"shots: skipped — {absent}")
        return 0
    except RuntimeError as failure:
        print(f"shots: {failure}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
