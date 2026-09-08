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
        _write_report(args.out, outcome, batch.account_id,
                      declared=_declared_labels(conn, batch.account_id),
                      reviews=_reviews_with_history(conn, outcome, batch.account_id))
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


def _reviews_with_history(conn, outcome, account_id: str) -> list:
    """This scan's maybes, each carrying how many scans have said the same.

    The live verdict cannot know this and the store can. Passing it explicitly
    keeps `render` ignorant of the database, which is what lets the report be
    rendered in a test from a scan result and nothing else.
    """
    from .gateway import blind_reach
    from .report import Review
    from .store.scans import ReviewStore

    reviews = ReviewStore(conn)
    reach = blind_reach(_open_questions(conn, account_id))
    return [
        Review(
            principal=v.principal,
            confidence=v.confidence,
            evidence=tuple(v.evidence),
            seen_in_scans=reviews.recurrence(account_id, v.principal),
            sends_to=reach.get(v.principal, ()),
        )
        for v in outcome.result.review_candidates
    ]


def _write_report(
    path: str, outcome, account_label: str, diff: ScanDiff | None = None,
    drift: list[Drift] | None = None, declared: list[str] | None = None,
    reviews: list | None = None,
) -> None:
    Path(path).write_text(render(
        outcome.result,
        account_label=account_label,
        generated_at=datetime.now(UTC),
        diff=diff if diff is not None else outcome.diff,
        drift=drift if drift is not None else outcome.drift,
        coverage=outcome.coverage,
        declared=declared,
        reviews=reviews,
    ))


def _open_questions(conn, account_id: str) -> list:
    """Gateway questions this account has not answered yet.

    Declared addresses are dropped. A customer who declared a gateway last week
    being asked about it again on every scan learns that the questions are not
    worth reading, which is the failure mode this whole mechanism cannot
    afford.
    """
    from .gateway import Candidate
    from .store.declarations import CandidateStore, DeclarationStore

    declared = DeclarationStore(conn).declared_for(account_id)
    return [
        Candidate.from_row(c)
        for c in CandidateStore(conn).latest_for(account_id)
        if not declared.covers(c["address"])
    ]


def _declared_labels(conn, account_id: str) -> list[str]:
    """What this account declared, for the report provenance section."""
    from .store.declarations import DeclarationStore

    return [
        f"{r.value} ({r.note})" if r.note else r.value
        for r in DeclarationStore(conn).records_for(account_id)
    ]


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
    """List the accounts this database holds, worst first.

    A count of agents per account is not enough to choose between fifty of
    them. What somebody with an afternoon needs is which account holds
    unsanctioned agents that can destroy things and which has not been scanned
    — so the destructive count leads and the ordering follows it.
    """
    from .store.scans import ScanStore

    conn = open_database(args.db)
    rows = conn.execute(
        "SELECT account_id, COUNT(*) AS agents, "
        "SUM(CASE WHEN status = 'sanctioned' THEN 1 ELSE 0 END) AS sanctioned, "
        "SUM(CASE WHEN status NOT IN ('sanctioned', 'retired') "
        "         AND blast_radius = 'destructive' THEN 1 ELSE 0 END) AS destructive "
        "FROM agents GROUP BY account_id"
    ).fetchall()

    if not rows:
        print("No accounts in this database yet.")
        return 0

    scans = ScanStore(conn)
    summary = []
    for row in rows:
        latest = scans.latest_scan(row["account_id"])
        summary.append({
            "account_id": row["account_id"],
            "agents": row["agents"],
            "sanctioned": row["sanctioned"] or 0,
            "unsanctioned": row["agents"] - (row["sanctioned"] or 0),
            "destructive": row["destructive"] or 0,
            "last_scan": latest.started_at if latest else None,
        })

    # Never-scanned first, then by what can do the most damage. An account
    # nobody has looked at outranks any finding, because a finding is
    # something somebody knows.
    summary.sort(key=lambda a: (
        a["last_scan"] is not None, -a["destructive"], -a["unsanctioned"], a["account_id"],
    ))

    print(f"{'account':<18}{'agents':>8}{'sanctioned':>12}{'unsanctioned':>14}"
          f"{'destructive':>13}{'last scan':>14}")
    print("-" * 79)
    for a in summary:
        when = f"{a['last_scan']:%Y-%m-%d}" if a["last_scan"] else "never"
        print(f"{a['account_id']:<18}{a['agents']:>8}{a['sanctioned']:>12}"
              f"{a['unsanctioned']:>14}{a['destructive']:>13}{when:>14}")
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


