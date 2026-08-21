"""The report must not overstate, and must not leak."""

from datetime import UTC, datetime

import pytest

from custos.classify.engine import Disposition, Verdict
from custos.classify.features import Features
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
from custos.register.store import Register
from custos.report import render
from custos.scan import ScanResult

T0 = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

FEATURES = Features(
    have_inbound_logs=True, model_windows=10, total_model_egress=10**6,
    total_model_ingress=10**5, egress_ratio=10.0, inbound_coupling=0.0,
    egress_per_inbound_request=0.0, tool_interleave=1.0, distinct_tool_addresses=2,
    mcp_windows=3, median_episode_windows=5.0, p90_episode_windows=9.0,
    ratio_growth=1.4, offhours_egress_fraction=0.2, episodes=4,
)


def agent(principal="arn:aws:iam::1:role/finance-close", team="finance",
          radius=BlastRadius.WRITE, evidence=("Sent 1.0MB and received 132KB, a ratio of 7.9:1.",)):
    return Agent(
        id="agt_1", first_seen=T0, last_seen=T0, status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.95,
                              observed_principal=principal, evidence=list(evidence)),
        identity=Identity(principal=principal, owner_team=team, compute="Lambda"),
        model=ModelUse(providers={"anthropic"}, est_monthly_spend_usd=1420.0),
        reach=Reach(tools={"billing-api"}, data_stores={"billing-db"}, blast_radius=radius),
    )


def result(agents=(), reviews=()):
    reg = Register()
    for a in agents:
        reg.upsert(a)
    verdicts = list(reviews)
    return ScanResult(register=reg, verdicts=verdicts, telemetry=[], principals_seen=11)


def review(principal="arn:aws:iam::1:role/ci-runner", unavailable=()):
    return Verdict(
        principal=principal, confidence=0.52, disposition=Disposition.REVIEW,
        features=FEATURES, firings=[], unavailable=list(unavailable),
    )


@pytest.fixture
def page():
    return render(result([agent()], [review()]), "acme-nonprod", T0)


def test_report_states_the_headline_and_the_owner(page):
    assert "unsanctioned agent" in page
    assert "finance" in page


def test_report_carries_the_evidence_behind_each_finding(page):
    assert "a ratio of 7.9:1" in page


def test_report_states_that_payloads_were_never_collected(page):
    assert "Payload contents were never collected" in page
    assert "SEC-18" in page


def test_report_states_that_it_authorises_nothing(page):
    assert "authorises anything" in page
    assert "SEC-17" in page


def test_placeholder_pricing_is_disclosed(page):
    assert "unverified placeholder pricing" in page


def test_missing_alb_logs_are_disclosed_as_reduced_recall_not_hidden():
    page = render(result([agent()], [review(unavailable=["inbound_decoupling"])]), "acme", T0)
    assert "Load balancer access logs were not available" in page
    assert "Recall is reduced" in page


def test_unattributed_findings_get_their_own_section():
    orphan = agent(principal="arn:aws:iam::1:role/svc0001", team="")
    page = render(result([orphan]), "acme", T0)
    assert "Unattributed findings" in page


def test_no_unattributed_section_when_everything_is_owned(page):
    assert "Unattributed findings" not in page


def test_review_candidates_are_not_presented_as_agents(page):
    assert "For review" in page
    assert "needs a human" in page


def test_html_is_escaped():
    hostile = agent(principal='arn:aws:iam::1:role/<script>alert("x")</script>')
    page = render(result([hostile]), "acme", T0)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page


def test_account_label_is_escaped():
    page = render(result([agent()]), '<img src=x onerror="alert(1)">', T0)
    assert "<img src=x" not in page


def test_report_is_self_contained():
    """A customer opens this from an email attachment on a locked-down laptop."""
    page = render(result([agent()]), "acme", T0)
    for external in ("http://", "https://", "<script", "src="):
        assert external not in page, external


# --- change and drift sections ----------------------------------------------

def _diff(changes=(), previous=1):
    from custos.diff import ScanDiff

    d = ScanDiff(previous_scan_id=previous, current_scan_id=2)
    d.changes = list(changes)
    return d


def _change(kind, detail="finance-close went from write to destructive", team="finance"):
    from custos.diff import Change

    return Change(kind=kind, agent_id="agt_1",
                  principal="arn:aws:iam::1:role/finance-close",
                  detail=detail, owner_team=team)


def test_no_change_section_on_a_first_scan():
    """Marking every agent as new is technically true and useless."""
    page = render(result([agent()]), "acme", T0, diff=_diff(previous=None))
    assert "Since the last scan" not in page


def test_a_quiet_week_says_so_rather_than_rendering_empty():
    page = render(result([agent()]), "acme", T0, diff=_diff())
    assert "Nothing changed" in page


def test_an_escalation_is_called_out_above_the_inventory():
    from custos.diff import ChangeKind

    page = render(
        result([agent()]), "acme", T0,
        diff=_diff([_change(ChangeKind.BLAST_RADIUS_INCREASED)]),
    )
    assert page.index("Since the last scan") < page.index("Unsanctioned agents")
    assert "most worth acting on today" in page
    assert "went from write to destructive" in page
    assert "finance" in page


def test_a_new_agent_is_listed_without_the_alarm():
    from custos.diff import ChangeKind

    page = render(
        result([agent()]), "acme", T0,
        diff=_diff([_change(ChangeKind.APPEARED, "kb-indexer appeared for the first time")]),
    )
    assert "kb-indexer appeared" in page
    assert "most worth acting on today" not in page


def test_drift_is_phrased_as_questions_and_claims_nothing():
    from custos.baseline import Drift, DriftKind

    page = render(
        result([agent()]), "acme", T0,
        drift=[Drift(kind=DriftKind.NEW_TOOL, agent_id="agt_1", observed_at=T0,
                     detail="reached deploy-ctl for the first time in 9 scans")],
    )
    assert "Behaviour worth asking about" in page
    assert "Is that expected?" in page
    assert "None of it is evidence of a problem" in page
    for word in ("compromised", "malicious", "breach", "attack"):
        assert word not in page.lower()


def test_no_drift_section_when_there_is_no_drift():
    page = render(result([agent()]), "acme", T0, drift=[])
    assert "Behaviour worth asking about" not in page


def test_change_details_are_escaped():
    from custos.diff import ChangeKind

    page = render(
        result([agent()]), "acme", T0,
        diff=_diff([_change(ChangeKind.APPEARED, "<script>alert('x')</script>")]),
    )
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
