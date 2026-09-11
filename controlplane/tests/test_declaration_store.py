"""Storing declarations, and the two rules that shape the table."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custos.store.db import open_database
from custos.store.declarations import DeclarationStore

ACCOUNT = "447120043318"
OTHER = "209384756102"
AT = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def store():
    return DeclarationStore(open_database())


def test_a_declaration_comes_back_ready_to_classify(store):
    from custos.catalog import DestinationClass
    from custos.declared import classify_with

    store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        "llm-gateway",
        region="us-east-1",
        at=AT,
    )
    declared = store.declared_for(ACCOUNT, "us-east-1")
    assert classify_with(declared, "10.0.7.9", 443) is DestinationClass.MODEL


def test_declarations_do_not_cross_accounts(store):
    store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", region="us-east-1", at=AT)
    assert store.declared_for(OTHER, "us-east-1").empty


def test_declaring_needs_a_person(store):
    """It changes what counts as an agent, which makes it the second decision
    in this system with that property. The first already requires a name."""
    with pytest.raises(ValueError, match="needs a person's name"):
        store.declare(ACCOUNT, "10.0.7.0/24", "range", "   ", region="us-east-1", at=AT)


def test_an_invalid_range_is_rejected_at_write_time(store):
    """Not at read time. A declaration stored and rejected later is one a
    customer believes is in effect while their agents stay invisible, and
    finding out at the next scan is too late."""
    with pytest.raises(ValueError, match="not a valid network"):
        store.declare(ACCOUNT, "10.0.7.0/99", "range", "ezra@custos.dev", region="us-east-1", at=AT)
    assert store.records_for(ACCOUNT) == []


def test_withdrawing_takes_it_out_of_effect(store):
    record = store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        region="us-east-1",
        at=AT,
    )
    assert store.withdraw(record.id, ACCOUNT, "priya@custos.dev", at=AT) is True
    assert store.declared_for(ACCOUNT, "us-east-1").empty


def test_a_withdrawn_declaration_is_still_on_the_record(store):
    """Withdrawing narrows what counts as a model endpoint, so unlike
    declaring it can make a finding disappear — which is exactly why the
    record of it has to survive."""
    record = store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        region="us-east-1",
        at=AT,
    )
    store.withdraw(record.id, ACCOUNT, "priya@custos.dev", at=AT)

    assert store.records_for(ACCOUNT) == []
    history = store.records_for(ACCOUNT, include_withdrawn=True)
    assert len(history) == 1
    assert history[0].withdrawn_by == "priya@custos.dev"
    assert history[0].active is False


def test_withdrawing_someone_elses_declaration_does_nothing(store):
    record = store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        region="us-east-1",
        at=AT,
    )
    assert store.withdraw(record.id, OTHER, "attacker", at=AT) is False
    assert not store.declared_for(ACCOUNT, "us-east-1").empty


def test_withdrawing_twice_is_reported_as_no_change(store):
    record = store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        region="us-east-1",
        at=AT,
    )
    assert store.withdraw(record.id, ACCOUNT, "priya@custos.dev", at=AT) is True
    assert store.withdraw(record.id, ACCOUNT, "someone-else", at=AT) is False
    # And the first withdrawal is the one on the record.
    assert store.records_for(ACCOUNT, include_withdrawn=True)[0].withdrawn_by == "priya@custos.dev"


def test_withdrawing_needs_a_person_too(store):
    record = store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        region="us-east-1",
        at=AT,
    )
    with pytest.raises(ValueError, match="needs a person's name"):
        store.withdraw(record.id, ACCOUNT, "", at=AT)


def test_who_declared_it_is_kept(store):
    store.declare(
        ACCOUNT,
        "10.0.7.0/24",
        "range",
        "ezra@custos.dev",
        "llm-gateway",
        region="us-east-1",
        at=AT,
    )
    record = store.records_for(ACCOUNT)[0]
    assert record.declared_by == "ezra@custos.dev"
    assert record.note == "llm-gateway"
    assert record.declared_at == AT


# --- a private address means a different host in every region ------------------


def test_a_private_range_declared_everywhere_is_refused(store):
    """The one shape this must refuse. 10.0.7.40 is the model gateway in
    us-east-1 and, in every other region the account runs in, whatever happens
    to live at that address — so declaring it everywhere turns ordinary
    internal traffic into model traffic.

    That does not hide agents, it invents them, which is the direction this
    system is least able to recover from.
    """
    with pytest.raises(ValueError, match="different things in different regions"):
        store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)

    assert store.records_for(ACCOUNT) == []


def test_a_public_range_needs_no_region(store):
    """A published provider range means the same thing everywhere."""
    record = store.declare(
        ACCOUNT, "160.79.104.0/23", "range", "ezra@custos.dev", at=AT
    )
    assert record.region == ""


def test_an_aws_service_needs_no_region(store):
    """A service name is a service name in every region."""
    record = store.declare(ACCOUNT, "BEDROCK", "aws_service", "ezra@custos.dev", at=AT)
    assert record.region == ""


def test_a_region_scoped_declaration_applies_only_there(store):
    from custos.catalog import DestinationClass
    from custos.declared import classify_with

    store.declare(
        ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", region="us-east-1", at=AT
    )

    here = store.declared_for(ACCOUNT, "us-east-1")
    assert classify_with(here, "10.0.7.9", 443) is DestinationClass.MODEL

    elsewhere = store.declared_for(ACCOUNT, "eu-west-1")
    assert classify_with(elsewhere, "10.0.7.9", 443) is not DestinationClass.MODEL


def test_an_everywhere_declaration_applies_in_every_region(store):
    from custos.catalog import DestinationClass
    from custos.declared import classify_with

    store.declare(ACCOUNT, "160.79.104.0/23", "range", "ezra@custos.dev", at=AT)

    for region in ("us-east-1", "eu-west-1", ""):
        declared = store.declared_for(ACCOUNT, region)
        assert classify_with(declared, "160.79.104.10", 443) is DestinationClass.MODEL


def test_asking_without_a_region_returns_only_the_everywhere_ones(store):
    """The safe reading. A caller that does not know which region it is
    classifying must not be handed a private address that means something
    different in each."""
    store.declare(
        ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", region="us-east-1", at=AT
    )
    assert store.declared_for(ACCOUNT).empty


# --- questions from every region ----------------------------------------------

def _candidate(address, region):
    from custos.gateway import Candidate

    return Candidate(
        address=address, egress=5_000_000, ingress=1_000_000,
        principals=("role/a",), blind_principals=("role/a",), interleave=0.9,
    ), region


@pytest.fixture
def candidates():
    """A candidate store over a database with three real scans in it.

    Real scans because gateway_candidates has a foreign key onto them and is
    pruned with them: a question about traffic from three months ago is not a
    question anybody should still be answering.
    """
    from datetime import timedelta

    from custos.store.declarations import CandidateStore
    from custos.store.scans import ScanStore

    conn = open_database()
    scans = ScanStore(conn)
    for i, region in enumerate(("us-east-1", "eu-west-1", "us-east-1")):
        batch = scans.record_batch(
            account_id=ACCOUNT, region=region,
            window_start=AT + timedelta(hours=i), window_end=AT + timedelta(hours=i + 1),
            collector="t", received_at=AT + timedelta(hours=i + 1),
            flow_records=1, requests=0, have_alb_logs=True,
        )
        scans.record_scan(
            batch_id=batch.id, account_id=ACCOUNT, started_at=AT + timedelta(hours=i),
            principals_seen=1, agents_found=0, review_candidates=0, coverage=1.0,
            truncated=False, catalogue_revision="r", regions=(region,),
        )
    return CandidateStore(conn)


def test_every_regions_open_questions_are_kept(candidates):
    """A scan is one region's window. Taking the highest scan id across the
    account means the questions about eu-west-1 disappear the moment us-east-1
    is collected — so a customer running one collector per region sees half
    their open questions, alternating."""
    store = candidates
    east, _ = _candidate("10.0.7.40", "us-east-1")
    west, _ = _candidate("10.1.7.40", "eu-west-1")
    store.record(1, ACCOUNT, [east], region="us-east-1")
    store.record(2, ACCOUNT, [west], region="eu-west-1")
    # A third scan, of us-east-1 again, with a fresher question.
    store.record(3, ACCOUNT, [east], region="us-east-1")

    asked = store.latest_for(ACCOUNT)
    assert {(c["address"], c["region"], c["scan_id"]) for c in asked} == {
        ("10.1.7.40", "eu-west-1", 2),
        ("10.0.7.40", "us-east-1", 3),
    }


def test_a_regions_older_questions_are_not_shown_beside_its_newer_ones(candidates):
    """Within a region the newest scan replaces the last, or an address a
    customer answered last week comes back beside this week's numbers."""
    store = candidates
    old, _ = _candidate("10.0.7.40", "us-east-1")
    new, _ = _candidate("10.0.7.99", "us-east-1")
    store.record(1, ACCOUNT, [old], region="us-east-1")
    store.record(2, ACCOUNT, [new], region="us-east-1")

    assert [c["address"] for c in store.latest_for(ACCOUNT)] == ["10.0.7.99"]


def test_an_account_with_no_questions_has_none(candidates):
    assert candidates.latest_for(ACCOUNT) == []


def test_questions_are_ranked_across_regions_not_grouped_by_them(candidates):
    """A list read top-down is a list whose order answers "which of these
    first". Ordering by region put eu-west-1's weakest question above
    us-east-1's strongest for no reason but the alphabet."""
    from custos.gateway import Candidate

    loud = Candidate(
        address="10.0.7.40", egress=90_000_000, ingress=10_000_000,
        principals=("role/a", "role/b"), blind_principals=("role/a", "role/b"),
        interleave=0.9,
    )
    quiet = Candidate(
        address="10.1.7.40", egress=2_000_000, ingress=500_000,
        principals=("role/c",), blind_principals=("role/c",), interleave=0.9,
    )
    candidates.record(1, ACCOUNT, [loud], region="us-east-1")
    candidates.record(2, ACCOUNT, [quiet], region="eu-west-1")

    assert [c["address"] for c in candidates.latest_for(ACCOUNT)] == [
        "10.0.7.40", "10.1.7.40",
    ]
