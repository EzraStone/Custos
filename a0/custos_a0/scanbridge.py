"""Feed a synthetic corpus through the real scan pipeline.

A0's corpus is the only realistic end-to-end input available before a customer
exists, so the scanner is exercised against it rather than against fixtures
written to make the scanner pass.
"""

from __future__ import annotations

from datetime import timedelta

from custos.attribute import PrincipalFacts
from custos.reach import IamCapability
from custos.scan import ScanInput, ScanResult
from custos.scan import run as run_scan

from . import corpus as corpus_mod
from .trace import Corpus
from .wire import AggregationConfig, aggregate

# Synthetic IAM facts. Deliberately uneven: two principals carry clean resource
# tags, two carry only an IAM path, and the rest carry nothing — which is what
# tag hygiene actually looks like in a company that adopted agents bottom-up.
FACTS: dict[str, dict] = {
    "support-triage-task": {
        "resource_tags": {"team": "support-platform", "contact": "support-eng@example.com"}
    },
    "autofix-runner": {"resource_tags": {"team": "developer-experience"}},
    "finance-close": {"iam_path": "/finance/service-role/"},
    "ops-automation": {"iam_path": "/platform/service-role/"},
}

CAPABILITIES: dict[str, set[str]] = {
    "finance-close": {"s3:GetObject", "dynamodb:UpdateItem", "rds:ModifyDBInstance"},
    "ops-automation": {"ecs:UpdateService", "s3:*", "sts:AssumeRole"},
    "support-triage-task": {"s3:GetObject", "dynamodb:Query"},
    "autofix-runner": {"s3:PutObject", "lambda:UpdateFunctionCode"},
    "inventory-recon": {"dynamodb:UpdateItem"},
}

COMPUTE = {
    "support-triage-task": "ECS", "autofix-runner": "EC2", "inventory-recon": "Lambda",
    "finance-close": "Lambda", "ops-automation": "EKS",
}


def _short(principal: str) -> str:
    return principal.rsplit("/", 1)[-1]


def scan(
    corpus: Corpus | None = None,
    interval_seconds: int = 60,
    have_alb_logs: bool = True,
) -> ScanResult:
    """Run the production scanner over synthetic telemetry."""
    c = corpus if corpus is not None else corpus_mod.build()
    interval = timedelta(seconds=interval_seconds)
    capture = aggregate(
        c, AggregationConfig(interval=interval, have_alb_logs=have_alb_logs)
    )

    facts, capabilities, compute_by_eni = {}, {}, {}
    for w in c.workloads:
        short = _short(w.principal)
        facts[w.principal] = PrincipalFacts(
            principal=w.principal, account_id=capture.config.account_id,
            compute=w.compute, **FACTS.get(short, {}),
        )
        if short in CAPABILITIES:
            capabilities[w.principal] = IamCapability(
                principal=w.principal, actions=frozenset(CAPABILITIES[short])
            )
        compute_by_eni[w.eni] = w.compute

    return run_scan(
        ScanInput(
            account_id=capture.config.account_id,
            start=c.start, end=c.end,
            records=capture.records, requests=capture.requests,
            principal_by_eni=capture.principal_by_eni,
            address_by_eni=capture.address_by_eni,
            compute_by_eni=compute_by_eni,
            facts=facts, capabilities=capabilities,
            interval=interval, inbound_logs_available=have_alb_logs,
        )
    )
