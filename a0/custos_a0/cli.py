"""`custos-a0` — run the experiment and write its artifacts.

    custos-a0 experiment            print the G0 verdict and the sweep table
    custos-a0 experiment --out DIR  also write the report and log fixtures
    custos-a0 report --out DIR      render a scan report from the corpus
    custos-a0 fixtures --out DIR    write flow log fixtures in the native format
    custos-a0 conversion            measure wire bytes per token, both ways round
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
        f"{'configuration':<42}{'recall':>8}{'precision':>11}"
        f"{'margin':>9}{'headroom':>10}{'records':>10}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.scenario.name:<42}{r.recall:>8.2f}{r.precision:>11.2f}"
            f"{r.separation_margin:>+9.3f}{r.agent_headroom:>+10.3f}"
            f"{r.flow_records:>10,}"
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
        "The base corpus separates by 0.415. Quote this number instead wherever "
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

    print(
        "Custos gateway questions — one self-hosted gateway, one endpoint AWS "
        "names for us, one it will not, seven ordinary services\n"
    )
    header = f"{'':4}  {'address':16}{'egress':>12}{'ingress':>11}{'ratio':>9}   answer"
    print(header)
    print("-" * len(header))
    for i, a in enumerate(result.asked, start=1):
        c = a.candidate
        cut = "" if i <= result.limit else "   (below the cut, never shown)"
        print(
            f"{i:>3}.  {c.address:16}{c.egress / 1e6:>10.1f}MB"
            f"{c.ingress / 1e6:>9.1f}MB{c.ratio:>8.1f}:1   "
            f"{'MODEL' if a.real else 'no'}{cut}"
        )

    print()
    rank = result.rank if result.rank is not None else "never asked"
    print(
        f"shown {len(result.shown)}   worth asking "
        f"{len(result.shown) - result.wasted}   precision {result.precision:.2f}   "
        f"first real endpoint at {rank}"
    )
    if not result.found:
        print()
        print(
            "The customer never sees the question that matters. Every other "
            "question is honest and every one of them wastes the attention "
            "that would have answered it."
        )
    return 0


def cmd_conversion(args: argparse.Namespace) -> int:
    """Measure the payload-bytes-to-tokens conversion the dollar figures rest on.

    Separate from every other command here because it scores no classifier. It
    measures an assumption — that a model call is four bytes per token — which
    `docs/STATUS.md` called a guess wrong in a direction nobody had measured.

    The answer turned out to be that the constant was right and the input was
    wrong. Applied to wire bytes it is off by between 10% and forty-four times
    depending on how the customer's client is configured; applied to payload it
    is 4.13 to 4.24 across every workload in the corpus, both ways round.
    """
    from custos.framing import STREAMED_PACKET_BYTES, responses_streamed

    from . import corpus as corpus_mod
    from .conversion import measure

    corpus = corpus_mod.build()
    header = (
        f"{'workload':<26}{'streams':>9}{'mean pkt':>10}{'payload b/tok':>15}"
        f"{'read as':>10}"
    )
    for streaming in (False, True):
        label = "streamed" if streaming else "whole"
        print(f"Custos conversion — model responses arrive {label}" + chr(10))
        print(header)
        print("-" * len(header))
        for m in measure(corpus, streaming):
            read = responses_streamed(
                m.ingress_bytes, m.ingress_packets, m.egress_packets
            )
            wrong = "  <-- WRONG" if read != m.streams else ""
            print(
                f"{m.workload:<26}{str(m.streams):>9}{m.mean_data_packet:>10.0f}"
                f"{m.payload_per_token:>15.2f}"
                f"{('streamed' if read else 'whole'):>10}{wrong}"
            )
        print()

    everything = measure(corpus, True) + measure(corpus, False)
    streamed = [m for m in everything if m.streams]
    whole = [m for m in everything if not m.streams]
    print(
        f"the discriminator sits at {STREAMED_PACKET_BYTES:.0f} bytes, between "
        f"{max(m.mean_data_packet for m in streamed):.0f} and "
        f"{min(m.mean_data_packet for m in whole):.0f}"
    )
    # The mixed-regime workload is excluded from the band and named, because a
    # range that quietly contained it would be describing a workload the
    # measurement cannot speak for rather than the spread of the constant.
    pure = [m for m in everything if m.payload_per_token > 1.0]
    mixed = [m.workload for m in everything if m.payload_per_token <= 1.0]
    print(
        f"payload bytes per token: {min(m.payload_per_token for m in pure):.2f}"
        f"-{max(m.payload_per_token for m in pure):.2f}, both ways round"
        + (f" (excluding {', '.join(sorted(set(mixed)))}, which runs both)" if mixed else "")
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

    p = sub.add_parser("conversion",
                       help="measure wire bytes per token, both ways round")
    p.set_defaults(func=cmd_conversion)

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
