"""Baselines and drift.

The tests are weighted toward the ways this must stay quiet. A drift detector
that fires on ordinary variation trains its reader to ignore it, and then it
protects nothing.
"""

from datetime import UTC, datetime, timedelta

from custos.baseline import (
    MIN_OBSERVATIONS,
    DriftKind,
    build,
    detect,
    detect_from_history,
)

T0 = datetime(2026, 8, 10, tzinfo=UTC)


def obs(rate=10.0, tools=(), hours=(9, 10, 11), day=0):
    return {
        "calls_per_hour": rate,
        "tools": set(tools),
        "active_hours": {h: 1.0 for h in hours},
        "observed_at": T0 + timedelta(days=day),
    }


def history(n, **kw):
    return [obs(day=i, **kw) for i in range(n)]


def test_a_young_agent_has_no_baseline():
    """Two data points are not a pattern. Reporting a departure from them
    manufactures findings out of ordinary variation."""
    baseline = build("agt_1", history(MIN_OBSERVATIONS - 1))
    assert not baseline.established
    assert detect(baseline, obs(rate=1000, tools=("brand-new",)), T0) == []


def test_an_established_baseline_summarises_normal():
    baseline = build("agt_1", history(10, tools=("billing-api",)))
    assert baseline.established
    assert baseline.tool_set == {"billing-api"}
    assert baseline.active_hours == {9, 10, 11}
    assert baseline.calls_per_hour_p50 == 10.0


def test_a_new_tool_is_the_strongest_finding():
    baseline = build("agt_1", history(10, tools=("billing-api",)))
    found = detect(baseline, obs(tools=("billing-api", "deploy-ctl")), T0)
    assert [d.kind for d in found] == [DriftKind.NEW_TOOL]
    assert "deploy-ctl" in found[0].detail


def test_findings_are_phrased_as_questions_to_the_owner():
    """A question gets answered. An accusation gets argued with."""
    baseline = build("agt_1", history(10, tools=("billing-api",)))
    found = detect(baseline, obs(tools=("billing-api", "deploy-ctl")), T0)
    assert found[0].question.endswith("Is that expected?")
    for word in ("compromised", "malicious", "attack", "breach"):
        assert word not in found[0].question.lower()


def test_a_familiar_tool_is_not_drift():
    baseline = build("agt_1", history(10, tools=("billing-api", "orders-db")))
    assert detect(baseline, obs(tools=("billing-api",)), T0) == []


def test_ordinary_variation_does_not_fire():
    """Model workloads are spiky. If this fires, the section gets skipped."""
    varied = [obs(rate=r, day=i) for i, r in enumerate([8, 12, 9, 15, 11, 10, 13, 9])]
    baseline = build("agt_1", varied)
    assert detect(baseline, obs(rate=16.0), T0) == []


def test_a_genuine_spike_fires():
    varied = [obs(rate=r, day=i) for i, r in enumerate([8, 12, 9, 15, 11, 10, 13, 9])]
    baseline = build("agt_1", varied)
    found = detect(baseline, obs(rate=400.0), T0)
    assert any(d.kind is DriftKind.VOLUME_SPIKE for d in found)


def test_activity_outside_usual_hours_fires():
    baseline = build("agt_1", history(10, hours=(9, 10, 11)))
    found = detect(baseline, obs(hours=(3,)), T0)
    assert any(d.kind is DriftKind.OFF_PATTERN_HOURS for d in found)
    assert "03:00" in next(
        d.detail for d in found if d.kind is DriftKind.OFF_PATTERN_HOURS
    )


def test_findings_are_ordered_by_severity():
    baseline = build("agt_1", history(10, tools=("billing-api",), hours=(9,)))
    found = detect(
        baseline, obs(rate=9999, tools=("billing-api", "deploy-ctl"), hours=(3,)), T0
    )
    assert [d.kind for d in found] == [
        DriftKind.NEW_TOOL, DriftKind.OFF_PATTERN_HOURS, DriftKind.VOLUME_SPIKE
    ]


# A baseline that includes the observation being judged drags toward it, which
# is exactly how a slow drift stays invisible: every step looks normal against
# a baseline that already absorbed it.
def test_the_tested_observation_is_excluded_from_its_own_baseline():
    records = history(10, tools=("billing-api",))
    records.append(obs(tools=("billing-api", "deploy-ctl"), day=11))

    baseline, found = detect_from_history("agt_1", records)
    assert "deploy-ctl" not in baseline.tool_set
    assert any(d.kind is DriftKind.NEW_TOOL for d in found)


def test_detect_from_history_tolerates_an_empty_history():
    baseline, found = detect_from_history("agt_1", [])
    assert not baseline.established
    assert found == []


def test_a_single_observation_yields_no_findings():
    _, found = detect_from_history("agt_1", [obs()])
    assert found == []


def test_an_agent_with_no_prior_hours_does_not_report_hour_drift():
    """An agent whose history recorded no active hours has nothing to depart
    from, and inventing a departure would be a finding from missing data."""
    baseline = build("agt_1", [obs(hours=(), day=i) for i in range(10)])
    found = detect(baseline, obs(hours=(3,)), T0)
    assert not any(d.kind is DriftKind.OFF_PATTERN_HOURS for d in found)
