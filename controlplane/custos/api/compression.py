"""Accepting a compressed batch.

One collection window at the collector's own record limit is 500,000 flow
records, which is 203MB of JSON. Flow log JSON is the most compressible payload
imaginable — the same keys, the same addresses and the same subnets repeated
hundreds of thousands of times — and measures about 32x, so the collector sends
6.2MB instead. Nothing here decides that; the collector does. This is the other
half.

The size cap is the part worth reading. A decompression bomb is a few hundred
bytes on the wire that expands to whatever the sender likes, and the natural
implementation — decompress, then check — has already lost by the time it
checks. So this decompresses in chunks and stops at the limit, which means the
worst an anonymous caller can cost is one buffer of that size rather than the
process.
"""

from __future__ import annotations

import zlib

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_DECOMPRESSED = 512 * 1024 * 1024
"""Ceiling on one decompressed batch.

Above the 203MB a full window costs, so a legitimate collector never meets it,
and far below what would take the process down. A batch larger than this is
either a bomb or a collector configured with a window nobody should be using.
"""

# zlib's window size, with the 16 that says "expect a gzip header".
_GZIP_WINDOW = 16 + zlib.MAX_WBITS


class TooLarge(Exception):
    """The decompressed body exceeded the cap."""


class GzipRequestMiddleware:
    """Decompress request bodies sent with `Content-Encoding: gzip`.

    Starlette decompresses responses and not requests, so without this the
    collector's compressed batch reaches Pydantic as bytes that are not JSON
    and is refused as malformed — which looks exactly like a collector bug.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if headers.get("content-encoding", "").lower() != "gzip":
            await self.app(scope, receive, send)
            return

        try:
            body = await _read_decompressed(receive)
        except TooLarge:
            await _refuse(send, 413, b"batch too large decompressed")
            return
        except zlib.error:
            await _refuse(send, 400, b"body is not valid gzip")
            return

        # The headers described the compressed body. Leaving them would make
        # Content-Length disagree with what the application reads, which some
        # servers treat as a truncated request.
        rewritten = MutableHeaders(scope=scope)
        del rewritten["content-encoding"]
        rewritten["content-length"] = str(len(body))

        await self.app(scope, _replay(body), send)


async def _read_decompressed(receive: Receive) -> bytes:
    decompressor = zlib.decompressobj(_GZIP_WINDOW)
    chunks: list[bytes] = []
    total = 0

    more = True
    while more:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = decompressor.decompress(message.get("body", b""), MAX_DECOMPRESSED - total)
        if decompressor.unconsumed_tail:
            # The limit stopped it mid-chunk, which means the sender has more
            # to give than the cap allows. Stopping here rather than looping is
            # the whole point: nothing has been buffered beyond the cap.
            raise TooLarge
        total += len(chunk)
        chunks.append(chunk)
        more = message.get("more_body", False)

    return b"".join(chunks)


def _replay(body: bytes) -> Receive:
    """A receive channel that yields the decompressed body once."""
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


async def _refuse(send: Send, status: int, detail: bytes) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(detail) + 14).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": b'{"detail":"' + detail + b'"}'})
