"""Delivery, weighted toward what must not be sent."""

from datetime import UTC, datetime, timedelta

import pytest

from custos.baseline import Drift, DriftKind
from custos.deliver import (
    REPEAT_AFTER,
    Finding,
    Severity,
    SlackChannel,
    Suppressor,
    WebhookChannel,
    from_change,
    from_drift,
    from_first_scan,
)
from custos.diff import Change, ChangeKind
from custos.register.model import (
    Agent,
    BlastRadius,
    Identity,
    Provenance,
    Reach,
    Source,
    Status,
)
from custos.store.db import open_database

T0 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
ACCOUNT = "447120043318"


def finding(severity=Severity.ACT_NOW, title="Credential can now do more damage",
            principal="arn:aws:iam::1:role/finance-close", team="finance"):
    return Finding(
        severity=severity, title=title, detail="went from write to destructive",
        account_id=ACCOUNT, principal=principal, owner_team=team,
        blast_radius=BlastRadius.DESTRUCTIVE, observed_at=T0,
    )


def agent(principal="arn:aws:iam::1:role/x", team="finance", radius=BlastRadius.WRITE):
    return Agent(
        id="agt_1", first_seen=T0, last_seen=T0, status=Status.DISCOVERED,
        provenance=Provenance(source=Source.DISCOVERED, confidence=0.95,
                              observed_principal=principal),
        identity=Identity(principal=principal, owner_team=team, account_id=ACCOUNT),
        reach=Reach(blast_radius=radius),
    )


# --- severity mapping ---------------------------------------------------------

def test_an_escalation_is_act_now():
    change = Change(kind=ChangeKind.BLAST_RADIUS_INCREASED, agent_id="a",
                    principal="role/x", detail="write to destructive", owner_team="finance")
    assert from_change(change, ACCOUNT).severity is Severity.ACT_NOW


def test_a_disappearance_is_only_a_note():
    change = Change(kind=ChangeKind.DISAPPEARED, agent_id="a",
                    principal="role/x", detail="gone")
    assert from_change(change, ACCOUNT).severity is Severity.NOTE


# A departure from baseline is a question, not a conclusion. Paging someone for
# one claims a certainty the data cannot carry.
def test_drift_is_never_act_now():
    for kind in DriftKind:
        drift = Drift(kind=kind, agent_id="agt_1", observed_at=T0, detail="did a thing")
        assert from_drift(drift, agent(), ACCOUNT).severity is Severity.REVIEW


def test_the_first_scan_delivers_one_summary_not_one_alert_per_agent():
    """Forty messages on day one is how a channel gets muted before it has ever
    been useful."""
    agents = [agent(principal=f"role/a{i}") for i in range(40)]
    findings = from_first_scan(agents, ACCOUNT, T0)
    assert len(findings) == 1
    assert "40 unsanctioned agents" in findings[0].detail
    assert len(findings[0].evidence) == 5


def test_a_clean_first_scan_delivers_nothing():
    assert from_first_scan([], ACCOUNT, T0) == []


# --- suppression --------------------------------------------------------------

@pytest.fixture
def suppressor():
    return Suppressor(open_database())


def test_a_finding_is_delivered_once(suppressor):
    deliverable, suppressed = suppressor.filter([finding()], "slack", T0)
    assert len(deliverable) == 1 and suppressed == 0

    suppressor.record(deliverable, "slack", T0)
    deliverable, suppressed = suppressor.filter([finding()], "slack", T0 + timedelta(days=1))
    assert deliverable == [] and suppressed == 1


def test_an_escalation_returns_after_its_window(suppressor):
    """Long enough not to nag, short enough that something nobody acted on
    comes back before it is forgotten."""
    suppressor.record([finding()], "slack", T0)
    later = T0 + REPEAT_AFTER[Severity.ACT_NOW] + timedelta(minutes=1)
    deliverable, _ = suppressor.filter([finding()], "slack", later)
    assert len(deliverable) == 1


def test_a_note_waits_far_longer_than_an_escalation():
    assert REPEAT_AFTER[Severity.NOTE] > REPEAT_AFTER[Severity.REVIEW]
    assert REPEAT_AFTER[Severity.REVIEW] > REPEAT_AFTER[Severity.ACT_NOW]


# A finding sent to Slack has not been sent to a SIEM. Treating them as one
# silently drops half the integration a customer paid for.
def test_suppression_is_per_channel(suppressor):
    suppressor.record([finding()], "slack", T0)
    deliverable, _ = suppressor.filter([finding()], "webhook", T0)
    assert len(deliverable) == 1


