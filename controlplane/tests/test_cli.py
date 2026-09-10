"""The CLI, exercised end to end against a temporary database."""

import json
from datetime import UTC, datetime

import pytest

from custos.cli import main
from custos.store.db import open_database
from custos.store.scans import ScanStore


@pytest.fixture(scope="module")
def realistic_batch_path(tmp_path_factory):
    """A batch from the corpus, written once for the whole module."""
    import json

    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    body = build_batch(corpus.build(corpus.CorpusSpec(days=1))).model_dump(mode="json")
    body["account_id"] = "447120043318"
    path = tmp_path_factory.mktemp("batches") / "batch.json"
    path.write_text(json.dumps(body))
    return path


@pytest.fixture(scope="module")
def batch_file(tmp_path_factory):
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    path = tmp_path_factory.mktemp("cli") / "batch.json"
    batch = build_batch(corpus.build(corpus.CorpusSpec(days=1)))
    path.write_text(batch.model_dump_json())
    return path


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "custos.db")


ACCOUNT = "447120043318"


def test_scan_reports_findings_and_exits_nonzero(db, batch_file, capsys):
    """Non-zero on findings so it composes with a cron that should page."""
    code = main(["--db", db, "scan", str(batch_file)])
    out = capsys.readouterr().out
    assert code == 1
    assert "unsanctioned agent" in out
    assert "agents found  5" in out


def test_scan_writes_a_self_contained_report(db, batch_file, tmp_path):
    report = tmp_path / "report.html"
    main(["--db", db, "scan", str(batch_file), "--out", str(report)])
    page = report.read_text()
    assert page.startswith("<!DOCTYPE html>")
    assert "http://" not in page and "https://" not in page


def test_the_written_report_says_how_often_a_maybe_recurred(db, batch_file, tmp_path):
    """One uncertain window is a question about a window. The same workload
    uncertain in every scan is a question about the account, and the report is
    what the workload owner actually reads."""
    from custos.batch import Batch

    once = tmp_path / "once.html"
    main(["--db", db, "scan", str(batch_file), "--out", str(once)])
    assert "<h2>For review</h2>" in once.read_text(), "this corpus has maybes"
    assert "Seen in" not in once.read_text(), "one scan is the default"

    first = Batch.model_validate_json(batch_file.read_text())
    span = first.window_end - first.window_start
    second_path = tmp_path / "second.json"
    second_path.write_text(first.model_copy(update={
        "window_start": first.window_end,
        "window_end": first.window_end + span,
    }).model_dump_json())

    twice = tmp_path / "twice.html"
    main(["--db", db, "scan", str(second_path), "--out", str(twice)])

    page = twice.read_text()
    assert "Seen in" in page
    assert "2 scans" in page


