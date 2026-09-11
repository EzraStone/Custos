"""`custos-a0` — run the experiment and write its artifacts.

    custos-a0 experiment            print the G0 verdict and the sweep table
    custos-a0 experiment --out DIR  also write the report and log fixtures
    custos-a0 report --out DIR      render a scan report from the corpus
    custos-a0 fixtures --out DIR    write flow log fixtures in the native format
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from custos.report import render
from custos.telemetry import write_lines

from . import corpus as corpus_mod
from .evaluate import Result, decide, run_all
from .scanbridge import scan
from .wire import AggregationConfig, aggregate


def _sweep_table(results: list[Result]) -> str:
    header = (
        f"{'configuration':<34}{'recall':>8}{'precision':>11}"
        f"{'margin':>9}{'records':>10}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.scenario.name:<34}{r.recall:>8.2f}{r.precision:>11.2f}"
            f"{r.separation_margin:>+9.3f}{r.flow_records:>10,}"
        )
    return "\n".join(lines)


def _detail_table(result: Result) -> str:
    lines = [
        f"{'workload':<26}{'truth':<11}{'confidence':>11}  disposition",
        "-" * 62,
    ]
    for row in result.rows:
        flag = "" if row.correct else "   <-- MISS"
        lines.append(
            f"{row.workload:<26}{row.label:<11}{row.verdict.confidence:>11.3f}"
            f"  {row.verdict.disposition}{flag}"
        )
    return "\n".join(lines)


def cmd_experiment(args: argparse.Namespace) -> int:
    results = run_all()
    gate = decide(results)

    print("Custos A0 — does the signature separate agents from chatbots?\n")
    print(_sweep_table(results))
    print()
    primary = next(
        r
        for r in results
        if r.scenario.have_alb_logs and r.scenario.interval_seconds == 60
    )
    print(_detail_table(primary))
    print()
    print(f"G0: {gate.headline}")
    print(f"    {gate.detail}")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "sweep.txt").write_text(
            _sweep_table(results) + "\n\n" + _detail_table(primary) + "\n\n"
            f"G0: {gate.headline}\n    {gate.detail}\n"
        )
        _write_report(out)
        _write_fixtures(out)
        print(f"\nArtifacts written to {out}/")

    return 0 if gate.passed else 1


def _write_report(out: Path) -> Path:
    result = scan()
    path = out / "scan-report.html"
    path.write_text(
        render(result, account_label="acme-nonprod (synthetic)", generated_at=datetime.now(UTC))
    )
    return path


def _write_fixtures(out: Path, limit: int = 4000) -> Path:
    """Write flow log lines in the native format.

    Real-shaped fixtures matter beyond testing: they are what a prospective
    customer's platform engineer looks at when asking what exactly we ingest.
    """
    capture = aggregate(corpus_mod.build(), AggregationConfig())
    path = out / "flowlogs-sample.log"
    with path.open("w") as fh:
        write_lines(fh, capture.records[:limit])
    return path


def cmd_stress(args: argparse.Namespace) -> int:
    """Score the classifier against the corpus with partially-coupled workloads.

    Separate from `experiment` because G0 was defined against the base corpus
    and is measured there. This is the number to quote in diligence: the base
    corpus separates by 0.26, and a corpus containing agents that pause for
    human approval separates by roughly half that.
    """
    from .evaluate import run_hard

    result = run_hard(gateway_declared=not args.hide_gateway)

    print("Custos stress corpus — partially-coupled workloads included\n")
    print(_detail_table(result))
    print()
    margin = (
        f"separation margin {result.separation_margin:+.3f}"
        if result.margin_is_meaningful
        else "separation margin  n/a"
    )
    print(f"recall {result.recall:.2f}   precision {result.precision:.2f}   {margin}")
    if not result.margin_is_meaningful:
        names = ", ".join(r.workload for r in result.unscorable)
        print()
        print(
            f"No margin is quoted because {names} has no model traffic to "
            "score — every signal but the MCP fingerprint is a ratio over the "
            "intervals containing it. A margin measured across a workload "
            "nothing could score describes the absence of evidence, not the "
            "separation of classes."
        )
    if result.missed_agents:
        print()
        for row in result.missed_agents:
            print(f"missed: {row.workload}")
            print(f"        {row.note}")

    print()
    print(
        "The base corpus separates by 0.260. Quote this number instead wherever "
        "that one would be doing work."
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = _write_report(out)
    print(f"wrote {path}")
    return 0


def cmd_fixtures(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = _write_fixtures(out)
    print(f"wrote {path}")
    return 0


def cmd_questions(args: argparse.Namespace) -> int:
    """Score the gateway detector against a corpus that can produce a bad question.

    Separate from `stress` because it measures a different thing. `stress`
    scores verdicts; this scores an interruption. The corpus it runs against
    contains one real self-hosted gateway and seven ordinary internal services
    that ordinary infrastructure floods — a log collector, a backup service, a
    thumbnailer — each of which has the traffic shape the detector looks for
    and none of which is a gateway.
    """
    from .questions import run

    result = run(limit=args.limit)

    print("Custos gateway questions — one real gateway, seven ordinary services\n")
    header = f"{'':4}  {'address':16}{'egress':>12}{'ingress':>11}{'ratio':>9}   answer"
    print(header)
    print("-" * len(header))
    for i, a in enumerate(result.asked, start=1):
        c = a.candidate
        cut = "" if i <= result.limit else "   (below the cut, never shown)"
        print(
            f"{i:>3}.  {c.address:16}{c.egress / 1e6:>10.1f}MB"
            f"{c.ingress / 1e6:>9.1f}MB{c.ratio:>8.1f}:1   "
            f"{'GATEWAY' if a.real else 'no'}{cut}"
        )

    print()
    rank = result.rank if result.rank is not None else "never asked"
    print(
        f"shown {len(result.shown)}   worth asking "
        f"{len(result.shown) - result.wasted}   precision {result.precision:.2f}   "
        f"real gateway at {rank}"
    )
    if not result.found:
        print()
        print(
            "The customer never sees the question that matters. Every other "
            "question is honest and every one of them wastes the attention "
            "that would have answered it."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="custos-a0", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("experiment", help="run the sweep and decide G0")
    p.add_argument("--out", help="directory for artifacts")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("stress", help="score against the partially-coupled corpus")
    p.add_argument("--hide-gateway", action="store_true",
                   help="leave the self-hosted gateway undeclared, as a customer "
                        "who has not told us about theirs")
    p.set_defaults(func=cmd_stress)

    p = sub.add_parser("questions", help="score the gateway detector's questions")
    p.add_argument("--limit", type=int, default=5,
                   help="how many questions a customer is shown")
    p.set_defaults(func=cmd_questions)

    p = sub.add_parser("report", help="render a scan report from the corpus")
    p.add_argument("--out", default="out")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("fixtures", help="write flow log fixtures")
    p.add_argument("--out", default="out")
    p.set_defaults(func=cmd_fixtures)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
