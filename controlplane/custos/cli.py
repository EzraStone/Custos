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

from .baseline import Drift
from .batch import Batch
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

    if args.notify:
        _deliver(conn, outcome, batch.account_id)

    if args.out:
        _write_report(args.out, outcome, batch.account_id)
        print()
        print(f"report        {args.out}")

    # Exit non-zero when unsanctioned agents were found, so this composes with
    # a CI job or a cron that should page someone. A clean account exits zero.
    return 1 if outcome.result.register.unsanctioned else 0


def _deliver(conn, outcome, account_id: str) -> None:
    """Send findings to whatever channels are configured.

    Never fails the scan. The findings are in the register and the report
    either way, and a scan that exited non-zero because Slack was down would
    make an unrelated outage look like a security event.
    """
    from .deliver import from_env, notify
    from .store.db import now

    channels = from_env()
    if not channels:
        print()
        print("notify        no channels configured "
              "(set CUSTOS_SLACK_WEBHOOK or CUSTOS_SIEM_WEBHOOK)")
        return

    result = notify(conn, outcome, account_id, channels, now())
    print()
    for delivery in result.deliveries:
        status = "ok" if delivery.ok else f"FAILED: {delivery.error}"
        print(f"notify        {delivery.channel}: sent {delivery.sent}, "
              f"suppressed {delivery.suppressed} — {status}")


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
        coverage=outcome.coverage,
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


