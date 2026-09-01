"""Channel configuration from the environment.

    CUSTOS_SLACK_WEBHOOK   https://hooks.slack.com/services/...
    CUSTOS_SIEM_WEBHOOK    https://siem.example.com/ingest
    CUSTOS_SIEM_HEADERS    X-Api-Key: secret, X-Tenant: acme

No channels configured means no delivery, and that is a supported state rather
than a warning. A first scan is run by hand and read directly; delivery is what
a customer turns on when they want to stop reading reports.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from .channel import Channel, SlackChannel, WebhookChannel


def _headers(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in raw.split(","):
        name, _, value = pair.partition(":")
        name, value = name.strip(), value.strip()
        if name and value:
            out[name] = value
    return out


def from_env(getenv: Callable[[str], str | None] = os.getenv) -> list[Channel]:
    """Build channels from configuration. Returns an empty list when unset."""
    channels: list[Channel] = []

    slack_url = (getenv("CUSTOS_SLACK_WEBHOOK") or "").strip()
    if slack_url.startswith("https://"):
        channels.append(SlackChannel(webhook_url=slack_url))

    siem_url = (getenv("CUSTOS_SIEM_WEBHOOK") or "").strip()
    if siem_url.startswith("https://"):
        channels.append(WebhookChannel(
            url=siem_url,
            headers=_headers(getenv("CUSTOS_SIEM_HEADERS") or ""),
        ))

    # Plaintext endpoints are dropped rather than used. A finding names a
    # customer's principals and their blast radius; sending that over http is
    # a worse outcome than not delivering it.
    return channels
