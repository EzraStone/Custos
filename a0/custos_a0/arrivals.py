"""Arrival processes.

Human-driven traffic is diurnal and weekday-weighted. Machine-driven traffic is
not. That difference is a weak classifier signal on its own — the specification
correctly rates "sustained off-hours activity" as weak — but it has to be
modelled correctly, because getting it wrong would make the corpus separable for
a reason that would not hold in production.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from random import Random


def diurnal(t: datetime) -> float:
    """Traffic multiplier in (0, 1] for the hour of `t`.

    Gaussian around 15:00 UTC with a weekend suppression. The exact shape does
    not matter; what matters is that human traffic has one and machine traffic
    does not.
    """
    h = t.hour + t.minute / 60
    v = 0.08 + 0.92 * math.exp(-((h - 15) ** 2) / (2 * 4.2**2))
    if t.weekday() >= 5:
        v *= 0.25
    return v


def poisson_arrivals(
    rng: Random, start: datetime, end: datetime, peak_per_hour: float
) -> list[datetime]:
    """Draw arrival times from a diurnally modulated Poisson process."""
    out: list[datetime] = []
    t = start
    while t < end:
        rate = max(peak_per_hour * diurnal(t), 0.01)
        t += timedelta(hours=rng.expovariate(1.0) / rate)
        if t < end:
            out.append(t)
    return out


def uniform_arrivals(
    rng: Random, start: datetime, end: datetime, per_hour: float
) -> list[datetime]:
    """Draw arrival times from a flat Poisson process with no diurnal shape.

    Used for machine-triggered workloads: queue consumers, cron fan-out, CI.
    """
    out: list[datetime] = []
    t = start
    while t < end:
        t += timedelta(hours=rng.expovariate(1.0) / max(per_hour, 0.01))
        if t < end:
            out.append(t)
    return out


def jitter(rng: Random, d: timedelta, frac: float) -> timedelta:
    """Scale `d` by a random factor in [1-frac, 1+frac]."""
    return d * (1 - frac + 2 * frac * rng.random())
