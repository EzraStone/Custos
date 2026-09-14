"""The two constants the dollar figures rest on, held against the corpus.

`controlplane/custos/spend.py` carries a number for wire bytes per streamed
output token and a number for telling a streamed response from a whole one.
Neither is derivable from anything in the control plane — they came from this
corpus — and a constant copied out of a measurement is a constant that drifts
from it silently.

The same pattern as `test_limits_agree.py` and `test_model_services_agree.py`:
two places that have to agree, and nothing making them.
"""

from __future__ import annotations

import pytest
from custos.spend import (
    FRAMING_OVERHEAD,
    STREAMED_BYTES_PER_TOKEN,
    STREAMED_PACKET_BYTES,
    responses_streamed,
)

from custos_a0 import corpus as corpus_mod
from custos_a0.conversion import measure


@pytest.fixture(scope="module")
def both():
    corpus = corpus_mod.build()
    return measure(corpus, streaming=True), measure(corpus, streaming=False)


def _streamed(rows):
    return [m for m in rows if m.streams]


def test_the_discriminator_reads_every_workload_correctly(both):
    """Both captures, every workload, including the embedding one that does
    not stream in the streamed capture — which is what makes this a
    measurement of the protocol rather than of the corpus."""
    for rows in both:
        for m in rows:
            read = responses_streamed(
                m.ingress_bytes, m.ingress_packets, m.egress_packets
            )
            assert read is m.streams, (
                m.workload, m.mean_data_packet, m.bytes_per_output_token
            )


def test_the_discriminator_sits_in_empty_space(both):
    """A threshold between two adjacent points is a lucky landing rather than
    a finding — the same rule the classifier's thresholds follow."""
    streamed, whole = both
    highest_streamed = max(m.mean_data_packet for m in _streamed(streamed))
    lowest_whole = min(
        m.mean_data_packet for m in whole + [m for m in streamed if not m.streams]
    )

    assert highest_streamed < STREAMED_PACKET_BYTES < lowest_whole
    assert lowest_whole / highest_streamed > 4, (highest_streamed, lowest_whole)


def test_the_streamed_constant_matches_what_the_corpus_produces(both):
    """`estimate_tokens` takes a framing haircut off the wire bytes before
    dividing, so the constant in spend.py is not the measured figure — it is
    the measured figure times what survives that haircut. The two drifting
    apart is exactly the kind of thing nothing else would catch."""
    streamed, _ = both
    # kb-assistant mixes streamed completions with whole embedding responses
    # and fits neither constant. Excluded by its ground truth rather than by
    # name: a workload whose responses are all streamed is what this measures.
    pure = [m for m in _streamed(streamed) if m.bytes_per_output_token > 100]
    measured = sum(m.bytes_per_output_token for m in pure) / len(pure)
    effective = STREAMED_BYTES_PER_TOKEN / (1 - FRAMING_OVERHEAD)

    assert abs(effective - measured) / measured < 0.05, (effective, measured)


def test_dividing_a_streamed_response_by_four_is_wrong_by_more_than_forty(both):
    """The size of the error, as the number the commit messages quote."""
    streamed, _ = both
    pure = [m for m in _streamed(streamed) if m.bytes_per_output_token > 100]
    assert min(m.bytes_per_output_token for m in pure) / 4 > 40


def test_a_whole_response_is_still_about_four_bytes_a_token(both):
    """The original constant was not wrong, it was answering a different
    question. Worth pinning: a change that fixed streaming by breaking the
    ordinary case would look like progress in every number above."""
    _, whole = both
    for m in whole:
        assert 4 <= m.bytes_per_output_token <= 12, (m.workload, m.bytes_per_output_token)
