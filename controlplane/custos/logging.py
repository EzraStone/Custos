"""Structured logging.

JSON lines, because these go to a customer's SIEM as often as to our own, and a
human-readable log that has to be regex-parsed on the way in is a log that gets
parsed wrong.

One rule governs what may be logged, and it is the same rule that governs what
may be shipped: **nothing that describes a person**. Principal ARNs, endpoints,
byte counts, and agent identifiers are fine — they describe software. Request
URLs, user agents, client addresses, and tag values are not, because tag values
are free text a customer might have put anything in.

`redact` enforces the tag case specifically, since that is the one place
customer-authored free text reaches this module.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Keys that may never appear in a log record, mirroring the SEC-18 guard on the
# wire types. A field named like this in a log is the same mistake as a field
# named like this on the wire, made somewhere less obvious.
FORBIDDEN_KEYS = frozenset({
    "body", "payload", "prompt", "completion", "content", "message_body",
    "text", "request_body", "response_body", "url", "user_agent", "client_ip",
    "headers", "query", "token", "authorization", "secret", "password",
})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "at": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    """Drop anything that could describe a person rather than software.

    Applied to every structured field before it reaches a log line. Values are
    not inspected — only keys — because inspecting values means deciding what a
    string is, and the only reliable way to keep prompt text out of a log is to
    never have a field that could hold it.
    """
    return {
        key: value
        for key, value in fields.items()
        if key.lower() not in FORBIDDEN_KEYS
    }


def configure(level: str = "INFO", stream=None) -> None:
    """Install the JSON formatter on the root logger."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)


def event(logger: logging.Logger, name: str, **fields: Any) -> None:
    """Log a structured event.

        event(log, "batch.ingested", account_id=..., agents_found=5)

    A helper rather than a convention, because `logger.info(msg, extra={...})`
    is easy to get subtly wrong and the wrong version silently drops the fields.
    """
    logger.info(name, extra={"fields": fields})
