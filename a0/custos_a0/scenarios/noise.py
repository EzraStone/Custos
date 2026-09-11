"""Ordinary infrastructure that looks like a model gateway.

The gateway detector asks a customer a question: "this internal address
receives far more than it returns from workloads that never reach a model
provider — is it your gateway?" Every workload here produces exactly that
shape and the answer is no.

They exist because "the detector asks nothing on the base corpus" was being
read as a precision result when it was really a statement about the corpus:
there was no internal destination in it that ordinary infrastructure floods,
so there was no question to get wrong.

Three groups, in increasing order of difficulty:

    bulk one-way        log shipping, backups, metrics, artifact publishing,
                        event production. Enormous egress, an acknowledgement
                        coming back. Separable on the size of the return leg if
                        anyone thinks to look at it.

    transform services  a thumbnailer and a document extractor. Something large
                        goes in, something substantial comes back. Not
                        separable from a gateway by volume or ratio, because
                        there is no volume or ratio that distinguishes them.

None of these is an agent, none of them makes a model call, and all of them are
blind by the detector's definition — which is the property that makes them
candidates rather than an assumption the detector could use to exclude them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from ..arrivals import jitter, poisson_arrivals, uniform_arrivals
from ..endpoints import (
    ARTIFACT_REGISTRY,
    BACKUP_SVC,
    DOC_EXTRACT,
    EVENT_PROXY,
    LOG_COLLECTOR,
    METRICS_PUSH,
    THUMBNAILER,
    Endpoint,
)
from ..trace import Call, CallKind, Label, Workload


def _stream(
    w: Workload,
    rng: Random,
    times: list[datetime],
    endpoint: Endpoint,
    req: int,
    resp: int,
    spread: float = 0.35,
) -> None:
    """One call per time, sized around `req` and `resp`.

    No episode structure and no accumulation: these workloads send the same
    kind of thing over and over, which is the one thing that is actually
    different about them and the thing byte totals throw away.
    """
    for i, at in enumerate(times):
        w.calls.append(
            Call(
                at=at,
                kind=CallKind.TOOL,
                endpoint=endpoint,
                req_bytes=int(req * (1 - spread + 2 * spread * rng.random())),
                resp_bytes=int(resp * (0.7 + 0.6 * rng.random())),
                step=i,
            )
        )


def log_shipper(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="fluent-bit-forwarder",
        principal="arn:aws:iam::447120043318:role/log-forwarder",
        scenario="log_shipper",
        label=Label.NOT_AGENT,
        compute="EKS",
        note=(
            "The most common workload in any account and the loudest thing on "
            "this list. Flushes a buffer to the log collector every minute and "
            "gets an acknowledgement. Half a gigabyte out, three megabytes "
            "back, and no model traffic anywhere — a gateway question about it "
            "outranks the real gateway on volume alone."
        ),
    )
    _stream(w, rng, uniform_arrivals(rng, start, end, 60.0), LOG_COLLECTOR,
            req=120_000, resp=200)
    return w


def backup_agent(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="volume-backup-agent",
        principal="arn:aws:iam::447120043318:role/backup-agent",
        scenario="backup_agent",
        label=Label.NOT_AGENT,
        compute="EC2",
        note=(
            "Runs nightly, ships chunks to the backup service, gets a receipt "
            "per chunk. The largest egress in the corpus by an order of "
            "magnitude, and on a schedule that looks exactly like the batch "
            "agent's."
        ),
    )
    t = start.replace(hour=2, minute=0, second=0, microsecond=0)
    while t < end:
        if t >= start:
            chunk = t
            for i in range(180):
                chunk += jitter(rng, timedelta(seconds=9), 0.5)
                if chunk >= end:
                    break
                w.calls.append(Call(
                    at=chunk, kind=CallKind.TOOL, endpoint=BACKUP_SVC,
                    req_bytes=int(8_000_000 * (0.6 + 0.8 * rng.random())),
                    resp_bytes=int(340 * (0.7 + 0.6 * rng.random())), step=i,
                ))
        t += timedelta(days=1)
    return w


def metrics_agent(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="prometheus-pusher",
        principal="arn:aws:iam::447120043318:role/metrics-push",
        scenario="metrics_agent",
        label=Label.NOT_AGENT,
        compute="EKS",
        note=(
            "Pushes a scrape to the gateway every minute, every minute, "
            "forever. Small individually and the ratio is extreme: what comes "
            "back is a 200."
        ),
    )
    _stream(w, rng, uniform_arrivals(rng, start, end, 60.0), METRICS_PUSH,
            req=12_000, resp=150, spread=0.2)
    return w


def ci_artifact_publisher(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="build-artifact-publisher",
        principal="arn:aws:iam::447120043318:role/ci-publisher",
        scenario="ci_artifact_publisher",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "Pushes build outputs to the internal registry after each green "
            "build. Bursty, machine-triggered, no inbound requests — the same "
            "three properties the decoupling signal reads as agent-shaped, "
            "with no model call anywhere near it."
        ),
    )
    _stream(w, rng, uniform_arrivals(rng, start, end, 3.0), ARTIFACT_REGISTRY,
            req=4_000_000, resp=900, spread=0.5)
    return w


def event_producer(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="clickstream-producer",
        principal="arn:aws:iam::447120043318:role/clickstream",
        scenario="event_producer",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "Batches user events to the Kafka REST proxy. Diurnal, because the "
            "events come from people — the one thing on this list whose volume "
            "follows the working day."
        ),
    )
    _stream(w, rng, poisson_arrivals(rng, start, end, 90.0), EVENT_PROXY,
            req=48_000, resp=400)
    return w


def image_pipeline(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="listing-image-pipeline",
        principal="arn:aws:iam::447120043318:role/image-pipeline",
        scenario="image_pipeline",
        label=Label.NOT_AGENT,
        compute="ECS",
        note=(
            "THE HARD NEGATIVE. Sends an image to the thumbnailer and gets a "
            "smaller image back. Roughly six to one, megabytes in both "
            "directions, no model traffic, no inbound requests. There is no "
            "byte ratio that separates this from a model gateway, because on "
            "the wire there is nothing to separate."
        ),
    )
    _stream(w, rng, uniform_arrivals(rng, start, end, 12.0), THUMBNAILER,
            req=1_800_000, resp=280_000, spread=0.45)
    return w


def document_ingest(rng: Random, start: datetime, end: datetime) -> Workload:
    w = Workload(
        name="contract-ingest",
        principal="arn:aws:iam::447120043318:role/contract-ingest",
        scenario="document_ingest",
        label=Label.NOT_AGENT,
        compute="Lambda",
        note=(
            "THE OTHER HARD NEGATIVE, and the one most likely to be mistaken "
            "for the real thing: PDFs go to the extractor, text comes back. "
            "Nine to one, which is squarely inside the band an agent's "
            "transcript traffic occupies."
        ),
    )
    _stream(w, rng, uniform_arrivals(rng, start, end, 8.0), DOC_EXTRACT,
            req=1_200_000, resp=130_000, spread=0.55)
    return w