def cmd_accounts(args: argparse.Namespace) -> int:
    """List the accounts this database holds.

    The first thing anyone needs on a fleet database, and otherwise only
    answerable by guessing an account ID and seeing whether anything comes
    back.
    """
    conn = open_database(args.db)
    rows = conn.execute(
        "SELECT account_id, COUNT(*) AS agents, "
        "SUM(CASE WHEN status = 'sanctioned' THEN 1 ELSE 0 END) AS sanctioned "
        "FROM agents GROUP BY account_id ORDER BY account_id"
    ).fetchall()

    if not rows:
        print("No accounts in this database yet.")
        return 0

    print(f"{'account':<18}{'agents':>8}{'sanctioned':>12}{'unsanctioned':>14}")
    print("-" * 52)
    for row in rows:
        unsanctioned = row["agents"] - (row["sanctioned"] or 0)
        print(f"{row['account_id']:<18}{row['agents']:>8}"
              f"{row['sanctioned'] or 0:>12}{unsanctioned:>14}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    conn = open_database(args.db)
    scans = ScanStore(conn).scans_for(args.account, limit=args.limit)
    if not scans:
        print("No scans recorded for this account.")
        return 0

    # Two coverage columns, because they answer different questions. `coverage`
    # is how much of the traffic was read; `scope` is how much of what was
    # found could be named rather than shown as an address. A run of 100%
    # coverage and 20% scope is a set of correct findings nobody can approve.
    print(
        f"{'when':<26}{'principals':>11}{'agents':>8}{'review':>8}"
        f"{'coverage':>10}{'scope':>8}"
    )
    print("-" * 71)
    for s in scans:
        flag = " (truncated)" if s.truncated else ""
        scope = f"{s.scope_readable:>7.0%}" if s.scope_total else f"{'-':>8}"
        print(
            f"{s.started_at.strftime('%Y-%m-%d %H:%M UTC'):<26}"
            f"{s.principals_seen:>11}{s.agents_found:>8}{s.review_candidates:>8}"
            f"{s.coverage:>9.0%}{scope}{flag}"
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

    # Print the scope before granting it, as the console does. The scope is
    # what is being approved, and a command that reports it only afterwards
    # gives the operator no moment at which they could have declined.
    existing = agents.get(args.agent_id)
    if existing is None:
        print(f"error: no agent {args.agent_id}", file=sys.stderr)
        return 2

    scope = sorted(existing.reach.tools | existing.reach.data_stores)
    print(f"{existing.identity.principal}")
    print(f"  blast radius  {existing.reach.blast_radius}")
    print(f"  approving     {', '.join(scope) if scope else 'nothing observed'}")

    try:
        agent = agents.grant_imprimatur(args.agent_id, operator=args.operator, at=now())
    except (KeyError, TransitionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"sanctioned {agent.identity.principal} by {args.operator}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Drop telemetry past its retention window.

    Intended for a cron. Agents and audit entries are never touched, so this is
    safe to run unattended — the destructive-looking command cannot destroy the
    thing that matters.
    """
    from .store.retention import prune, vacuum

    conn = open_database(args.db)
    result = prune(conn, observation_days=args.observation_days, scan_days=args.scan_days)
    if not args.no_vacuum:
        vacuum(conn)

    print(
        f"pruned {result.observations:,} observations, {result.scans:,} scans, "
        f"{result.batches:,} batches"
    )
    print("agents and audit entries were not touched")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Show what changed between the two most recent scans."""
    from .diff import compare

    conn = open_database(args.db)
    agents = AgentStore(conn)
    scans = ScanStore(conn)

    history = scans.scans_for(args.account, limit=2)
    if len(history) < 2:
        print("Need two scans to compare. Run another scan.")
        return 0

    current, previous = history[0], history[1]
    registry = {a.id: a for a in agents.list_for_account(args.account)}
    result = compare(
        registry,
        scans.observations_for_scan(current.id),
        scans.observations_for_scan(previous.id),
        previous_scan_id=previous.id,
        current_scan_id=current.id,
    )

    print(f"comparing {previous.started_at:%Y-%m-%d %H:%M} .. "
          f"{current.started_at:%Y-%m-%d %H:%M} UTC")
    print()
    print(result.headline)
    for change in result.actionable:
        owner = change.owner_team or "unattributed"
        print(f"  [{change.kind}] {change.detail}")
        print(f"      owner: {owner}")
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    """Generate a customer's setup material.

    Prints the credentials once. They are not stored, and re-running produces
    different ones — so the output of this command is the only copy, which is
    deliberate.
    """
    from .onboard import InvalidAccount, generate

    try:
        onboarding = generate(args.account, args.endpoint, args.custos_account)
    except (InvalidAccount, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "account_id": onboarding.account_id,
            "external_id": onboarding.external_id,
            "token": onboarding.token,
            "tokens_env": onboarding.tokens_env,
        }, indent=2))
        return 0

    print("=" * 72)
    print("KEEP THIS. The credentials below are not stored and cannot be recovered.")
    print("=" * 72)
    print()
    print("Add to the control plane's CUSTOS_TOKENS:")
    print(f"  {onboarding.tokens_env}")
    print()
    print("Collector environment, once the role exists:")
    for line in onboarding.collector_env.splitlines():
        print(f"  {line}")
    print()
    print("-" * 72)
    print("Send this to the customer:")
    print("-" * 72)
    print(onboarding.message)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="custos", description=__doc__)
    parser.add_argument("--db", default="custos.db", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="ingest a batch and report on it")
    p.add_argument("batch", help="path to a batch JSON file")
    p.add_argument("--out", help="write an HTML report here")
    p.add_argument("--notify", action="store_true",
                   help="deliver findings to configured channels")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("register", help="list the register")
    p.add_argument("--account", required=True)
    p.add_argument("--unsanctioned-only", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("accounts", help="list the accounts this database holds")
    p.set_defaults(func=cmd_accounts)

    p = sub.add_parser("history", help="list past scans")
    p.add_argument("--account", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("onboard", help="generate a customer's setup material")
    p.add_argument("--account", required=True, help="the customer's 12-digit AWS account ID")
    p.add_argument("--endpoint", required=True, help="https URL of your control plane")
    p.add_argument("--custos-account", default="000000000000",
                   help="the AWS account your collector assumes from")
    p.add_argument("--json", action="store_true", help="credentials only, for scripting")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser("diff", help="compare the two most recent scans")
    p.add_argument("--account", required=True)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("prune", help="drop telemetry past its retention window")
    p.add_argument("--observation-days", type=int, default=90)
    p.add_argument("--scan-days", type=int, default=365)
    p.add_argument("--no-vacuum", action="store_true",
                   help="skip reclaiming disk space, which locks the database briefly")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("grant", help="sanction an agent")
    p.add_argument("agent_id")
    p.add_argument("--operator", required=True, help="the human granting authority")
    p.set_defaults(func=cmd_grant)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
