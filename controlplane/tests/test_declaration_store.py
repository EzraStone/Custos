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

    store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", "llm-gateway", at=AT)
    declared = store.declared_for(ACCOUNT)
    assert classify_with(declared, "10.0.7.9", 443) is DestinationClass.MODEL


def test_declarations_do_not_cross_accounts(store):
    store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)
    assert store.declared_for(OTHER).empty


def test_declaring_needs_a_person(store):
    """It changes what counts as an agent, which makes it the second decision
    in this system with that property. The first already requires a name."""
    with pytest.raises(ValueError, match="needs a person's name"):
        store.declare(ACCOUNT, "10.0.7.0/24", "range", "   ", at=AT)


def test_an_invalid_range_is_rejected_at_write_time(store):
    """Not at read time. A declaration stored and rejected later is one a
    customer believes is in effect while their agents stay invisible, and
    finding out at the next scan is too late."""
    with pytest.raises(ValueError, match="not a valid network"):
        store.declare(ACCOUNT, "10.0.7.0/99", "range", "ezra@custos.dev", at=AT)
    assert store.records_for(ACCOUNT) == []


def test_withdrawing_takes_it_out_of_effect(store):
    record = store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)
    assert store.withdraw(record.id, ACCOUNT, "priya@custos.dev", at=AT) is True
    assert store.declared_for(ACCOUNT).empty


def test_a_withdrawn_declaration_is_still_on_the_record(store):
    """Withdrawing narrows what counts as a model endpoint, so unlike
    declaring it can make a finding disappear — which is exactly why the
    record of it has to survive."""
    record = store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)
    store.withdraw(record.id, ACCOUNT, "priya@custos.dev", at=AT)

    assert store.records_for(ACCOUNT) == []
    history = store.records_for(ACCOUNT, include_withdrawn=True)
    assert len(history) == 1
    assert history[0].withdrawn_by == "priya@custos.dev"
    assert history[0].active is False


def test_withdrawing_someone_elses_declaration_does_nothing(store):
    record = store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)
    assert store.withdraw(record.id, OTHER, "attacker", at=AT) is False
    assert not store.declared_for(ACCOUNT).empty


def test_withdrawing_twice_is_reported_as_no_change(store):
    record = store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)
    assert store.withdraw(record.id, ACCOUNT, "priya@custos.dev", at=AT) is True
    assert store.withdraw(record.id, ACCOUNT, "someone-else", at=AT) is False
    # And the first withdrawal is the one on the record.
    assert store.records_for(ACCOUNT, include_withdrawn=True)[0].withdrawn_by == "priya@custos.dev"


def test_withdrawing_needs_a_person_too(store):
    record = store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", at=AT)
    with pytest.raises(ValueError, match="needs a person's name"):
        store.withdraw(record.id, ACCOUNT, "", at=AT)


def test_who_declared_it_is_kept(store):
    store.declare(ACCOUNT, "10.0.7.0/24", "range", "ezra@custos.dev", "llm-gateway", at=AT)
    record = store.records_for(ACCOUNT)[0]
    assert record.declared_by == "ezra@custos.dev"
    assert record.note == "llm-gateway"
    assert record.declared_at == AT
