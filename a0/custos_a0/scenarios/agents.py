"""Positive cases: workloads that are genuinely agents."""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from ..arrivals import jitter, poisson_arrivals, uniform_arrivals
from ..endpoints import (
    ANTHROPIC,
    ARTIFACTS_S3,
    BEDROCK,
    BILLING_API,
    BILLING_DB,
    DEPLOY_API,
    MCP_FILES,
    MCP_GITHUB,
    OPENAI,
    ORDERS_DB,
    TICKET_API,
)
from ..episode import agent_episode
from ..trace import Label, Workload


def agent_langgraph(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="support-triage-agent",
        principal="arn:aws:iam::447120043318:role/support-triage-task",
        scenario="agent_langgraph",
        label=Label.AGENT,
        compute="ECS",
        note=(
            "Event-driven LangGraph service, fires on ticket creation. "
            "6-14 steps with a tool call on every step. The canonical positive."
        ),
    )
    # Triggered from a queue, not through the load balancer, so no inbound
    # request accompanies any of it — but the queue is fed by human activity,
    # which is why the arrivals are still diurnal.
    for at in poisson_arrivals(rng, start, end, 5.0):
        tools = [TICKET_API, ORDERS_DB, MCP_GITHUB]
        agent_episode(w, rng, at, 6 + rng.randrange(9), ANTHROPIC, tools)
    return w


def agent_coding(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="autofix-coding-agent",
        principal="arn:aws:iam::447120043318:role/autofix-runner",
        scenario="agent_coding",
        label=Label.AGENT,
        compute="EC2",
        note=(
            "Long-horizon coding agent, 25-55 steps, heavy filesystem and VCS "
            "tool traffic. Longest episodes in the corpus."
        ),
    )
    for at in poisson_arrivals(rng, start, end, 1.1):
        tools = [MCP_FILES, MCP_GITHUB, ARTIFACTS_S3]
        agent_episode(w, rng, at, 25 + rng.randrange(31), ANTHROPIC, tools)
    return w


def agent_tool_loop(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="inventory-reconciler",
        principal="arn:aws:iam::447120043318:role/inventory-recon",
        scenario="agent_toolloop",
        label=Label.AGENT,
        compute="Lambda",
        note=(
            "Tight tool-calling loop, 4-8 steps, high frequency. Short episodes "
            "stress every signal that depends on an episode being long."
        ),
    )
    for at in uniform_arrivals(rng, start, end, 14.0):
        agent_episode(w, rng, at, 4 + rng.randrange(5), BEDROCK, [ORDERS_DB, BILLING_API])
    return w


def agent_low_volume(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="finance-close-agent",
        principal="arn:aws:iam::447120043318:role/finance-close",
        scenario="agent_lowvol",
        label=Label.AGENT,
        compute="Lambda",
        note=(
            "Runs three times a day with write reach to billing. THE RECALL "
            "CASE: the dangerous unsanctioned agent is usually low volume, not "
            "high. Missing this one is the miss that matters commercially."
        ),
    )
    day = start
    while day < end:
        for hour in (6, 13, 21):
            at = day + timedelta(hours=hour) + jitter(rng, timedelta(minutes=11), 0.9)
            if at < end:
                tools = [BILLING_API, BILLING_DB]
                agent_episode(w, rng, at, 5 + rng.randrange(4), ANTHROPIC, tools)
        day += timedelta(days=1)
    return w


def agent_scheduled_ops(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="nightly-ops-agent",
        principal="arn:aws:iam::447120043318:role/ops-automation",
        scenario="agent_scheduled_ops",
        label=Label.AGENT,
        compute="EKS",
        note=(
            "Cron-triggered ops agent holding deploy-control reach. Runs "
            "entirely off-hours, several episodes back to back."
        ),
    )
    day = start
    while day < end:
        at = day + timedelta(hours=2) + jitter(rng, timedelta(minutes=6), 0.8)
        for _ in range(3 + rng.randrange(3)):
            if at >= end:
                break
            tools = [DEPLOY_API, MCP_GITHUB, ARTIFACTS_S3]
            at = agent_episode(w, rng, at, 8 + rng.randrange(8), OPENAI, tools)
            at += jitter(rng, timedelta(seconds=90), 0.5)
        day += timedelta(days=1)
    return w
