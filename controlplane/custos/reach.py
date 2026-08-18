"""Reach and blast radius.

`reach` is the field that converts interest into a purchase order. "You have 34
agents" is a curiosity. "Eleven are unsanctioned, and one holds a role with
write access to your billing tables" is a budget line.

An important honesty constraint runs through this module. Flow logs show that a
principal *reached* a destination. They cannot show whether it *wrote*. Write
capability comes from the principal's IAM policy, which a read-only collector
can enumerate (SEC-16), and the two are reported as different things:

    observed reach     what the agent actually talked to
    granted capability what its credential would permit

The gap between them is itself a finding, and often the most alarming one — an
agent that only ever read from a bucket, holding a role that could delete it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import DestinationClass, classify
from .classify.episodes import PrincipalTelemetry
from .register.model import BlastRadius, Reach

DESTRUCTIVE_ACTIONS = frozenset({
    "s3:DeleteObject", "s3:DeleteBucket", "dynamodb:DeleteTable",
    "rds:DeleteDBInstance", "rds:DeleteDBCluster", "ec2:TerminateInstances",
    "iam:DeleteRole", "iam:DeleteUser", "kms:ScheduleKeyDeletion",
    "lambda:DeleteFunction", "ecs:DeleteCluster", "secretsmanager:DeleteSecret",
})

WRITE_PREFIXES = (
    "s3:Put", "s3:Write", "dynamodb:Put", "dynamodb:Update", "dynamodb:BatchWrite",
    "rds:Modify", "rds:Create", "iam:Put", "iam:Attach", "iam:Create",
    "lambda:Update", "lambda:Invoke", "ecs:Update", "ecs:Run",
    "secretsmanager:Put", "secretsmanager:Update", "kms:Encrypt", "sts:AssumeRole",
)

_WILDCARD_WRITE = frozenset({"*", "s3:*", "dynamodb:*", "rds:*", "iam:*", "ec2:*"})


@dataclass(frozen=True, slots=True)
class IamCapability:
    """What a principal's attached policies permit.

    Enumerated by the collector through IAM read-only calls. Never inferred
    from traffic, because traffic shows what happened and this describes what
    could.
    """

    principal: str
    actions: frozenset[str] = frozenset()
    assumable_roles: frozenset[str] = frozenset()
    readable_secrets: frozenset[str] = frozenset()


def granted_blast_radius(capability: IamCapability) -> BlastRadius:
    """Worst case permitted by the credential."""
    actions = capability.actions
    if actions & DESTRUCTIVE_ACTIONS or actions & _WILDCARD_WRITE:
        return BlastRadius.DESTRUCTIVE
    if any(a.startswith(WRITE_PREFIXES) for a in actions):
        return BlastRadius.WRITE
    return BlastRadius.READ


@dataclass(slots=True)
class ReachReport:
    reach: Reach
    observed_only: set[str] = field(default_factory=set)
    """Destinations reached. Evidence of what the agent does."""
    overreach: bool = False
    """True when the credential permits materially more than was observed.

    Reported prominently. An agent that only ever read, holding a role that
    could delete, is the finding a platform lead acts on fastest — it is a
    concrete over-permission with a named owner and an obvious remediation."""


def observed_reach(t: PrincipalTelemetry) -> tuple[set[str], set[str]]:
    """Split observed destinations into (tools, data stores)."""
    tools: set[str] = set()
    stores: set[str] = set()
    for window in t.windows:
        for address in window.tool_addresses:
            # Port is not retained per address on the window, so re-derive the
            # class from what the window recorded.
            if DestinationClass.DATASTORE in window.tool_classes and address not in tools:
                stores.add(address)
            else:
                tools.add(address)
    # An address seen as both is a tool; the more specific claim wins.
    return tools, stores - tools


def build(t: PrincipalTelemetry, capability: IamCapability | None = None) -> ReachReport:
    """Combine observed reach with granted capability."""
    tools, stores = observed_reach(t)
    model_endpoints = {a for w in t.windows for a in w.model_addresses}

    granted = granted_blast_radius(capability) if capability else BlastRadius.READ
    observed_write_targets = {
        a for a in tools | stores if classify(a, 0) is not DestinationClass.EXTERNAL
    }

    reach = Reach(
        credentials=set(capability.assumable_roles | capability.readable_secrets)
        if capability
        else set(),
        tools=tools,
        data_stores=stores,
        blast_radius=granted,
    )
    return ReachReport(
        reach=reach,
        observed_only=model_endpoints | observed_write_targets,
        overreach=bool(
            capability
            and granted is not BlastRadius.READ
            and not stores
        ),
    )
