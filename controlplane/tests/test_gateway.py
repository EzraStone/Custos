"""Finding a model gateway nobody mentioned."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from custos.classify.episodes import sessionize
from custos.gateway import Candidate, blind_reach, candidates
from custos.telemetry import Direction, FlowRecord

START = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _flows(eni: str, peer: str, minutes: int, out: int, back: int, port: int = 443):
    records = []
    for minute in range(minutes):
        at = START + timedelta(minutes=minute)
        records.append(FlowRecord(
            account_id="1", interface_id=eni, srcaddr="10.0.1.5", dstaddr=peer,
            srcport=41000 + minute, dstport=port, protocol=6, packets=40, bytes=out,
            start=at, end=at + timedelta(seconds=30), direction=Direction.EGRESS,
        ))
        records.append(FlowRecord(
            account_id="1", interface_id=eni, srcaddr=peer, dstaddr="10.0.1.5",
            srcport=port, dstport=41000 + minute, protocol=6, packets=8, bytes=back,
            start=at, end=at + timedelta(seconds=30), direction=Direction.INGRESS,
        ))
    return records


def _telemetry(records, principals):
    return sessionize(records, principals, {}, {}, origin=START,
                      interval=timedelta(seconds=60))


def test_a_hidden_gateway_is_offered_as_a_question():
    """The case this exists for. A workload sending a transcript-shaped stream
    to an internal address and reaching no model provider we recognise."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    found = candidates(_telemetry(records, {"eni-1": "role/agent"}))
    assert [c.address for c in found] == ["10.0.7.9"]
    assert found[0].blind_principals == ("role/agent",)


def test_an_ordinary_internal_api_is_not_offered():
    # Roughly symmetric traffic is a request/response API, not a transcript.
    records = _flows("eni-1", "10.0.4.23", 40, 40_000, 38_000)
    assert candidates(_telemetry(records, {"eni-1": "role/app"})) == []


def test_a_quiet_destination_is_not_worth_asking_about():
    # A megabyte over a whole window is a health check.
    records = _flows("eni-1", "10.0.7.9", 5, 1_000, 10)
    assert candidates(_telemetry(records, {"eni-1": "role/app"})) == []


def test_a_workload_that_already_reaches_a_model_is_not_evidence():
    """Its traffic to an internal address is tool calls. Nothing is hidden, and
    asking would be noise on an account that is working correctly."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    records += _flows("eni-1", "160.79.104.10", 40, 100_000, 8_000)
    assert candidates(_telemetry(records, {"eni-1": "role/agent"})) == []


def test_a_gateway_several_workloads_share_ranks_above_one_they_do_not():
    """The alternative explanation — this workload has an unusual internal API
    — gets weaker with every workload that shares the destination."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    records += _flows("eni-2", "10.0.7.9", 40, 140_000, 9_000)
    records += _flows("eni-3", "10.0.8.9", 40, 400_000, 9_000)

    found = candidates(_telemetry(records, {
        "eni-1": "role/a", "eni-2": "role/b", "eni-3": "role/c",
    }))
    assert [c.address for c in found] == ["10.0.7.9", "10.0.8.9"]
    assert len(found[0].blind_principals) == 2


def test_a_datastore_is_never_a_gateway_candidate():
    # It classifies as a datastore by port, and this only ever asks about
    # destinations the catalogue calls an internal API.
    records = _flows("eni-1", "10.0.9.44", 40, 140_000, 9_000, port=5432)
    assert candidates(_telemetry(records, {"eni-1": "role/app"})) == []


def test_a_recognised_model_endpoint_is_never_a_candidate():
    records = _flows("eni-1", "160.79.104.10", 40, 140_000, 9_000)
    assert candidates(_telemetry(records, {"eni-1": "role/agent"})) == []


def test_the_question_says_what_was_measured():
    """It is put to a person who has to check it, so it carries the numbers
    they would check rather than a verdict they would have to trust."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    question = candidates(_telemetry(records, {"eni-1": "role/agent"}))[0].question
    assert "10.0.7.9" in question
    assert "MB" in question and ":1" in question
    assert question.endswith("Is it a model gateway?")


def test_nothing_is_offered_for_an_account_with_no_internal_traffic():
    records = _flows("eni-1", "160.79.104.10", 40, 140_000, 9_000)
    assert candidates(_telemetry(records, {"eni-1": "role/agent"})) == []


# --- the join with the review band --------------------------------------------


def test_blind_reach_inverts_the_candidate_list():
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    records += _flows("eni-2", "10.0.7.9", 40, 140_000, 9_000)
    found = candidates(_telemetry(records, {"eni-1": "role/a", "eni-2": "role/b"}))

    assert blind_reach(found) == {
        "role/a": ("10.0.7.9",),
        "role/b": ("10.0.7.9",),
    }


def test_a_workload_reaching_two_candidates_carries_both_strongest_first():
    """Candidate order, not alphabetical. The first address is the one a
    caller offers as the thing to declare, so it has to be the better
    question — here the one carrying more volume."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    records += _flows("eni-1", "10.0.8.9", 40, 140_000, 9_000)
    # A second workload reaches only 10.0.8.9, which makes it the better
    # question: the "this one workload has an unusual internal API"
    # explanation gets weaker with every workload that shares a destination.
    records += _flows("eni-2", "10.0.8.9", 40, 140_000, 9_000)
    found = candidates(_telemetry(records, {"eni-1": "role/a", "eni-2": "role/b"}))

    assert [c.address for c in found] == ["10.0.8.9", "10.0.7.9"]
    assert blind_reach(found)["role/a"] == ("10.0.8.9", "10.0.7.9")


def test_a_workload_with_recognised_model_traffic_is_not_in_the_reach():
    """Its traffic to an internal address is tool calls. Nothing is hidden, so
    correlating it with a review verdict would be a false lead."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    records += _flows("eni-2", "10.0.7.9", 40, 140_000, 9_000)
    # eni-2 also talks to a provider we recognise, so it is not blind.
    records += _flows("eni-2", "160.79.104.10", 20, 90_000, 6_000)

    found = candidates(_telemetry(records, {"eni-1": "role/a", "eni-2": "role/b"}))
    assert "role/b" not in blind_reach(found)


def test_blind_reach_of_nothing_is_nothing():
    assert blind_reach([]) == {}


def test_a_stored_candidate_rebuilds_into_the_same_thing():
    """The join has to work on questions read back from the store, which is
    where every caller that matters gets them."""
    records = _flows("eni-1", "10.0.7.9", 40, 140_000, 9_000)
    original = candidates(_telemetry(records, {"eni-1": "role/a"}))[0]
    row = {
        "address": original.address, "egress": original.egress,
        "ingress": original.ingress, "principals": list(original.principals),
        "blind_principals": list(original.blind_principals),
    }
    assert Candidate.from_row(row) == original
