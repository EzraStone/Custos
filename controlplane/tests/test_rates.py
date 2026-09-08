"""What an account pays, and what happens when they have not said."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custos.spend import PRICES, PRICES_REVISION
from custos.store.db import open_database
from custos.store.rates import RateStore

ACCOUNT = "447120043318"
OTHER = "209384756102"
AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def store():
    return RateStore(open_database())


def test_an_account_with_no_rates_gets_the_unverified_table(store):
    rates = store.rates_for(ACCOUNT)
    assert rates.verified is False
    assert rates.revision == PRICES_REVISION
    assert rates.for_provider("anthropic") == PRICES["anthropic"]


def test_a_supplied_rate_is_used_and_dated(store):
    """A rate supplied two years ago is not the same claim as one from
    yesterday, so the revision carries the date rather than only the fact."""
    store.supply(ACCOUNT, "anthropic", 1.5, 7.5, "ezra@custos.dev", at=AT)
    rates = store.rates_for(ACCOUNT)

    assert rates.verified is True
    assert rates.revision == "customer-supplied 2026-09-08"
    assert rates.for_provider("anthropic").input_per_mtok == 1.5


def test_rates_do_not_cross_accounts(store):
    store.supply(ACCOUNT, "anthropic", 1.5, 7.5, "ezra@custos.dev", at=AT)
    assert store.rates_for(OTHER).verified is False


def test_a_zero_rate_is_refused(store):
    """Far more likely an empty form field than a free provider, and a zero
    would make every agent on it look free — the one direction this number
    must never be wrong in."""
    with pytest.raises(ValueError, match="greater than zero"):
        store.supply(ACCOUNT, "anthropic", 0, 7.5, "ezra@custos.dev", at=AT)
    with pytest.raises(ValueError, match="greater than zero"):
        store.supply(ACCOUNT, "anthropic", 1.5, 0, "ezra@custos.dev", at=AT)
    assert store.rates_for(ACCOUNT).verified is False


def test_supplying_a_rate_needs_a_person(store):
    with pytest.raises(ValueError, match="needs a person's name"):
        store.supply(ACCOUNT, "anthropic", 1.5, 7.5, "  ", at=AT)


def test_the_newest_rate_per_provider_wins(store):
    store.supply(ACCOUNT, "anthropic", 3.0, 15.0, "ezra@custos.dev", at=AT)
    store.supply(
        ACCOUNT, "anthropic", 1.5, 7.5, "priya@custos.dev",
        at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    assert store.rates_for(ACCOUNT).for_provider("anthropic").input_per_mtok == 1.5


def test_an_old_rate_is_still_on_the_record(store):
    """A figure in last month's report was computed from the rate in effect
    then, and overwriting the row would make that report unreproducible."""
    store.supply(ACCOUNT, "anthropic", 3.0, 15.0, "ezra@custos.dev", at=AT)
    store.supply(
        ACCOUNT, "anthropic", 1.5, 7.5, "priya@custos.dev",
        at=datetime(2026, 9, 9, tzinfo=UTC),
    )
    history = store.history_for(ACCOUNT)
    assert len(history) == 2
    assert {h["input_per_mtok"] for h in history} == {3.0, 1.5}


def test_a_provider_with_no_rate_falls_back_rather_than_failing(store):
    store.supply(ACCOUNT, "anthropic", 1.5, 7.5, "ezra@custos.dev", at=AT)
    rates = store.rates_for(ACCOUNT)
    assert rates.for_provider("bedrock") == PRICES["bedrock"]
