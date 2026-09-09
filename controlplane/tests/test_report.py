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
from custos.report import Coverage, Question, Review, render
from custos.scan import ScanResult

from .conftest import prose

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


# --- what the account's flow log could not answer -----------------------------


def test_a_format_without_ports_says_why_there_are_no_mcp_servers():
    """Otherwise the report's silence on MCP servers reads as a finding rather
    than as a field nobody recorded."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(missing_fields=("dstport",)),
    )
    assert "Destination ports were not recorded" in page
    assert "a gap in the log, not a finding" in page


def test_a_complete_format_says_none_of_that(page):
    assert "were not recorded by this account" not in page
    assert "does not record direction" not in page


def test_records_dropped_for_want_of_a_direction_are_disclosed():
    """The parse counters cannot show these: the lines parsed. They were
    dropped afterwards, and their bytes are in no figure in the report."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(direction_undecided=4210),
    )
    assert "4,210 flow records were discarded" in page
    assert "Their bytes are in no figure above" in page


def test_public_ipv6_destinations_are_disclosed_as_a_blind_spot():
    """An agent reaching a provider over IPv6 produces no finding at all, so a
    report with no findings on a dual-stack account means much less than it
    appears to."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(ipv6_destinations=12),
    )
    assert "12 of the public destinations reached were IPv6" in page
    assert "catalogue is IPv4 only" in page


def test_an_ipv4_only_account_is_told_none_of_that(page):
    assert "were IPv6" not in page


def test_an_unrecognised_missing_field_is_not_rendered_as_a_sentence():
    """SEC-23 at the render: the list arrives from a batch, and only names we
    have a stated cost for become prose in a customer's document."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(missing_fields=("acme-internal-tag",)),
    )
    assert "acme-internal-tag" not in page


# --- the review band --------------------------------------------------------


def test_the_questions_section_asks_rather_than_concludes():
    """The one section of the report that is a question. A workload behind an
    undeclared gateway produces no finding at all, so an account with one looks
    clean — and a clean report above an open question here means much less than
    a clean report alone."""
    page = render(
        result([agent()]), "acme", T0,
        questions=[Question(address="10.0.7.40",
                            question="10.0.7.40 received 40.0MB. Is it a model gateway?",
                            reached_by=("arn:aws:iam::1:role/deploy-remediation",))],
    )
    assert "<h2>Questions</h2>" in page
    assert "Is it a model gateway?" in page
    assert "deploy-remediation" in page
    assert "declare it and the next scan" in page


def test_no_questions_section_when_there_is_nothing_to_ask(page):
    """A section that appears whatever happened is one a reader skips."""
    assert "<h2>Questions</h2>" not in page


def test_a_question_never_reads_as_a_finding():
    """A heuristic that promoted an internal address to a model endpoint would
    manufacture agents out of any busy internal service."""
    page = render(
        result(), "acme", T0,
        questions=[Question(address="10.0.7.40", question="Is it a model gateway?")],
    )
    assert "No unsanctioned agents found." in page
    assert "needs an answer" in page


def test_a_maybe_seen_once_does_not_say_how_often():
    page = render(
        result([agent()]), "acme", T0,
        reviews=[Review(principal="role/ci-runner", confidence=0.5)],
    )
    assert "Seen in" not in page


def test_an_address_in_a_question_is_escaped():
    """It reaches the page from telemetry, which is attacker-influenced."""
    page = render(
        result([agent()]), "acme", T0,
        questions=[Question(address='<script>alert("x")</script>',
                            question='<script>alert("q")</script>',
                            reached_by=('<script>alert("p")</script>',))],
    )
    assert "<script>alert" not in page


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
    assert "Nothing changed" in prose(page)


def test_an_escalation_is_called_out_above_the_inventory():
    from custos.diff import ChangeKind

    page = render(
        result([agent()]), "acme", T0,
        diff=_diff([_change(ChangeKind.BLAST_RADIUS_INCREASED)]),
    )
    assert page.index("Since the last scan") < page.index("Unsanctioned agents")
    assert "most worth acting on today" in prose(page)
    assert "went from write to destructive" in page
    assert "finance" in page


def test_a_new_agent_is_listed_without_the_alarm():
    from custos.diff import ChangeKind

    page = render(
        result([agent()]), "acme", T0,
        diff=_diff([_change(ChangeKind.APPEARED, "kb-indexer appeared for the first time")]),
    )
    assert "kb-indexer appeared" in page
    assert "most worth acting on today" not in prose(page)


