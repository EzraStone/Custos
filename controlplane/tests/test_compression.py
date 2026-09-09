"""A compressed batch, and what must not get through with it.

The collector compresses because one window at its own record limit is 500,000
flow records — 203MB of JSON, 6.2MB gzipped. Starlette decompresses responses
and not requests, so without the middleware that batch reaches Pydantic as
bytes that are not JSON and is refused as malformed, which looks exactly like a
collector bug.
"""

from __future__ import annotations

import gzip
import json
import zlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from custos.api import TokenStore, create_app
from custos.api.compression import MAX_DECOMPRESSED
from custos.store.db import open_database

ACCOUNT = "447120043318"
TOKEN = "tok-acme"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
W0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def client():
    app = create_app(conn=open_database(), tokens=TokenStore({TOKEN: ACCOUNT}))
    return TestClient(app)


def batch(start=W0):
    return {
        "account_id": ACCOUNT,
        "region": "us-east-1",
        "window_start": start.isoformat(),
        "window_end": (start + timedelta(hours=1)).isoformat(),
        "collector_version": "test",
        "flows": [], "requests": [], "principals": [], "attachments": [],
    }


def gzipped(payload: dict) -> bytes:
    return gzip.compress(json.dumps(payload).encode())


HEADERS = AUTH | {"Content-Type": "application/json", "Content-Encoding": "gzip"}


def test_a_compressed_batch_is_accepted(client):
    response = client.post("/v1/batches", content=gzipped(batch()), headers=HEADERS)
    assert response.status_code == 202
    assert response.json()["duplicate"] is False


def test_a_compressed_batch_is_the_same_batch(client):
    """The middleware must not change what was sent, only how it arrived."""
    client.post("/v1/batches", content=gzipped(batch()), headers=HEADERS)
    again = client.post("/v1/batches", json=batch(), headers=AUTH)
    assert again.json()["duplicate"] is True, "the two spellings differed"


def test_an_uncompressed_batch_still_works(client):
    assert client.post("/v1/batches", json=batch(), headers=AUTH).status_code == 202


def test_a_body_that_is_not_gzip_is_refused_as_such(client):
    """Not as malformed JSON. A collector shipping plain bytes under a gzip
    header has a bug worth naming rather than one to guess at."""
    response = client.post(
        "/v1/batches", content=json.dumps(batch()).encode(), headers=HEADERS
    )
    assert response.status_code == 400
    assert "gzip" in response.json()["detail"]


def test_authorisation_still_applies_to_a_compressed_body(client):
    """The middleware runs before authentication, so this is the check that
    decompressing early did not create a way past it."""
    response = client.post(
        "/v1/batches", content=gzipped(batch()),
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert response.status_code == 401


def test_unknown_fields_are_still_refused_when_compressed(client):
    """SEC-18 at the HTTP boundary must not be reachable around."""
    payload = batch() | {"prompt": "you are a helpful assistant"}
    response = client.post("/v1/batches", content=gzipped(payload), headers=HEADERS)
    assert response.status_code == 422


# --- the bomb -----------------------------------------------------------------

def test_a_decompression_bomb_is_refused_rather_than_buffered(client):
    """A few hundred bytes on the wire that expand to whatever the sender
    likes. The natural implementation — decompress, then check — has already
    lost by the time it checks."""
    bomb = gzip.compress(b"\0" * (MAX_DECOMPRESSED + 1024))
    assert len(bomb) < 1_000_000, "the point is that this is small on the wire"

    response = client.post("/v1/batches", content=bomb, headers=HEADERS)
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_the_cap_is_above_what_a_real_window_costs():
    """A legitimate collector must never meet it. One window at the collector's
    record limit is 225MB of JSON, measured."""
    assert MAX_DECOMPRESSED > 225 * 1024 * 1024


def test_the_cap_is_not_far_above_it_either():
    """Validating that window already costs 2.7GB of resident memory. A cap
    with room for four of them is not a cap."""
    assert MAX_DECOMPRESSED < 2 * 225 * 1024 * 1024


def test_an_oversized_refusal_names_the_remedy(client):
    """Otherwise the operator's next move is to retry the same window."""
    bomb = gzip.compress(b"\0" * (MAX_DECOMPRESSED + 1024))
    detail = client.post("/v1/batches", content=bomb, headers=HEADERS).json()["detail"]
    assert "CUSTOS_WINDOW" in detail


def test_an_uncompressed_body_is_capped_by_its_declared_length(client):
    """Reading it to find out how big it is would be doing the expensive thing
    first, which is the whole failure being avoided."""
    response = client.post(
        "/v1/batches",
        content=b"{}",
        headers=AUTH | {
            "Content-Type": "application/json",
            "Content-Length": str(MAX_DECOMPRESSED + 1),
        },
    )
    assert response.status_code == 413


def test_a_batch_that_is_merely_large_is_not_refused(client):
    """The cap must not be a limit on ordinary use. 20,000 flow records is a
    quiet hour, and it must go through."""
    payload = batch()
    payload["flows"] = [
        {
            "account_id": ACCOUNT, "interface_id": f"eni-{i % 500:06x}",
            "srcaddr": "10.0.1.5", "dstaddr": "160.79.104.10",
            "srcport": 41000 + i % 20000, "dstport": 443, "protocol": 6,
            "packets": 40, "bytes": 140000,
            "start": W0.isoformat(),
            "end": (W0 + timedelta(seconds=59)).isoformat(),
            "action": "ACCEPT", "log_status": "OK", "vpc_id": "vpc-0a1b2c3d",
            "subnet_id": "subnet-0ab12345", "direction": "egress",
            "src_aws_service": "", "dst_aws_service": "", "tcp_flags": 19,
        }
        for i in range(20_000)
    ]
    body = gzipped(payload)
    assert len(body) < 2_000_000

    assert client.post("/v1/batches", content=body, headers=HEADERS).status_code == 202


def test_a_truncated_gzip_stream_is_refused_not_half_read(client):
    whole = gzipped(batch())
    response = client.post("/v1/batches", content=whole[: len(whole) // 2], headers=HEADERS)
    assert response.status_code in (400, 422)


def test_zlib_without_a_gzip_header_is_refused(client):
    """Deflate is not gzip, and accepting it would mean the header no longer
    says what the body is."""
    raw = zlib.compress(json.dumps(batch()).encode())
    assert client.post("/v1/batches", content=raw, headers=HEADERS).status_code == 400
