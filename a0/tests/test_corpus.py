"""The corpus must have the properties the experiment depends on.

These are not tests of the classifier. They are tests that the thing we are
about to classify is actually hard, so that a pass means something.
"""

from custos_a0 import corpus
from custos_a0.trace import CallKind, Label


def test_corpus_is_deterministic():
    a, b = corpus.build(), corpus.build()
    assert [(w.name, len(w.calls)) for w in a.workloads] == [
        (w.name, len(w.calls)) for w in b.workloads
    ]


def test_corpus_has_both_classes_and_no_duplicate_principals():
    c = corpus.build()
    agents, negatives = c.counts()
    assert agents == 5
    assert negatives == 6
    principals = [w.principal for w in c.workloads]
    assert len(set(principals)) == len(principals)


def test_every_workload_produced_traffic():
    for w in corpus.build().workloads:
        assert w.model_calls > 0, w.name


def test_all_calls_lie_inside_the_observation_window():
    c = corpus.build()
    for w in c.workloads:
        for call in w.calls:
            assert c.start <= call.at, (w.name, call.at)


def test_volume_alone_does_not_separate_the_classes():
    """The corpus must not be separable on call count.

    If the highest-volume workloads were all agents, a classifier could pass by
    counting calls, and that result would not survive contact with a customer.
    """
    c = corpus.build()
    ranked = sorted(c.workloads, key=lambda w: w.model_calls, reverse=True)
    assert ranked[0].label is Label.NOT_AGENT, ranked[0].name
    agents = [w for w in c.workloads if w.label is Label.AGENT]
    assert min(w.model_calls for w in agents) < max(
        w.model_calls for w in c.workloads if w.label is Label.NOT_AGENT
    )


def test_tool_interleave_alone_does_not_separate_the_classes():
    """At least one negative must interleave tool calls like an agent."""
    c = corpus.build()
    rag = c.by_name("kb-assistant")
    assert rag.label is Label.NOT_AGENT
    assert rag.tool_calls > 0


def test_context_accumulation_alone_does_not_separate_the_classes():
    """The multi-turn chatbot must genuinely grow its context like an agent."""
    w = corpus.build().by_name("sales-copilot-web")
    assert w.label is Label.NOT_AGENT
    by_session: dict[str, list[int]] = {}
    for call in w.calls:
        if call.kind is CallKind.MODEL:
            by_session.setdefault(call.request_id.rsplit("-t", 1)[0], []).append(call.req_bytes)
    long_sessions = [v for v in by_session.values() if len(v) >= 4]
    assert long_sessions, "no multi-turn sessions generated"
    grew = sum(1 for v in long_sessions if v[-1] > v[0] * 1.5)
    assert grew / len(long_sessions) > 0.9


def test_some_negatives_have_no_inbound_correlation():
    """The review-band cases must actually be decoupled from human requests."""
    c = corpus.build()
    for name in ("nightly-doc-summariser", "ci-test-generator"):
        w = c.by_name(name)
        assert w.label is Label.NOT_AGENT
        assert w.inbound_requests == 0, name
