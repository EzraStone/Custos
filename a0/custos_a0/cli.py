"""`custos-a0` — run the experiment and write its artifacts.

    custos-a0 experiment            print the G0 verdict and the sweep table
    custos-a0 experiment --out DIR  also write the report and log fixtures
    custos-a0 report --out DIR      render a scan report from the corpus
    custos-a0 fixtures --out DIR    write flow log fixtures in the native format
    custos-a0 conversion            measure payload bytes per token, both ways round
    custos-a0 ablation              which signal is carrying the result
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
        f"{'margin':>9}{'headroom':>10}{'review gap':>12}{'records':>10}"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        lines.append(
            f"{r.scenario.name:<42}{r.recall:>8.2f}{r.precision:>11.2f}"
            f"{r.separation_margin:>+9.3f}{r.agent_headroom:>+10.3f}"
            f"{r.review_clearance:>12.3f}{r.flow_records:>10,}"
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
    corpus separates by 0.485, and a corpus containing agents that pause for
    human approval does not separate at all — the margin is negative, and
    the workload responsible is named rather than averaged away.
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
    print(
        f"recall {result.recall:.2f}   "
        f"surfaced {result.surfaced_recall:.2f}   "
        f"precision {result.precision:.2f}   {margin}"
    )
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
    if result.overlap:
        agent, negative = result.overlap
        print()
        without = result.margin_without(agent.workload)
        print(
            f"The margin is negative because {agent.workload} "
            f"({agent.verdict.confidence:.3f}) scores below {negative.workload} "
            f"({negative.verdict.confidence:.3f}), which is not an agent. "
            f"Leaving that one workload out the margin is {without:+.3f}, so "
            "this is one shape the classifier cannot separate rather than a "
            "failure across the board — a distinction that matters to anyone "
            "quoting either number, and neither should be quoted alone."
        )

    if result.missed_agents:
        print()
        for row in result.missed_agents:
            door = "dismissed, nobody sees it" if row.dismissed else "in the review queue"
            print(f"missed: {row.workload} ({door})")
            print(f"        {row.note}")

    print()
    print(
        "The base corpus separates by 0.485. Quote this number instead wherever "
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

    result = run(limit=args.limit, streaming=args.streaming)

    print(
        "Custos gateway questions — one self-hosted gateway, one endpoint AWS "
        "names for us, one it will not, seven ordinary services"
        + (", responses streamed" if args.streaming else "") + "\n"
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
        f"{'workload':<26}{'endpoint':<11}{'streams':>9}{'mean pkt':>10}"
        f"{'payload b/tok':>15}{'read as':>10}"
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
                f"{m.workload:<26}{m.endpoint:<11}{str(m.streams):>9}"
                f"{m.mean_data_packet:>10.0f}{m.payload_per_token:>15.2f}"
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
    # Nothing is excluded. A row is one conversation now, so the workload that
    # runs both regimes contributes one row of each and both are in the band.
    print(
        f"payload bytes per token: {min(m.payload_per_token for m in everything):.2f}"
        f"-{max(m.payload_per_token for m in everything):.2f}, "
        f"across {len(everything)} conversations, both ways round"
    )
    return 0


def cmd_ablation(args: argparse.Namespace) -> int:
    """Which signal is carrying the result, on each corpus.

    Both corpora, because they answer differently and the difference is the
    point: the base corpus is easy enough that most of the signals are
    redundant, so a signal can look free there and be load-bearing on the
    workloads that are actually hard.

    Four columns of outcome rather than three. Margin, recall and precision
    are all measured at the register boundary, and a signal whose only job is
    to hold an ambiguous workload in the review queue moves none of them.
    That is not a hypothetical either: it is what `mcp_fingerprint` does, and
    for two corpora this table called it free.
    """
    from . import corpus as corpus_mod
    from .ablation import run_ablation
    from .evaluate import SCENARIOS, stress_declarations

    scenario = SCENARIOS[0]
    header = (
        f"{'signal removed':<24}{'weight':>8}{'margin':>9}{'cost':>8}"
        f"{'recall':>8}{'surfaced':>10}{'precision':>11}"
    )
    for label, spec, declared in (
        ("base corpus", corpus_mod.CorpusSpec(), None),
        ("stress corpus", corpus_mod.CorpusSpec(hard=True), stress_declarations()),
    ):
        corpus = corpus_mod.build(spec)
        rows = run_ablation(corpus, scenario, declared)
        print(f"Custos signal ablation — {label}, margin {rows[0].baseline_margin:+.3f}"
              + chr(10))
        print(header)
        print("-" * len(header))
        for a in rows:
            flag = "" if a.load_bearing else "   <-- carries nothing here"
            if a.dropped:
                flag = "   <-- drops " + ", ".join(a.dropped)
            print(
                f"{a.removed:<24}{a.weight:>8.1f}{a.margin:>+9.3f}{a.margin_cost:>+8.3f}"
                f"{a.recall:>8.2f}{a.surfaced_recall:>10.2f}{a.precision:>11.2f}{flag}"
            )
        print()

    print(
        "Cost is how much separation the signal was providing. Negative means "
        "removing it widened the margin."
    )
    print(
        "Surfaced is the fraction of true agents still reaching a human by "
        "either door. A signal can cost nothing in margin or recall and still "
        "be the only thing keeping a workload in the review queue."
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
    p.add_argument("--streaming", action="store_true",
                   help="model responses arrive one token at a time, which is "
                        "what a gateway proxying an SSE API produces")
    p.set_defaults(func=cmd_questions)

    p = sub.add_parser("ablation",
                       help="which signal is carrying the result")
    p.set_defaults(func=cmd_ablation)

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