def test_different_findings_do_not_suppress_each_other(suppressor):
    suppressor.record([finding()], "slack", T0)
    other = finding(principal="arn:aws:iam::1:role/other")
    deliverable, _ = suppressor.filter([other], "slack", T0)
    assert len(deliverable) == 1


# The same escalation on three consecutive scans is one finding. A fingerprint
# that varied between scans would defeat suppression entirely.
def test_fingerprints_ignore_timestamps_and_detail_text():
    a = finding()
    b = Finding(
        severity=a.severity, title=a.title, detail="completely different wording",
        account_id=a.account_id, principal=a.principal, owner_team=a.owner_team,
        observed_at=T0 + timedelta(days=9),
    )
    assert a.fingerprint == b.fingerprint


def test_fingerprints_separate_accounts():
    a = finding()
    b = Finding(severity=a.severity, title=a.title, detail=a.detail,
                account_id="999999999999", principal=a.principal)
    assert a.fingerprint != b.fingerprint


def test_history_can_be_forgotten(suppressor):
    suppressor.record([finding()], "slack", T0)
    assert suppressor.forget(ACCOUNT, before=T0 + timedelta(days=90)) == 1
    deliverable, _ = suppressor.filter([finding()], "slack", T0 + timedelta(days=91))
    assert len(deliverable) == 1


# --- channels -----------------------------------------------------------------

class Recorder:
    """Captures what a channel would send, instead of sending it."""

    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.fail = fail

    def __call__(self, url, payload, headers=None):
        if self.fail:
            raise self.fail
        self.calls.append((url, payload, headers))


def slack(recorder):
    channel = SlackChannel(webhook_url="https://hooks.example/x")
    channel.post = recorder
    return channel


def webhook(recorder):
    channel = WebhookChannel(url="https://siem.example/x", headers={"X-Key": "k"})
    channel.post = recorder
    return channel


# A burst of eleven messages scrolls a channel and gets the app muted.
def test_slack_sends_one_message_per_scan():
    recorder = Recorder()
    result = slack(recorder).send([finding() for _ in range(11)], T0)
    assert len(recorder.calls) == 1
    assert result.ok and result.sent == 11


def test_slack_lists_urgent_findings_and_counts_the_rest():
    """A reader deciding whether to stop what they are doing is not helped by
    six lines about batch jobs."""
    findings = [
        finding(severity=Severity.ACT_NOW),
        *(finding(severity=Severity.NOTE, principal=f"role/n{i}") for i in range(6)),
    ]
    recorder = Recorder()
    slack(recorder).send(findings, T0)

    text = recorder.calls[0][1]["text"]
    assert "Credential can now do more damage" in text
    assert "6 note" in text
    assert text.count("went from write to destructive") == 1


def test_every_slack_line_names_an_owner():
    """A finding a reader cannot route is one they scroll past."""
    recorder = Recorder()
    slack(recorder).send([finding()], T0)
    assert "owner: *finance*" in recorder.calls[0][1]["text"]


def test_an_unattributed_finding_says_so_rather_than_being_blank():
    recorder = Recorder()
    slack(recorder).send([finding(team="")], T0)
    assert "unattributed" in recorder.calls[0][1]["text"]


def test_the_webhook_sends_every_field_in_one_request():
    """The consumer is a correlation engine; truncating drops the fields it
    joins on, and separate posts bill for events it cannot correlate."""
    recorder = Recorder()
    webhook(recorder).send([finding(), finding(principal="role/other")], T0)

    assert len(recorder.calls) == 1
    url, payload, headers = recorder.calls[0]
    assert headers == {"X-Key": "k"}
    assert len(payload["findings"]) == 2
    for key in ("fingerprint", "severity", "principal", "owner_team", "blast_radius"):
        assert key in payload["findings"][0]


def test_nothing_is_sent_when_there_is_nothing_to_say():
    for channel in (slack(Recorder()), webhook(Recorder())):
        result = channel.send([], T0)
        assert result.ok and result.sent == 0


# A scan that aborted because a webhook was down would lose the data as well as
# the notification.
def test_a_channel_failure_is_reported_not_raised():
    for build in (slack, webhook):
        channel = build(Recorder(fail=OSError("connection refused")))
        result = channel.send([finding()], T0)
        assert not result.ok
        assert "connection refused" in result.error
        assert result.sent == 0
