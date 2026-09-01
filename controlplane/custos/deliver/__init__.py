"""Getting findings to a human, without becoming noise."""

from .channel import Channel, Delivery, SlackChannel, WebhookChannel
from .config import from_env
from .finding import Finding, Severity, from_change, from_drift, from_first_scan
from .notify import NotifyResult, build_findings, notify
from .suppress import REPEAT_AFTER, Suppressor

__all__ = [
    "REPEAT_AFTER",
    "Channel",
    "Delivery",
    "Finding",
    "NotifyResult",
    "Severity",
    "SlackChannel",
    "Suppressor",
    "WebhookChannel",
    "build_findings",
    "from_change",
    "from_drift",
    "from_env",
    "from_first_scan",
    "notify",
]
