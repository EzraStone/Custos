"""`custos` — run a scan, render a report, inspect the register.

Exists so the whole pipeline can be driven without standing up a server. The
first customer scan will be run this way: a collector dry-run writes a batch to
a file, this reads it, and a report comes out. No service to deploy, no database
to provision, nothing to explain in a security review.

    custos scan batch.json --db acme.db --out report.html
    custos register --db acme.db
    custos history --db acme.db
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .api.schema import Batch
from .baseline import Drift
from .diff import ScanDiff
from .pipeline import ingest
from .report import render
from .store.agents import AgentStore
from .store.db import open_database
from .store.scans import ScanStore


def _load(path: str) -> Batch:
    raw = Path(path).read_text()
    return Batch.model_validate_json(raw)


def cmd_scan(args: argparse.Namespace) -> int:
    batch = _load(args.batch)
    conn = open_database(args.db)
    outcome = ingest(conn, batch)

    print(f"account       {batch.account_id}")
    print(f"window        {batch.window_start.isoformat()} .. {batch.window_end.isoformat()}")
    print(f"flow records  {len(batch.flows):,}")
    print(f"agents found  {len(outcome.result.register.agents)}")
    print(f"for review    {len(outcome.result.review_candidates)}")
    if outcome.batch.duplicate:
        print("note          this window had already been ingested; the batch was replaced")
    if outcome.coverage_note:
        print(f"limited by    {outcome.coverage_note}")
    print()
    print(outcome.result.headline)

    if outcome.diff.actionable:
        print()
        print(outcome.diff.headline)
        for change in outcome.diff.actionable:
            print(f"  [{change.kind}] {change.detail}")

    if outcome.drift:
        print()
        print("Behaviour worth asking about:")
        for finding in outcome.drift:
            print(f"  {finding.question}")

    if args.out:
        _write_report(args.out, outcome, batch.account_id)
        print()
        print(f"report        {args.out}")

    # Exit non-zero when unsanctioned agents were found, so this composes with
    # a CI job or a cron that should page someone. A clean account exits zero.
    return 1 if outcome.result.register.unsanctioned else 0


def _write_report(
    path: str, outcome, account_label: str, diff: ScanDiff | None = None,
    drift: list[Drift] | None = None,
) -> None:
    Path(path).write_text(render(
        outcome.result,
        account_label=account_label,
        generated_at=datetime.now(UTC),
        diff=diff if diff is not None else outcome.diff,
        drift=drift if drift is not None else outcome.drift,
    ))


def cmd_register(args: argparse.Namespace) -> int:
    conn = open_database(args.db)
    agents = AgentStore(conn)
    records = (
        agents.unsanctioned(args.account)
        if args.unsanctioned_only
        else agents.list_for_account(args.account)
    )

    if args.json:
        print(json.dumps([
            {
                "id": a.id, "principal": a.identity.principal, "status": str(a.status),
                "blast_radius": str(a.reach.blast_radius),
                "owner_team": a.identity.owner_team,
                "confidence": a.provenance.confidence,
            }
            for a in records
        ], indent=2))
        return 0

    if not records:
        print("No agents in the register for this account.")
        return 0

    print(f"{'principal':<34}{'radius':<13}{'status':<16}{'owner':<22}conf")
    print("-" * 92)
    for a in records:
        print(
            f"{a.identity.principal.rsplit('/', 1)[-1]:<34}"
            f"{str(a.reach.blast_radius):<13}{str(a.status):<16}"
            f"{a.identity.owner_team or '-':<22}{a.provenance.confidence:.2f}"
        )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    conn = open_database(args.db)
    scans = ScanStore(conn).scans_for(args.account, limit=args.limit)
    if not scans:
        print("No scans recorded for this account.")
        return 0

    print(f"{'when':<26}{'principals':>11}{'agents':>8}{'review':>8}{'coverage':>10}")
    print("-" * 63)
    for s in scans:
        flag = " (truncated)" if s.truncated else ""
        print(
            f"{s.started_at.strftime('%Y-%m-%d %H:%M UTC'):<26}"
            f"{s.principals_seen:>11}{s.agents_found:>8}{s.review_candidates:>8}"
            f"{s.coverage:>9.0%}{flag}"
        )
    return 0


def cmd_grant(args: argparse.Namespace) -> int:
    """Sanction an agent from the command line.

    Requires --operator. SEC-17 needs a person, and a CLI invocation with no
    named human is a machine granting itself authority.
    """
    from .register.store import TransitionError
    from .store.db import now

    conn = open_database(args.db)
    agents = AgentStore(conn)
    try:
        agent = agents.grant_imprimatur(args.agent_id, operator=args.operator, at=now())
    except (KeyError, TransitionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"sanctioned {agent.identity.principal} by {args.operator}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="custos", description=__doc__)
    parser.add_argument("--db", default="custos.db", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="ingest a batch and report on it")
    p.add_argument("batch", help="path to a batch JSON file")
    p.add_argument("--out", help="write an HTML report here")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("register", help="list the register")
    p.add_argument("--account", required=True)
    p.add_argument("--unsanctioned-only", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("history", help="list past scans")
    p.add_argument("--account", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("grant", help="sanction an agent")
    p.add_argument("agent_id")
    p.add_argument("--operator", required=True, help="the human granting authority")
    p.set_defaults(func=cmd_grant)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
