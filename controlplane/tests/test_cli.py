"""The CLI, exercised end to end against a temporary database."""

import json

import pytest

from custos.cli import main


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
    from custos.api.schema import Batch

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
