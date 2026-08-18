from datetime import UTC, datetime, timedelta

from custos.classify.episodes import build_episodes, build_windows, sessionize
from custos.telemetry import ACK, SYN, Direction, FlowRecord, InboundRequest

T0 = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)
MIN = timedelta(seconds=60)


def rec(offset_s, dst, port, direction, nbytes, flags=ACK, svc="", eni="eni-1", srcport=40000):
    return FlowRecord(
        account_id="1", interface_id=eni,
        srcaddr="10.0.20.11" if direction is Direction.EGRESS else dst,
        dstaddr=dst if direction is Direction.EGRESS else "10.0.20.11",
        srcport=srcport if direction is Direction.EGRESS else port,
        dstport=port if direction is Direction.EGRESS else srcport,
        protocol=6, packets=10, bytes=nbytes,
        start=T0 + timedelta(seconds=offset_s), end=T0 + timedelta(seconds=offset_s),
        direction=direction, dst_aws_service=svc, tcp_flags=flags,
    )


def test_windows_separate_model_from_tool_traffic():
    recs = [
        rec(5, "160.79.104.10", 443, Direction.EGRESS, 50_000),
        rec(6, "160.79.104.10", 443, Direction.INGRESS, 2_000),
        rec(7, "10.0.4.21", 8080, Direction.EGRESS, 900),
        rec(8, "10.0.4.21", 8080, Direction.INGRESS, 4_000),
    ]
    (w,) = build_windows(recs, T0, MIN)
    assert (w.model_egress, w.model_ingress) == (50_000, 2_000)
    assert (w.tool_egress, w.tool_ingress) == (900, 4_000)
    assert w.has_model and w.has_tool


def test_unknown_public_destination_counts_as_neither():
    recs = [rec(5, "93.184.216.34", 443, Direction.EGRESS, 50_000)]
    (w,) = build_windows(recs, T0, MIN)
    assert w.model_egress == 0
    assert w.tool_egress == 0


def test_new_connections_counted_once_per_five_tuple():
    recs = [
        rec(5, "160.79.104.10", 443, Direction.EGRESS, 1000, flags=ACK | SYN, srcport=40001),
        rec(6, "160.79.104.10", 443, Direction.EGRESS, 1000, flags=ACK | SYN, srcport=40001),
        rec(7, "160.79.104.10", 443, Direction.EGRESS, 1000, flags=ACK | SYN, srcport=40002),
    ]
    (w,) = build_windows(recs, T0, MIN)
    assert w.model_connections == 2


def test_episodes_split_on_a_gap_beyond_tolerance():
    recs = []
    for minute in (0, 1, 2, 9, 10):
        recs.append(rec(minute * 60 + 5, "160.79.104.10", 443, Direction.EGRESS, 10_000))
    windows = build_windows(recs, T0, MIN)
    episodes = build_episodes(windows, MIN, gap_tolerance=1)
    assert [e.length for e in episodes] == [3, 2]


def test_single_idle_window_does_not_shatter_an_episode():
    """A call straddling a window boundary must not fragment one trajectory."""
    recs = [
        rec(minute * 60 + 5, "160.79.104.10", 443, Direction.EGRESS, 10_000)
        for minute in (0, 2, 4)
    ]
    episodes = build_episodes(build_windows(recs, T0, MIN), MIN, gap_tolerance=1)
    assert [e.length for e in episodes] == [3]


def test_no_model_traffic_yields_no_episodes():
    recs = [rec(5, "10.0.4.21", 8080, Direction.EGRESS, 900)]
    assert build_episodes(build_windows(recs, T0, MIN), MIN) == []


def test_sessionize_groups_by_principal_and_attaches_inbound():
    recs = [
        rec(5, "160.79.104.10", 443, Direction.EGRESS, 10_000, eni="eni-a"),
        rec(5, "160.79.104.10", 443, Direction.EGRESS, 10_000, eni="eni-b"),
    ]
    out = sessionize(
        recs,
        principal_by_eni={"eni-a": "role/alpha", "eni-b": "role/beta"},
        address_by_eni={"eni-a": "10.0.20.11", "eni-b": "10.0.21.11"},
        requests={"10.0.20.11": [InboundRequest(T0, "10.0.20.11", 100, 200)]},
        origin=T0, interval=MIN,
    )
    assert [t.principal for t in out] == ["role/alpha", "role/beta"]
    assert len(out[0].inbound) == 1
    assert out[1].inbound == []


def test_unattributable_eni_is_dropped_not_merged():
    """SEC-20: an ENI with no principal must never be folded into another's
    telemetry, which would corrupt an owned finding with unowned traffic."""
    recs = [rec(5, "160.79.104.10", 443, Direction.EGRESS, 10_000, eni="eni-orphan")]
    out = sessionize(recs, {}, {}, {}, T0, MIN)
    assert out == []
