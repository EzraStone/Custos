"""The A0 signal experiment.

A0 answers exactly one question, which every other page of the specification is
contingent on:

    Does an agent separate from a chatbot backend using flow logs alone?

The specification proposes answering it by standing up an AWS test environment
and hand-analysing two weeks of capture. This package answers a strictly harder
version of the question first, in software, in a few hours:

    Does an agent separate from a chatbot backend using flow logs alone, AFTER
    those flow logs have been degraded by 60-second aggregation and TLS
    connection reuse the way real VPC Flow Logs degrade them?

The second question is the one that matters, because it is the one the product
actually faces. Answering it first means the AWS environment gets built to
confirm a result rather than to search for one.

Nothing in this package ships to customers. It generates labelled synthetic
telemetry and scores the production classifier in `custos.classify` against it,
so the experiment and the product cannot drift apart.
"""

__version__ = "0.1.0"