def test_drift_is_phrased_as_questions_and_claims_nothing():
    from custos.baseline import Drift, DriftKind

    page = render(
        result([agent()]), "acme", T0,
        drift=[Drift(kind=DriftKind.NEW_TOOL, agent_id="agt_1", observed_at=T0,
                     detail="reached deploy-ctl for the first time in 9 scans")],
    )
    assert "Behaviour worth asking about" in page
    assert "Is that expected?" in page
    assert "None of it is evidence of a problem" in prose(page)
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


# --- coverage -----------------------------------------------------------------

def test_complete_coverage_shows_no_banner():
    from custos.report import Coverage

    page = render(result([agent()]), "acme", T0, coverage=Coverage())
    assert "Incomplete coverage" not in page


def test_partial_parsing_is_banner_worthy():
    """A caveat at the bottom is a caveat nobody reads before concluding."""
    from custos.report import Coverage

    page = render(result([agent()]), "acme", T0, coverage=Coverage(parsed_fraction=0.4))
    assert "Incomplete coverage" in page
    assert "40% of flow log lines parsed" in prose(page)
    assert page.index("Incomplete coverage") < page.index("Unsanctioned agents")


def test_truncation_and_dropped_records_are_both_named():
    from custos.report import Coverage

    page = prose(render(
        result([agent()]), "acme", T0,
        coverage=Coverage(truncated=True, skipped_records=1234),
    ))
    assert "truncated at its record limit" in page
    assert "dropped 1,234 records" in page


def test_the_banner_says_what_incompleteness_means():
    from custos.report import Coverage

    page = prose(render(result([agent()]), "acme", T0, coverage=Coverage(truncated=True)))
    assert "an absence of findings means less here" in page


def test_an_unreadable_scope_gets_its_own_banner():
    """Separate from the coverage banner, because it is a separate problem.

    Incomplete coverage says findings may be missing. An unreadable scope says
    every finding is present and correct and the approval decision on each one
    is a list of IP addresses. Folding them together would let a reader
    discharge both with one glance.
    """
    from custos.report.html import Coverage

    html = render(
        result(), "447120043318", datetime(2026, 8, 20, tzinfo=UTC),
        coverage=Coverage(scope_named=1, scope_total=5),
    )
    assert "Scope is mostly addresses" in html
    assert "1 of 5 internal destinations" in html
    assert "findings below are unaffected" in html.lower() or "unaffected" in html


def test_a_readable_scope_gets_no_banner():
    from custos.report.html import Coverage

    html = render(
        result(), "447120043318", datetime(2026, 8, 20, tzinfo=UTC),
        coverage=Coverage(scope_named=4, scope_total=5),
    )
    assert "Scope is mostly addresses" not in html


def test_no_internal_destinations_is_not_an_unreadable_scope():
    # A scan that reached nothing internal has no unreadable scope. Warning
    # here would be the empty-read-as-full-coverage mistake in reverse.
    from custos.report.html import Coverage

    html = render(
        result(), "447120043318", datetime(2026, 8, 20, tzinfo=UTC),
        coverage=Coverage(scope_named=0, scope_total=0),
    )
    assert "Scope is mostly addresses" not in html


def test_a_report_says_whether_any_endpoint_was_declared():
    """A finding is only as good as the catalogue that produced it. A reader
    who does not know whether a gateway was declared cannot tell an account
    with no agents from an account whose agents are all behind one."""
    html = render(
        result(), "447120043318", datetime(2026, 8, 20, tzinfo=UTC),
        declared=["10.0.7.0/24 (llm-gateway)"],
    )
    assert "10.0.7.0/24 (llm-gateway)" in html
    assert "somebody said so" in html


def test_a_report_with_no_declarations_says_what_that_costs():
    # The more important half. Silence here reads as "there is nothing to
    # declare", which is a claim nobody has checked.
    html = render(result(), "447120043318", datetime(2026, 8, 20, tzinfo=UTC))
    assert "declared no additional model endpoints" in html
    assert "self-hosted gateway" in html


def test_placeholder_pricing_is_declared_and_says_how_to_fix_it():
    html = render(result(), "447120043318", datetime(2026, 8, 20, tzinfo=UTC))
    assert "unverified placeholder pricing" in html
    assert "custos set-rate" in html


def test_customer_rates_are_credited_and_still_called_estimates():
    """An account that supplied its rates should not read a caveat saying the
    figures came from a placeholder — and should still be told the numbers are
    derived from wire bytes rather than token counts."""
    from custos.scan import ScanResult

    priced = ScanResult(
        register=result().register, verdicts=[], telemetry=[],
        prices_revision="customer-supplied 2026-09-08",
    )
    html = render(priced, "447120043318", datetime(2026, 8, 20, tzinfo=UTC))

    assert "rates this account supplied" in html
    assert "customer-supplied 2026-09-08" in html
    assert "unverified placeholder pricing" not in html
    assert "remain estimates" in html
