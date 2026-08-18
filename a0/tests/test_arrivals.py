from datetime import UTC, datetime, timedelta
from random import Random

from custos_a0.arrivals import diurnal, poisson_arrivals, uniform_arrivals

START = datetime(2026, 8, 10, tzinfo=UTC)  # a Monday


def test_diurnal_peaks_in_business_hours():
    assert diurnal(START.replace(hour=15)) > diurnal(START.replace(hour=4)) * 5


def test_diurnal_suppresses_weekend():
    sat = START + timedelta(days=5)
    assert diurnal(sat.replace(hour=15)) < diurnal(START.replace(hour=15))


def test_poisson_is_diurnally_shaped_and_uniform_is_not():
    end = START + timedelta(days=3)
    p = poisson_arrivals(Random(1), START, end, 40.0)
    u = uniform_arrivals(Random(1), START, end, 40.0)

    def night_fraction(ts):
        return sum(1 for t in ts if t.hour < 6) / len(ts)

    # Human traffic is scarce overnight; machine traffic is indifferent to it.
    assert night_fraction(p) < 0.10
    assert night_fraction(u) > 0.20


def test_arrivals_are_deterministic_under_seed():
    end = START + timedelta(days=1)
    assert poisson_arrivals(Random(7), START, end, 10.0) == poisson_arrivals(
        Random(7), START, end, 10.0
    )
