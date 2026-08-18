from datetime import UTC, datetime, timedelta

from custos_a0.wire.connpool import ConnectionPool, ack_traffic, framed

T0 = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)


def test_framing_adds_overhead_and_scales_with_packets():
    small_bytes, small_pkts = framed(500)
    assert small_pkts == 1
    assert small_bytes > 500

    big_bytes, big_pkts = framed(100_000)
    assert big_pkts > 60
    # Overhead is a few percent, not a multiple.
    assert 1.02 < big_bytes / 100_000 < 1.10


def test_framing_of_nothing_is_nothing():
    assert framed(0) == (0, 0)
    assert ack_traffic(0) == (0, 0)


def test_sequential_calls_reuse_one_connection():
    """The whole reason per-call timing is unrecoverable from flow logs."""
    pool = ConnectionPool()
    ports = {
        pool.acquire("160.79.104.10", 443, T0 + timedelta(seconds=i)).srcport
        for i in range(20)
    }
    assert ports == {32_768}


def test_connection_is_replaced_after_keepalive_expires():
    pool = ConnectionPool(keepalive=timedelta(seconds=90))
    a = pool.acquire("160.79.104.10", 443, T0)
    b = pool.acquire("160.79.104.10", 443, T0 + timedelta(seconds=200))
    assert a.srcport != b.srcport


def test_distinct_destinations_get_distinct_connections():
    pool = ConnectionPool()
    a = pool.acquire("160.79.104.10", 443, T0)
    b = pool.acquire("10.0.4.21", 8080, T0)
    assert a.srcport != b.srcport
