"""Delivery channels.

Two, and the split is not about protocol. Slack is for a person deciding
whether to act today; a webhook is for a SIEM correlating this against
everything else. They want different amounts of the same finding, and sending
one shape to both produces something that is wrong for each.

Both are best-effort by design. A delivery failure never fails a scan: the
findings are in the register and the report either way, and a scan that aborted
because a webhook was down would lose the data as well as the notification.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..logging import event, get
from .finding import Finding, Severity

log = get("custos.deliver")

TIMEOUT_SECONDS = 10
"""Short. A delivery channel is not on the critical path and must not become
one — a hung webhook should not hold a scan transaction open."""


@dataclass(frozen=True, slots=True)
class Delivery:
    channel: str
    sent: int
    suppressed: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class Channel(Protocol):
    name: str

    def send(self, findings: list[Finding], at: datetime) -> Delivery: ...


Poster = Callable[[str, dict, dict[str, str] | None], None]
"""How a channel puts bytes on the network.

An injected field rather than a module-level call, so a test can assert what
would have been sent without a live endpoint. Delivery formatting is the part
most likely to be wrong and the part least likely to be exercised in
production, so it needs to be cheap to test.
"""


def _post(url: str, payload: dict, headers: dict[str, str] | None = None) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        if response.status >= 300:
            raise urllib.error.HTTPError(
                url, response.status, "delivery rejected", response.headers, None
            )


_EMOJI = {
    Severity.ACT_NOW: ":rotating_light:",
    Severity.REVIEW: ":mag:",
    Severity.NOTE: ":memo:",
}


@dataclass(slots=True)
class SlackChannel:
    """Slack incoming webhook.

    Formatting rules, each from a way a security bot becomes noise:

    One message per scan, not one per finding. A burst of eleven messages
    scrolls the channel and gets the app muted.

    ACT_NOW findings are listed in full; everything else is counted. A person
    scanning a channel needs to know whether to stop what they are doing, and
    that decision is not helped by six lines about batch jobs.

    Every line names an owner. A finding a reader cannot route is a finding
    they scroll past.
    """

    webhook_url: str
    name: str = "slack"
    post: Poster = _post

    def send(self, findings: list[Finding], at: datetime) -> Delivery:
        if not findings:
            return Delivery(channel=self.name, sent=0)

        try:
            self.post(self.webhook_url, {"text": self.render(findings, at)})
        except Exception as exc:  # noqa: BLE001 - any failure is the same failure here
            event(log, "delivery.failed", channel=self.name, error_type=type(exc).__name__)
            return Delivery(channel=self.name, sent=0, error=str(exc))

        event(log, "delivery.sent", channel=self.name, findings=len(findings))
        return Delivery(channel=self.name, sent=len(findings))

    def render(self, findings: list[Finding], at: datetime) -> str:
        urgent = [f for f in findings if f.severity is Severity.ACT_NOW]
        rest = [f for f in findings if f.severity is not Severity.ACT_NOW]

        account = findings[0].account_id
        lines = [f"*Custos* — account `{account}` — {at:%d %b %Y %H:%M UTC}"]

        for finding in urgent:
            lines.append(f"\n{_EMOJI[finding.severity]} *{finding.title}*")
            lines.append(f"> {finding.detail}")
            lines.append(f"> owner: *{finding.owner}*")
            for line in finding.evidence[:5]:
                lines.append(f"> • {line}")

        if rest:
            counts: dict[Severity, int] = {}
            for finding in rest:
                counts[finding.severity] = counts.get(finding.severity, 0) + 1
            summary = ", ".join(
                f"{count} {severity.value.replace('_', ' ')}"
                for severity, count in sorted(counts.items(), key=lambda kv: kv[0].rank)
            )
            lines.append(f"\n{_EMOJI[Severity.REVIEW]} {summary} — see the report")

        return "\n".join(lines)


@dataclass(slots=True)
class WebhookChannel:
    """Generic JSON webhook, for a SIEM.

    Sends every finding with every field, because the consumer is a correlation
    engine rather than a person and truncating for readability would drop the
    fields it joins on.

    One request per scan carrying an array, not one per finding. A SIEM
    ingesting eleven separate posts bills for eleven events and correlates
    none of them.
    """

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    name: str = "webhook"
    post: Poster = _post

    def send(self, findings: list[Finding], at: datetime) -> Delivery:
        if not findings:
            return Delivery(channel=self.name, sent=0)

        payload = {
            "source": "custos",
            "generated_at": at.isoformat(),
            "account_id": findings[0].account_id,
            "findings": [
                {
                    "fingerprint": f.fingerprint,
                    "severity": str(f.severity),
                    "title": f.title,
                    "detail": f.detail,
                    "principal": f.principal,
                    "owner_team": f.owner_team,
                    "blast_radius": str(f.blast_radius),
                    "observed_at": f.observed_at.isoformat() if f.observed_at else None,
                    "evidence": list(f.evidence),
                }
                for f in findings
            ],
        }

        try:
            self.post(self.url, payload, self.headers)
        except Exception as exc:  # noqa: BLE001
            event(log, "delivery.failed", channel=self.name, error_type=type(exc).__name__)
            return Delivery(channel=self.name, sent=0, error=str(exc))

        event(log, "delivery.sent", channel=self.name, findings=len(findings))
        return Delivery(channel=self.name, sent=len(findings))