def test_rescanning_the_same_window_says_so(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    main(["--db", db, "scan", str(batch_file)])
    assert "already been ingested" in capsys.readouterr().out


def test_register_lists_findings_worst_first(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    assert main(["--db", db, "register", "--account", ACCOUNT]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert "destructive" in lines[2]


def test_register_json_output_is_machine_readable(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    main(["--db", db, "register", "--account", ACCOUNT, "--json"])
    records = json.loads(capsys.readouterr().out)
    assert len(records) == 5
    assert all(r["status"] == "discovered" for r in records)


def test_granting_removes_an_agent_from_the_unsanctioned_set(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    main(["--db", db, "register", "--account", ACCOUNT, "--json"])
    agent_id = json.loads(capsys.readouterr().out)[0]["id"]

    assert main(["--db", db, "grant", agent_id, "--operator", "ezra@custos.dev"]) == 0
    assert "ezra@custos.dev" in capsys.readouterr().out

    main(["--db", db, "register", "--account", ACCOUNT, "--unsanctioned-only", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 4


def test_granting_requires_an_operator(db, batch_file):
    """SEC-17 at the command line: no named human, no grant."""
    with pytest.raises(SystemExit):
        main(["--db", db, "grant", "agt_x"])


def test_granting_an_unknown_agent_fails_cleanly(db, capsys):
    assert main(["--db", db, "grant", "agt_missing", "--operator", "ezra"]) == 2
    assert "error" in capsys.readouterr().err


def test_history_lists_scans(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    assert main(["--db", db, "history", "--account", ACCOUNT]) == 0
    assert "principals" in capsys.readouterr().out


def test_empty_database_reports_emptiness_rather_than_failing(db, capsys):
    assert main(["--db", db, "register", "--account", ACCOUNT]) == 0
    assert "No agents" in capsys.readouterr().out
    assert main(["--db", db, "history", "--account", ACCOUNT]) == 0
    assert "No scans" in capsys.readouterr().out


# --- diff and prune -----------------------------------------------------------

def test_diff_needs_two_scans_before_it_says_anything(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    assert main(["--db", db, "diff", "--account", ACCOUNT]) == 0
    assert "Need two scans" in capsys.readouterr().out


def test_diff_reports_an_escalation_with_its_owner(db, batch_file, tmp_path, capsys):
    """The sentence the subscription is built on."""
    from custos.batch import Batch

    first = Batch.model_validate_json(batch_file.read_text())
    main(["--db", db, "scan", str(batch_file)])

    escalated = [
        p.model_copy(update={"actions": sorted({*p.actions, "s3:DeleteBucket"})})
        if "finance-close" in p.principal else p
        for p in first.principals
    ]
    span = first.window_end - first.window_start
    second = first.model_copy(update={
        "window_start": first.window_end,
        "window_end": first.window_end + span,
        "principals": escalated,
    })
    second_path = tmp_path / "second.json"
    second_path.write_text(second.model_dump_json())
    main(["--db", db, "scan", str(second_path)])
    capsys.readouterr()

    main(["--db", db, "diff", "--account", ACCOUNT])
    out = capsys.readouterr().out
    assert "blast_radius_increased" in out
    assert "finance-close went from write to destructive" in out
    assert "owner: finance" in out


# The destructive-looking command must not be able to destroy the thing that
# matters, because it is meant to run unattended from a cron.
def test_prune_never_touches_agents_or_audit(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    assert main(["--db", db, "prune", "--observation-days", "1", "--scan-days", "2"]) == 0
    assert "agents and audit entries were not touched" in capsys.readouterr().out

    main(["--db", db, "register", "--account", ACCOUNT, "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 5


def test_prune_reports_what_it_removed(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()
    main(["--db", db, "prune"])
    assert "pruned" in capsys.readouterr().out


# --- notification -------------------------------------------------------------

def test_scan_without_channels_says_so_rather_than_failing(db, batch_file, capsys, monkeypatch):
    for key in ("CUSTOS_SLACK_WEBHOOK", "CUSTOS_SIEM_WEBHOOK"):
        monkeypatch.delenv(key, raising=False)

    main(["--db", db, "scan", str(batch_file), "--notify"])
    assert "no channels configured" in capsys.readouterr().out


# A scan that exited non-zero because Slack was down would make an unrelated
# outage look like a security event.
def test_a_delivery_failure_does_not_change_the_scan_result(db, batch_file, capsys, monkeypatch):
    monkeypatch.setenv("CUSTOS_SIEM_WEBHOOK", "https://127.0.0.1:1/nowhere")

    with_notify = main(["--db", db, "scan", str(batch_file), "--notify"])
    out = capsys.readouterr().out
    assert with_notify == 1, "still exits on findings, not on the delivery failure"
    assert "FAILED" in out


# --- fleets -------------------------------------------------------------------

def test_accounts_lists_what_the_database_holds(db, batch_file, capsys):
    """Otherwise only answerable by guessing an account ID and seeing whether
    anything comes back."""
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()

    assert main(["--db", db, "accounts"]) == 0
    out = capsys.readouterr().out
    assert "447120043318" in out
    assert "unsanctioned" in out


def test_accounts_counts_sanctioned_separately(db, batch_file, capsys):
    main(["--db", db, "scan", str(batch_file)])
    capsys.readouterr()
    main(["--db", db, "register", "--account", ACCOUNT, "--json"])
    agent_id = json.loads(capsys.readouterr().out)[0]["id"]
    main(["--db", db, "grant", agent_id, "--operator", "ezra"])
    capsys.readouterr()

    main(["--db", db, "accounts"])
    row = [ln for ln in capsys.readouterr().out.splitlines() if ACCOUNT in ln][0]
    # account, agents, sanctioned, unsanctioned, destructive, last scan.
    #
    # Destructive reads 0 because the agent just granted was the destructive
    # one — the register is ordered worst-first, so that is the id this test
    # picked. The column counts what is still awaiting a decision, which is
    # the question somebody choosing between accounts is asking.
    assert row.split()[:5] == [ACCOUNT, "5", "1", "4", "0"]


def test_accounts_on_an_empty_database_says_so(db, capsys):
    assert main(["--db", db, "accounts"]) == 0
    assert "No accounts" in capsys.readouterr().out


def test_history_shows_scope_alongside_coverage(tmp_path, capsys):
    """Two columns because they answer different questions. 100% coverage with
    20% scope is a set of correct findings nobody can approve."""
    db = tmp_path / "h.db"
    conn = open_database(db)
    scans = ScanStore(conn)
    record = scans.record_batch(
        account_id="1", region="us-east-1",
        window_start=datetime(2026, 8, 10, tzinfo=UTC),
        window_end=datetime(2026, 8, 10, 1, tzinfo=UTC),
        collector="test", received_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
        flow_records=10, requests=0, have_alb_logs=False,
    )
    scans.record_scan(
        batch_id=record.id, account_id="1",
        started_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
        principals_seen=3, agents_found=2, review_candidates=0,
        coverage=1.0, truncated=False, catalogue_revision="x",
        scope_named=1, scope_total=5,
    )
    conn.commit()
    conn.close()

    assert main(["--db", str(db), "history", "--account", "1"]) == 0
    out = capsys.readouterr().out
    assert "scope" in out
    assert "20%" in out


def test_history_shows_a_dash_when_nothing_internal_was_reached(tmp_path, capsys):
    # Not 0%. A scan that reached nothing internal has no unreadable scope,
    # and a zero in that column would read as a problem.
    db = tmp_path / "h2.db"
    conn = open_database(db)
    scans = ScanStore(conn)
    record = scans.record_batch(
        account_id="1", region="us-east-1",
        window_start=datetime(2026, 8, 10, tzinfo=UTC),
        window_end=datetime(2026, 8, 10, 1, tzinfo=UTC),
        collector="test", received_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
        flow_records=10, requests=0, have_alb_logs=False,
    )
    scans.record_scan(
        batch_id=record.id, account_id="1",
        started_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
        principals_seen=3, agents_found=2, review_candidates=0,
        coverage=1.0, truncated=False, catalogue_revision="x",
    )
    conn.commit()
    conn.close()

    assert main(["--db", str(db), "history", "--account", "1"]) == 0
    out = capsys.readouterr().out
    assert "0%" not in out.split("coverage")[-1].replace("100%", "")


def test_grant_prints_the_scope_before_granting_it(tmp_path, capsys):
    """The scope is what is being approved. A command that reports it only
    afterwards gives the operator no moment at which they could have declined,
    which is the same reason the console shows it in a dialog."""
    from custos.cli import main
    from custos.register.model import (
        Agent,
        Identity,
        ModelUse,
        Provenance,
        Reach,
        Source,
        Status,
    )
    from custos.register.store import agent_id
    from custos.store.agents import AgentStore

    db = tmp_path / "g.db"
    conn = open_database(db)
    at = datetime(2026, 8, 10, tzinfo=UTC)
    ident = "arn:aws:iam::1:role/finance-close"
    AgentStore(conn).upsert(Agent(
        id=agent_id("1", ident), first_seen=at, last_seen=at,
        status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.95,
                              observed_principal=ident, evidence=[]),
        identity=Identity(principal=ident, account_id="1"),
        model=ModelUse(),
        reach=Reach(tools={"billing-api 10.0.4.21"}, data_stores={"rds 10.0.9.45"}),
    ))
    conn.commit()
    conn.close()

    assert main([
        "--db", str(db), "grant", agent_id("1", ident), "--operator", "ezra@custos.dev",
    ]) == 0
    out = capsys.readouterr().out

    scope_line = next(line for line in out.splitlines() if "approving" in line)
    assert "billing-api 10.0.4.21" in scope_line
    assert "rds 10.0.9.45" in scope_line
    # Before, not after.
    assert out.index("approving") < out.index("sanctioned")


def test_grant_on_a_missing_agent_says_so_rather_than_raising(tmp_path, capsys):
    from custos.cli import main

    db = tmp_path / "g2.db"
    open_database(db).close()
    assert main(["--db", str(db), "grant", "agt_nope", "--operator", "ezra"]) == 2
    assert "no agent" in capsys.readouterr().err


def test_declare_says_it_takes_effect_next_scan(tmp_path, capsys):
    """Reclassifying stored telemetry would rewrite the history of what was
    found when, so the command says so rather than leaving someone to wonder
    why the register did not move."""
    db = tmp_path / "d.db"
    assert main([
        "--db", str(db), "declare", "10.0.7.0/24",
        "--account", "1", "--operator", "ezra@custos.dev", "--note", "llm-gateway",
        "--region", "us-east-1",
    ]) == 0
    out = capsys.readouterr().out
    assert "takes effect on the next scan" in out
    assert "not reclassified" in out


def test_declare_refuses_an_unparseable_range(tmp_path, capsys):
    db = tmp_path / "d2.db"
    assert main([
        "--db", str(db), "declare", "10.0.7.0/99",
        "--account", "1", "--operator", "ezra@custos.dev",
    ]) == 2
    assert "not a valid network" in capsys.readouterr().err


def test_endpoints_lists_what_was_declared(tmp_path, capsys):
    db = tmp_path / "d3.db"
    main(["--db", str(db), "declare", "10.0.7.0/24", "--account", "1",
          "--region", "us-east-1",
          "--operator", "ezra@custos.dev", "--note", "llm-gateway",
          "--region", "us-east-1"])
    capsys.readouterr()

    assert main(["--db", str(db), "endpoints", "--account", "1"]) == 0
    out = capsys.readouterr().out
    assert "10.0.7.0/24" in out and "llm-gateway" in out and "ezra@custos.dev" in out


def test_endpoints_points_at_the_gateways_command_when_there_are_none(tmp_path, capsys):
    # Somebody who has nothing declared usually does not know what to declare.
    db = tmp_path / "d4.db"
    open_database(db).close()
    assert main(["--db", str(db), "endpoints", "--account", "1"]) == 0
    assert "custos gateways" in capsys.readouterr().out


def test_gateways_says_nothing_is_hidden_rather_than_printing_an_empty_table(tmp_path, capsys):
    db = tmp_path / "d5.db"
    open_database(db).close()
    assert main(["--db", str(db), "gateways", "--account", "1"]) == 0
    assert "Nothing looks like an undeclared model gateway" in capsys.readouterr().out


def test_reviews_lists_the_maybes_with_their_evidence(tmp_path, capsys, realistic_batch_path):
    """A maybe with no evidence is a rumour. The whole value of showing these
    is what is known about them."""
    db = tmp_path / "r.db"
    main(["--db", str(db), "scan", str(realistic_batch_path)])
    capsys.readouterr()

    assert main(["--db", str(db), "reviews", "--account", "447120043318"]) == 0
    out = capsys.readouterr().out
    assert "review band" in out
    assert "ratio of" in out, "the evidence sentences are missing"


@pytest.fixture(scope="module")
def stress_batch_path(tmp_path_factory):
    """The harder corpus, which contains an agent behind a self-hosted
    gateway. The base corpus deliberately does not."""
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    body = build_batch(
        corpus.build(corpus.CorpusSpec(days=1, hard=True))
    ).model_dump(mode="json")
    body["account_id"] = ACCOUNT
    path = tmp_path_factory.mktemp("stress") / "batch.json"
    path.write_text(json.dumps(body))
    return path


def test_the_written_report_carries_the_open_questions(
    tmp_path, capsys, stress_batch_path
):
    """A workload behind an undeclared gateway produces no finding at all, so
    the account looks clean. The document that gets forwarded has to carry the
    one thing that would change how it is read."""
    db = tmp_path / "join.db"
    report = tmp_path / "stress.html"
    main(["--db", str(db), "scan", str(stress_batch_path), "--out", str(report)])
    capsys.readouterr()

    page = report.read_text()
    assert "<h2>Questions</h2>" in page
    assert "10.0.7.40" in page
    assert "deploy-remediation" in page


def test_the_base_corpus_report_asks_nothing(tmp_path, capsys, batch_file):
    """The half that matters: the base corpus hides nothing behind a gateway,
    so a section here would be teaching a reader to skip it."""
    db = tmp_path / "quiet.db"
    report = tmp_path / "base.html"
    main(["--db", str(db), "scan", str(batch_file), "--out", str(report)])
    capsys.readouterr()

    assert "<h2>Questions</h2>" not in report.read_text()


def test_gateways_stops_asking_once_the_address_is_declared(
    tmp_path, capsys, stress_batch_path
):
    """Being asked again every scan about something already answered is how a
    customer learns the questions are not worth reading."""
    db = tmp_path / "answered.db"
    main(["--db", str(db), "scan", str(stress_batch_path)])
    capsys.readouterr()

    main(["--db", str(db), "gateways", "--account", ACCOUNT])
    assert "10.0.7.40" in capsys.readouterr().out

    main(["--db", str(db), "declare", "10.0.7.40/32", "--account", ACCOUNT,
          "--operator", "ezra@custos.dev", "--note", "llm-gateway",
          "--region", "us-east-1"])
    capsys.readouterr()

    main(["--db", str(db), "gateways", "--account", ACCOUNT])
    assert "10.0.7.40" not in capsys.readouterr().out


def test_reviews_says_so_when_there_is_nothing_to_review(tmp_path, capsys):
    db = tmp_path / "r2.db"
    open_database(db).close()
    assert main(["--db", str(db), "reviews", "--account", "1"]) == 0
    assert "sure about everything" in capsys.readouterr().out


def test_there_is_no_command_that_promotes_a_maybe(capsys):
    """The register has one way in and it is a scan. A command that moved a
    maybe by hand would make every guarantee about how an agent got there
    conditional on nobody having used it."""
    import pytest as _pytest

    from custos import cli

    with _pytest.raises(SystemExit):
        cli.main(["--help"])
    helptext = capsys.readouterr().out
    for forbidden in ("promote", "confirm-agent", "register-agent"):
        assert forbidden not in helptext


def test_rates_says_the_defaults_are_placeholders(tmp_path, capsys):
    """Every dollar figure this account has been shown came from a
    placeholder, and nobody reading a report knows that unless it is said."""
    db = tmp_path / "rt.db"
    open_database(db).close()
    assert main(["--db", str(db), "rates", "--account", "1"]) == 0
    out = capsys.readouterr().out
    assert "unverified-placeholder" in out
    assert "order-of-magnitude" in out
    assert "custos set-rate" in out, "it should say how to fix it"


def test_setting_a_rate_stops_the_placeholder_warning(tmp_path, capsys):
    db = tmp_path / "rt2.db"
    main(["--db", str(db), "set-rate", "anthropic", "--account", "1",
          "--operator", "ezra@custos.dev", "--input", "1.5", "--output", "7.5"])
    capsys.readouterr()

    assert main(["--db", str(db), "rates", "--account", "1"]) == 0
    out = capsys.readouterr().out
    assert "customer-supplied" in out
    assert "order-of-magnitude" not in out


def test_setting_a_zero_rate_is_refused(tmp_path, capsys):
    db = tmp_path / "rt3.db"
    assert main(["--db", str(db), "set-rate", "anthropic", "--account", "1",
                 "--operator", "ezra@custos.dev", "--input", "0", "--output", "7.5"]) == 2
    assert "greater than zero" in capsys.readouterr().err


def test_accounts_puts_the_dangerous_and_the_unscanned_first(tmp_path, capsys):
    """A count of agents per account is not enough to choose between fifty of
    them. Never-scanned first, then by what can do the most damage."""
    from custos.register.model import (
        Agent,
        BlastRadius,
        Identity,
        ModelUse,
        Provenance,
        Reach,
        Source,
        Status,
    )
    from custos.register.store import agent_id
    from custos.store.agents import AgentStore

    db = tmp_path / "acc.db"
    conn = open_database(db)
    store = AgentStore(conn)
    at = datetime(2026, 8, 10, tzinfo=UTC)

    def add(account: str, name: str, radius: BlastRadius):
        ident = f"arn:aws:iam::{account}:role/{name}"
        store.upsert(Agent(
            id=agent_id(account, ident), first_seen=at, last_seen=at,
            status=Status.DISCOVERED,
            provenance=Provenance(source=Source.DISCOVERED, confidence=0.9,
                                  observed_principal=ident, evidence=[]),
            identity=Identity(principal=ident, account_id=account),
            model=ModelUse(), reach=Reach(blast_radius=radius),
        ))

    # Many read-only agents, one destructive, and one account with neither.
    for i in range(5):
        add("111111111111", f"reader-{i}", BlastRadius.READ)
    add("222222222222", "deleter", BlastRadius.DESTRUCTIVE)
    conn.commit()
    conn.close()

    assert main(["--db", str(db), "accounts"]) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith(("1111", "2222"))]

    assert lines[0].startswith("222222222222"), (
        "one agent that can delete outranks five that can only read"
    )
    assert "never" in out, "an unscanned account should say so"
