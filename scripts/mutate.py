#!/usr/bin/env python3
"""Remove each classifier signal and see which tests notice.

    make mutate

A signal is a claim about what separates an agent from a chatbot. The ablation
in `a0/custos_a0/ablation.py` measures what each one is worth on the corpus;
this measures something different and just as easy to lose — whether anything
in the suite would go red if one stopped working.

The two answers differ, which is the reason this exists separately.
`mcp_fingerprint` costs 0.000 of separation on both corpora and is noticed by
ten tests; a signal could equally cost a great deal and be pinned by one
assertion in one file, and nothing would say so.

A signal no test notices is a signal that can be broken by an unrelated
refactor and ship. That is the failure this gates on: zero is a failure, and
the count is printed either way so a signal that quietly loses coverage is
visible before it reaches zero.

It is deliberately not part of `make check`. Removing a signal makes a large
fraction of the suite fail, four times over, which is minutes rather than
seconds — the same reason `make smoke` is a thing you run rather than a gate.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = Path(__file__).resolve().parent / "_mutplugin.py"
SUITES = ("a0", "controlplane")


def signals() -> list[str]:
    """The shipping table, read from the code rather than listed here.

    A list in this file is a second place that has to agree, and the way that
    ends is a signal added last month silently not being measured.
    """
    sys.path.insert(0, str(ROOT / "controlplane"))
    from custos.classify import engine

    return [s.id for s in engine.SIGNALS]


def notice(signal: str) -> tuple[int, int]:
    """Run the suite without one signal. Returns (tests failing, files)."""
    env = dict(os.environ, CUSTOS_DROP_SIGNAL=signal)
    env["PYTHONPATH"] = str(PLUGIN.parent) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "_mutplugin",
         *SUITES, "--no-header", "-rf"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    failed = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAILED ")]
    files = {re.sub(r"::.*", "", ln.removeprefix("FAILED ")) for ln in failed}
    return len(failed), len(files)


def main() -> int:
    ids = signals()
    header = f"{'signal removed':<24}{'tests failing':>15}{'files':>8}"
    print("Custos signal mutation — what would notice if a signal stopped working\n")
    print(header)
    print("-" * len(header))

    unnoticed = []
    for signal in ids:
        count, files = notice(signal)
        flag = "" if count else "   <-- NOTHING NOTICES THIS"
        if not count:
            unnoticed.append(signal)
        print(f"{signal:<24}{count:>15}{files:>8}{flag}")

    print()
    if unnoticed:
        print(
            "FAIL — " + ", ".join(unnoticed) + " can be broken by an unrelated "
            "refactor and ship. A signal with no test is a claim nobody checks."
        )
        return 1
    print(
        "Every signal is noticed. The count is not a quality bar — a signal "
        "pinned by one assertion in one file is thinner than it looks."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
