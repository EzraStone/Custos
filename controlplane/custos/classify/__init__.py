"""The classifier.

Rule-based and hand-tuned, deliberately. Every finding will be challenged by the
engineer who owns the workload, and "the model said so" loses that argument
while "you sent 40x more bytes to Anthropic than you received back, in 14
consecutive minutes, with no request arriving at your load balancer" wins it.

Debuggability beats marginal accuracy until three design partners have produced
real traffic. That is a decision recorded in the specification, and it holds.
"""

from .episodes import Episode, PrincipalTelemetry, Window, sessionize

__all__ = ["Episode", "PrincipalTelemetry", "Window", "sessionize"]