def cmd_rates(args: argparse.Namespace) -> int:
    """What this account pays, and whether anyone confirmed it."""
    from .store.rates import RateStore

    rates = RateStore(open_database(args.db)).rates_for(args.account)
    print(f"revision  {rates.revision}")
    if not rates.verified:
        # The sentence that matters. Every dollar figure this account has been
        # shown came from a placeholder, and nobody reading a report knows
        # that unless somebody says it.
        print()
        print("These are order-of-magnitude placeholders, good for ranking agents")
        print("against each other and nothing else. Set your own with:")
        print(f"  custos set-rate anthropic --account {args.account} \\")
        print("    --input 3.00 --output 15.00 --operator you@example.com")
        print()

    print(f"{'provider':<14}{'input $/Mtok':>14}{'output $/Mtok':>15}")
    print("-" * 43)
    for provider, price in sorted(rates.prices.items()):
        print(f"{provider:<14}{price.input_per_mtok:>14.2f}{price.output_per_mtok:>15.2f}")
    return 0


def cmd_set_rate(args: argparse.Namespace) -> int:
    """Record what this account pays for one provider."""
    from .store.rates import RateStore

    conn = open_database(args.db)
    try:
        RateStore(conn).supply(
            args.account, args.provider, args.input_per_mtok,
            args.output_per_mtok, args.operator,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    conn.commit()

    print(f"{args.provider}: ${args.input_per_mtok:.2f} in, "
          f"${args.output_per_mtok:.2f} out per million tokens")
    print(f"recorded against {args.operator}")
    print("applies to the next scan; existing figures are not recomputed")
    return 0


def cmd_reviews(args: argparse.Namespace) -> int:
    """Workloads the classifier was unsure about in the last scan.

    Read only. There is no `custos promote`: the register has one way in and it
    is a scan, and a command that moved a maybe by hand would make every
    guarantee about how an agent got there conditional on nobody having used it.
    """
    from .gateway import blind_reach
    from .store.scans import ReviewStore

    conn = open_database(args.db)
    store = ReviewStore(conn)
    found = store.latest_for(args.account)
    if not found:
        print("The last scan was sure about everything it saw.")
        return 0

    reach = blind_reach(_open_questions(conn, args.account))
    # Correlated maybes first: a maybe that also sends a transcript-shaped
    # stream at an undeclared address is the shape of an agent behind a
    # gateway, and it is the one worth reading.
    found.sort(key=lambda r: (not reach.get(r["principal"]), -r["confidence"]))

    print(f"{len(found)} workload{'s' if len(found) != 1 else ''} in the review band.")
    print("Not confident enough to register as agents, not clearly ordinary either.")
    print()
    for r in found:
        seen = store.recurrence(args.account, r["principal"])
        # The recurrence is the point. One uncertain window is noise; the same
        # workload uncertain in eleven scans is a standing question.
        recurring = f"  (in {seen} scans)" if seen > 1 else ""
        print(f"  {r['principal'].rsplit('/', 1)[-1]}  {r['confidence']:.2f}{recurring}")
        sends_to = reach.get(r["principal"], ())
        if sends_to:
            print(f"      reaches no model provider we recognise, and sends "
                  f"{', '.join(sends_to)} far more than it gets back")
            print("      if that is a model gateway, this is an agent — "
                  f"custos declare {sends_to[0]} --account {args.account}")
        if r["unavailable"]:
            print(f"      could not evaluate: {', '.join(r['unavailable'])}"
                  " — low confidence may be for want of input")
        for line in r["evidence"]:
            print(f"      {line}")
    return 0


def cmd_endpoints(args: argparse.Namespace) -> int:
    """List what this account declared its model endpoints to be."""
    from .store.declarations import DeclarationStore

    records = DeclarationStore(open_database(args.db)).records_for(
        args.account, include_withdrawn=args.all
    )
    if not records:
        print("No declared endpoints. Run `custos gateways` to see what to ask about.")
        return 0

    print(f"{'value':<24}{'kind':<14}{'declared by':<24}{'note'}")
    print("-" * 78)
    for r in records:
        mark = "" if r.active else "  (withdrawn)"
        print(f"{r.value:<24}{r.kind:<14}{r.declared_by:<24}{r.note}{mark}")
    return 0


def cmd_declare(args: argparse.Namespace) -> int:
    """Declare a model endpoint.

    Takes effect on the next scan. Reclassifying stored telemetry would rewrite
    the history of what was found when, so the command says so rather than
    leaving someone to wonder why the register did not move.
    """
    from .store.declarations import DeclarationStore

    conn = open_database(args.db)
    try:
        record = DeclarationStore(conn).declare(
            args.account, args.value, args.kind, args.operator, args.note
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    conn.commit()

    print(f"declared {record.value} as a model endpoint for {args.account}")
    print(f"recorded against {record.declared_by}")
    print("takes effect on the next scan; existing findings are not reclassified")
    return 0


def cmd_gateways(args: argparse.Namespace) -> int:
    """Internal addresses that behave like model endpoints.

    Questions, not findings. A workload whose model calls go through one of
    these has no model traffic we can see, so it is not a finding of any kind —
    which is why this reads as a list of things to ask about rather than a list
    of things we concluded.
    """
    found = _open_questions(open_database(args.db), args.account)
    if not found:
        print("Nothing looks like an undeclared model gateway in the last scan.")
        return 0

    print("These addresses behave like model endpoints. If one is yours:")
    print(f"  custos declare <address> --account {args.account} --operator you@example.com")
    print()
    for c in found:
        print(f"  {c.question}")
        who = ", ".join(p.split("/")[-1] for p in c.blind_principals)
        print(f"      reached by: {who}")
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
        f"{result.batches:,} batches, {result.deliveries:,} delivery records"
    )
    if result.questions:
        # Said separately because it is the only line here an operator might
        # have wanted to act on before it went.
        print(
            f"{result.questions:,} open questions went with those scans "
            "(review candidates and gateway questions)"
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

    p = sub.add_parser("rates", help="what this account pays per provider")
    p.add_argument("--account", required=True)
    p.set_defaults(func=cmd_rates)

    p = sub.add_parser("set-rate", help="record what this account pays for a provider")
    p.add_argument("provider", help="anthropic, openai, bedrock, ...")
    p.add_argument("--account", required=True)
    p.add_argument("--operator", required=True)
    p.add_argument("--input", type=float, required=True, dest="input_per_mtok",
                   help="USD per million input tokens")
    p.add_argument("--output", type=float, required=True, dest="output_per_mtok",
                   help="USD per million output tokens")
    p.set_defaults(func=cmd_set_rate)

    p = sub.add_parser("reviews", help="workloads the classifier was unsure about")
    p.add_argument("--account", required=True)
    p.set_defaults(func=cmd_reviews)

    p = sub.add_parser("endpoints", help="model endpoints this account declared")
    p.add_argument("--account", required=True)
    p.add_argument("--all", action="store_true", help="include withdrawn declarations")
    p.set_defaults(func=cmd_endpoints)

    p = sub.add_parser("declare", help="declare a model endpoint")
    p.add_argument("value", help="a CIDR, an address, or an AWS service name")
    p.add_argument("--account", required=True)
    p.add_argument("--operator", required=True, help="the human making the declaration")
    p.add_argument("--note", default="", help="what you call this endpoint")
    p.add_argument("--kind", default="range", choices=["range", "aws_service"])
    p.set_defaults(func=cmd_declare)

    p = sub.add_parser("gateways", help="internal addresses that look like model endpoints")
    p.add_argument("--account", required=True)
    p.set_defaults(func=cmd_gateways)

    p = sub.add_parser("grant", help="sanction an agent")
    p.add_argument("agent_id")
    p.add_argument("--operator", required=True, help="the human granting authority")
    p.set_defaults(func=cmd_grant)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
