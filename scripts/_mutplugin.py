"""Drop one classifier signal for a whole pytest session.

Loaded with `-p _mutplugin` by `scripts/mutate.py`. Does nothing unless
CUSTOS_DROP_SIGNAL names a signal.

It patches `engine.SIGNALS` rather than `signals.SIGNALS` for the reason
recorded in `a0/custos_a0/ablation.py`: the engine does
`from .signals import SIGNALS`, so it holds its own reference and rebinding the
name in `signals` changes nothing it reads. That mistake produces a run in
which every test passes, which here would read as "nothing notices" — the
exact finding this tool exists to report.
"""

from __future__ import annotations

import os


def pytest_configure(config):
    dropped = os.environ.get("CUSTOS_DROP_SIGNAL")
    if not dropped:
        return
    from custos.classify import engine

    kept = tuple(s for s in engine.SIGNALS if s.id != dropped)
    if len(kept) == len(engine.SIGNALS):
        raise SystemExit(f"no signal called {dropped!r}")
    engine.SIGNALS = kept
