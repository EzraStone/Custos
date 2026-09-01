"""Scan-to-delivery orchestration.

The ordering tests are the point. Both failure modes are silent, and both
produce a finding that was never delivered and never will be.
"""

from datetime import UTC, datetime, timedelta

import pytest

from custos.deliver import Delivery, Severity, build_findings, notify
from custos.deliver.config import from_env
from custos.store.db import open_database

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
ACCOUNT = "447120043318"


class FakeChannel:
    def __init__(self, name="slack", fail=False):
        self.name = name
        self.fail = fail
        self.batches: list[list] = []

    def send(self, findings, at):
        if self.fail:
            return Delivery(channel=self.name, sent=0, error="endpoint down")
        self.batches.append(list(findings))
        return Delivery(channel=self.name, sent=len(findings))


@pytest.fixture(scope="module")
def scans():
    """Two ingested scans, the second with an escalation."""
    from custos_a0 import corpus
    from custos_a0.batchbridge import build_batch

    from custos.pipeline import ingest

    conn = open_database()
    first = build_batch(corpus.build(corpus.CorpusSpec(days=1)))
    first_outcome = ingest(conn, first)

    principals = [
        p.model_copy(update={"actions": sorted({*p.actions, "s3:DeleteBucket"})})
        if "finance-close" in p.principal else p
        for p in first.principals
    ]
    span = first.window_end - first.window_start
    second = first.model_copy(update={
        "window_start": first.window_end,
        "window_end": first.window_end + span,
        "principals": principals,
    })
    second_outcome = ingest(conn, second)
    return first_outcome, second_outcome


def test_a_first_scan_delivers_one_summary(scans):
    first, _ = scans
    findings = build_findings(first, ACCOUNT, T0)
    assert len(findings) == 1
    assert "unsanctioned agent" in findings[0].detail


# Repeating the inventory weekly is what turns a channel into wallpaper.
def test_a_later_scan_delivers_only_what_changed(scans):
    _, second = scans
    findings = build_findings(second, ACCOUNT, T0)
    assert findings
    assert all("unsanctioned agent" not in f.detail for f in findings)
    assert findings[0].severity is Severity.ACT_NOW
    assert "destructive" in findings[0].detail


def test_findings_are_ordered_urgent_first(scans):
    _, second = scans
    ranks = [f.severity.rank for f in build_findings(second, ACCOUNT, T0)]
    assert ranks == sorted(ranks)


def test_delivery_reaches_every_channel(scans):
    first, _ = scans
    conn = open_database()
    slack, siem = FakeChannel("slack"), FakeChannel("webhook")

    result = notify(conn, first, ACCOUNT, [slack, siem], T0)
    assert result.ok
    assert len(slack.batches) == 1 and len(siem.batches) == 1
    assert len(result.deliveries) == 2


def test_a_repeat_scan_delivers_nothing_new(scans):
    first, _ = scans
    conn = open_database()
    channel = FakeChannel()

    notify(conn, first, ACCOUNT, [channel], T0)
    again = notify(conn, first, ACCOUNT, [channel], T0 + timedelta(hours=1))

    assert len(channel.batches) == 1
    assert again.suppressed > 0


# Recording after the send is what keeps a webhook outage from consuming a
# finding's one delivery.
def test_a_failed_delivery_is_retried_on_the_next_scan(scans):
    first, _ = scans
    conn = open_database()
    broken = FakeChannel(fail=True)

    result = notify(conn, first, ACCOUNT, [broken], T0)
    assert not result.ok

    broken.fail = False
    retry = notify(conn, first, ACCOUNT, [broken], T0 + timedelta(hours=1))
    assert retry.ok
    assert len(broken.batches) == 1, "the finding must survive the outage"


def test_one_channel_failing_does_not_stop_another(scans):
    first, _ = scans
    conn = open_database()
    broken, working = FakeChannel("slack", fail=True), FakeChannel("webhook")

    result = notify(conn, first, ACCOUNT, [broken, working], T0)
    assert not result.ok
    assert len(working.batches) == 1


def test_no_channels_is_not_an_error(scans):
    first, _ = scans
    result = notify(open_database(), first, ACCOUNT, [], T0)
    assert result.ok
    assert result.findings


# --- configuration ------------------------------------------------------------

def test_channels_are_built_from_the_environment():
    channels = from_env({
        "CUSTOS_SLACK_WEBHOOK": "https://hooks.slack.com/services/x",
        "CUSTOS_SIEM_WEBHOOK": "https://siem.example/ingest",
        "CUSTOS_SIEM_HEADERS": "X-Api-Key: secret, X-Tenant: acme",
    }.get)
    assert {c.name for c in channels} == {"slack", "webhook"}
    siem = next(c for c in channels if c.name == "webhook")
    assert siem.headers == {"X-Api-Key": "secret", "X-Tenant": "acme"}


# A finding names a customer's principals and their blast radius. Sending that
# over http is worse than not delivering it.
def test_plaintext_endpoints_are_refused():
    assert from_env({"CUSTOS_SLACK_WEBHOOK": "http://hooks.example/x"}.get) == []
    assert from_env({"CUSTOS_SIEM_WEBHOOK": "http://siem.example/x"}.get) == []


def test_no_configuration_means_no_channels():
    assert from_env(lambda _: None) == []
