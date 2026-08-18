"""Workload generators for the A0 corpus.

The corpus is deliberately adversarial. Anyone can build one where agents
separate from chatbots — put a LangGraph service next to a one-call chatbot and
the answer is trivial and worthless. The negatives here are built to defeat the
naive signals:

    chatbot_multiturn   accumulates transcript across turns, so cumulative
                        egress grows super-linearly exactly like an agent's
    chatbot_rag         interleaves model and datastore calls exactly like an
                        agent's tool loop
    batch_summariser    no inbound requests, high model volume, no tools
    ci_codegen          bursts of sequential model calls, no inbound, no growth

A classifier that separates the first two is separating on something real. A
classifier that puts the last two in the review band rather than guessing is
behaving the way SEC-17 requires.
"""

from collections.abc import Callable
from datetime import datetime
from random import Random

from ..trace import Workload
from .agents import (
    agent_coding,
    agent_langgraph,
    agent_low_volume,
    agent_scheduled_ops,
    agent_tool_loop,
)
from .borderline import batch_summariser, ci_codegen
from .chatbots import chatbot_multiturn, chatbot_rag, chatbot_simple, embedding_service

Generator = Callable[[Random, datetime, datetime], Workload]

GENERATORS: list[Generator] = [
    agent_langgraph,
    agent_coding,
    agent_tool_loop,
    agent_low_volume,
    agent_scheduled_ops,
    chatbot_simple,
    chatbot_rag,
    chatbot_multiturn,
    embedding_service,
    batch_summariser,
    ci_codegen,
]

__all__ = [
    "GENERATORS",
    "Generator",
    "agent_coding",
    "agent_langgraph",
    "agent_low_volume",
    "agent_scheduled_ops",
    "agent_tool_loop",
    "batch_summariser",
    "chatbot_multiturn",
    "chatbot_rag",
    "chatbot_simple",
    "ci_codegen",
    "embedding_service",
]
