"""The classifier.

Rule-based and hand-tuned, deliberately. Every finding will be challenged by the
engineer who owns the workload, and "the model said so" loses that argument
while "you sent 34x more bytes to Anthropic than you received back, across 14
consecutive minutes, with no request arriving at your load balancer" wins it.

Debuggability beats marginal accuracy until three design partners have produced
real traffic. That decision is recorded in the specification, and it holds.
"""

from .engine import (
    AGENT_THRESHOLD,
    REVIEW_THRESHOLD,
    Disposition,
    Verdict,
    classify_all,
    classify_principal,
    score,
)
from .episodes import Episode, PrincipalTelemetry, Window, sessionize
from .features import Features, extract

__all__ = [
    "AGENT_THRESHOLD",
    "REVIEW_THRESHOLD",
    "Disposition",
    "Episode",
    "Features",
    "PrincipalTelemetry",
    "Verdict",
    "Window",
    "classify_all",
    "classify_principal",
    "extract",
    "score",
    "sessionize",
]
