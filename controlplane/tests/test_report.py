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


def test_the_report_names_the_region_it_covered():
    """Otherwise "no unsanctioned agents found" reads as a claim about the
    account when it is a claim about one region of it."""
    page = render(
        result([agent()]), "acme", T0, coverage=Coverage(regions=("us-east-1",))
    )
    assert "covers us-east-1 and no other region" in page
    assert "not about the account" in page


def test_several_regions_are_all_named():
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(regions=("eu-west-1", "us-east-1")),
    )
    assert "eu-west-1, us-east-1" in page


def test_a_scan_with_no_stated_region_claims_nothing_about_regions(page):
    """An older collector never sent one. Saying "covered  and no other region"
    would be worse than saying nothing."""
    assert "no other region" not in page


def test_failed_aws_reads_are_disclosed_beside_the_unattributed_findings():
    """An interface nobody could describe produces a finding with no owner,
    which is exactly what an account with no resource tags produces. Without
    this the report presents our throttling as a fact about their tagging."""
    orphan = agent(principal="arn:aws:iam::1:role/svc0001", team="")
    page = render(result([orphan]), "acme", T0, coverage=Coverage(read_errors=3))

    assert "3 AWS reads failed" in page
    assert "rather than for want of resource tags" in page


def test_one_failed_read_is_not_pluralised():
    page = render(result([agent()]), "acme", T0, coverage=Coverage(read_errors=1))
    assert "1 AWS read failed" in page


def test_a_scan_with_no_failed_reads_says_nothing_about_them(page):
    assert "AWS read" not in page


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


# --- an agent in more than one region -----------------------------------------


def _multi_region(agent_):
    from custos.register.model import RegionalReach

    agent_.regions = {"us-east-1", "eu-west-1"}
    agent_.region_reach = {
        "us-east-1": RegionalReach(tools=frozenset({"billing-api"})),
        "eu-west-1": RegionalReach(tools=frozenset({"ticketing"})),
    }
    agent_.reach.tools = {"billing-api", "ticketing"}
    return agent_


def test_an_agent_seen_in_several_regions_says_where():
    page = render(result([_multi_region(agent())]), "acme", T0)
    assert "eu-west-1, us-east-1" in page


def test_no_caveat_is_attached_to_the_reach_any_more():
    """It used to say "reach from one", because reach was whatever the scan
    that last saw the agent observed. It is the union across regions now, and
    a caveat describing a defect that has been fixed is worse than none."""
    page = render(result([_multi_region(agent())]), "acme", T0)
    assert "reach from one" not in page


def test_an_agent_in_one_region_does_not_get_a_regions_row(page):
    """One region is the ordinary case; printing it on every row would be a
    column of the same word."""
    assert "<dt>Regions</dt>" not in page


def test_a_region_name_is_escaped():
    hostile = agent()
    hostile.regions = {"<script>alert(1)</script>", "us-east-1"}
    assert "<script>alert" not in render(result([hostile]), "acme", T0)


# --- a field missing in one region of several ---------------------------------
#
# The flow log format is set per flow log, and a customer with three regions
# has three of them. A field absent in one is not absent from the account, and
# saying it is turns a fixable gap in one region into a property of the whole
# estate that nobody knows where to fix.

def _multi(missing_in):
    return Coverage(
        regions=("eu-west-1", "us-east-1"),
        missing_fields=tuple(sorted({f for f, _ in missing_in})),
        missing_in=missing_in,
    )


def test_a_field_missing_in_one_region_says_which():
    page = render(
        result([agent()]), "acme", T0,
        coverage=_multi((("dstport", ("us-east-1",)),)),
    )
    assert "Destination ports were not recorded" in page
    assert "us-east-1" in page
    assert "eu-west-1 does record it" in page or "the other region" in page


