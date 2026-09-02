"""The CLI, exercised end to end against a temporary database."""

import json
from datetime import UTC, datetime

import pytest

from custos.cli import main
from custos.store.db import open_database
from custos.store.scans import ScanStore


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
    assert row.split() == [ACCOUNT, "5", "1", "4"]


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