def test_a_field_missing_everywhere_is_not_qualified():
    """Naming every region covered would be a list of the regions already named
    two sentences up, and it would read as if somewhere still had the field."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=_multi((("dstport", ("eu-west-1", "us-east-1")),)),
    )
    assert "Destination ports were not recorded" in page
    assert "does record it" not in page


def test_a_single_region_banner_does_not_name_it():
    """It is named in the limitations already, and "in us-east-1" on a report
    that covers only us-east-1 reads as if somewhere else read cleanly."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(
            parsed_fraction=0.4, regions=("us-east-1",),
            parse_by_region=(("us-east-1", 0.4),),
        ),
    )
    assert "40% of flow log lines parsed" in page
    assert "parsed in us-east-1" not in page


def test_the_banner_names_the_region_that_read_badly():
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(
            parsed_fraction=0.4, regions=("eu-west-1", "us-east-1"),
            parse_by_region=(("eu-west-1", 1.0), ("us-east-1", 0.4)),
        ),
    )
    assert "40% of flow log lines parsed in us-east-1" in page
    assert "eu-west-1" not in page.split("Incomplete coverage")[1].split("</div>")[0]


def test_a_single_region_report_does_not_explain_the_principal_count():
    """There is nothing to explain: the figure and the register cover the same
    region, and a caveat about a discrepancy that does not exist is noise in
    the section a reader consults to find the ones that do."""
    page = render(
        result([agent()]), "acme", T0, coverage=Coverage(regions=("us-east-1",))
    )
    assert "counts one region" not in page


def test_destinations_we_declined_to_ask_about_are_counted():
    """A report with no findings and no questions is what an account with a
    hidden gateway produces, so every place we chose not to ask has to be
    visible — including the ones excluded by a rule that can be wrong."""
    page = render(result([agent()]), "acme", T0, coverage=Coverage(bulk_senders=5))
    assert "5 internal destinations had the traffic shape of a model gateway" in page
    assert "reach nothing else" in page
    assert "would be excluded by it" in page


def test_declining_nothing_says_nothing():
    page = render(result([agent()]), "acme", T0, coverage=Coverage())
    assert "traffic shape of a model gateway" not in page


def test_one_declined_destination_reads_as_one():
    page = render(result([agent()]), "acme", T0, coverage=Coverage(bulk_senders=1))
    assert "1 internal destination had the traffic shape" in page
    assert "the workloads reaching it reach nothing else" in page


def test_both_regions_tools_are_listed():
    page = render(result([_multi_region(agent())]), "acme", T0)
    assert "billing-api" in page
    assert "ticketing" in page


def test_a_region_where_nothing_resolved_is_marked():
    """The scan that saw this agent there resolved no destinations — usually a
    flow log with no port field. Listing the region unmarked would present a
    gap in the log as a region where the agent touches nothing."""
    from custos.register.model import RegionalReach

    a = _multi_region(agent())
    a.region_reach["eu-west-1"] = RegionalReach()
    page = render(result([a]), "acme", T0)
    assert "nothing resolved" in page


def test_an_agent_with_no_breakdown_is_not_accused_of_resolving_nothing():
    """Every agent discovered before the breakdown existed has an empty one,
    and marking all of their regions would be a claim about a column rather
    than about the account."""
    a = agent()
    a.regions = {"us-east-1", "eu-west-1"}
    page = render(result([a]), "acme", T0)
    assert "nothing resolved" not in page


def test_a_region_with_no_retained_telemetry_is_disclosed():
    """Telemetry is pruned after ninety days and the register is not. An agent
    from a pruned region is listed with figures from a scan that no longer
    exists, and nothing else on the page tells a reader that."""
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(
            regions=("eu-west-1", "us-east-1"), unretained_regions=("eu-west-1",)
        ),
    )
    assert "eu-west-1 appears above because agents were discovered there" in page
    assert "no longer exists" in page


def test_two_unretained_regions_read_as_plural():
    page = render(
        result([agent()]), "acme", T0,
        coverage=Coverage(unretained_regions=("ap-south-1", "eu-west-1")),
    )
    assert "ap-south-1, eu-west-1 appear above" in page
    assert "those regions" in page


def test_nothing_is_said_when_every_region_still_has_telemetry():
    page = render(result([agent()]), "acme", T0, coverage=Coverage(regions=("us-east-1",)))
    assert "no longer exists" not in page
