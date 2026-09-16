"""The classifier must not be able to cheat.

Feature extraction sees telemetry. It must never see a principal name. Real IAM
roles are frequently named things like `role/support-triage-agent`, and a
classifier that reads that string would score well on any corpus while learning
nothing — and would be defeated in production by a customer who names roles
after teams instead of functions.

This is enforced by inspecting the source of the modules that compute features
and signals, because it is a property no unit test on outputs can establish.
"""

import ast
import inspect
from datetime import UTC, datetime

from custos.classify import episodes, features, signals
from custos.classify.episodes import _to_payload

T0 = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

FORBIDDEN_ATTRIBUTES = {"principal", "enis"}


def _attribute_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}


def test_feature_extraction_never_reads_the_principal():
    leaked = _attribute_names(features) & FORBIDDEN_ATTRIBUTES
    assert not leaked, f"feature extraction reads {leaked}"


def test_signals_never_read_the_principal():
    leaked = _attribute_names(signals) & FORBIDDEN_ATTRIBUTES
    assert not leaked, f"signal definitions read {leaked}"


def test_sessionize_may_read_the_principal():
    """Sanity check on the test itself: grouping by principal is legitimate,
    so the guard must be scoped to feature and signal computation only."""
    assert "principal" in _attribute_names(episodes)


def test_features_are_computable_from_telemetry_alone():
    """Every field of Features must be derivable without identity.

    Checked by constructing telemetry with a deliberately misleading principal
    name and asserting the features are identical to a neutral one.
    """
    import dataclasses

    from custos.classify.episodes import PrincipalTelemetry

    misleading = PrincipalTelemetry(principal="arn:aws:iam::1:role/definitely-an-agent")
    neutral = PrincipalTelemetry(principal="arn:aws:iam::1:role/svc-0001")
    assert dataclasses.asdict(features.extract(misleading)) == dataclasses.asdict(
        features.extract(neutral)
    )


# --- and must not read the protocol either ----------------------------------
#
# The same class of guarantee, established by the same means, for a defect that
# took a year to notice. Feature extraction reading a principal name would
# learn something that does not generalise between accounts; feature extraction
# reading wire bytes learns something that does not generalise between two
# accounts whose clients are configured differently.
#
# It is not hypothetical and it is not subtle in hindsight. On a streamed
# capture the egress ratio of a confirmed agent read 0.76:1 — the shape of a
# chatbot — because wire bytes carry a TLS handshake per connection, one
# acknowledgement per two outbound segments, and forty-two bytes of framing for
# every byte the model said.

WIRE_ATTRIBUTES = {
    "model_egress_wire",
    "model_ingress_wire",
    "model_egress_packets",
    "model_ingress_packets",
}


def test_feature_extraction_never_reads_wire_bytes():
    leaked = _attribute_names(features) & WIRE_ATTRIBUTES
    assert not leaked, (
        f"feature extraction reads {leaked}. Those counts include "
        "acknowledgements, TLS handshakes and streaming framing, none of which "
        "is anything the workload said — a feature computed from them measures "
        "how the customer configured their client."
    )


def test_signals_never_read_wire_bytes():
    leaked = _attribute_names(signals) & WIRE_ATTRIBUTES
    assert not leaked, f"signal definitions read {leaked}"


def test_sessionize_may_read_them():
    """The guard has to be scoped, or it forbids the correction itself.

    `episodes` is where wire bytes are turned into payload, so it reads every
    one of these by necessity. A test that banned them everywhere would ban the
    fix for the thing it exists to prevent.
    """
    assert _attribute_names(episodes) >= WIRE_ATTRIBUTES


def test_the_ratio_is_identical_for_two_clients_configured_differently():
    """The behavioural half, and the one that would have caught this.

    The same conversation, framed two ways: one response arriving whole, the
    same bytes arriving a token at a time. The features must not be able to
    tell.
    """
    import dataclasses

    from custos.classify.episodes import PeerTraffic, PrincipalTelemetry, Window
    from custos.framing import ACK_BYTES, SSE_INFLATION

    payload, acks = 400_000, 2_000
    sent = 4_000_000

    def telemetry(streamed: bool) -> PrincipalTelemetry:
        if streamed:
            ingress = int(payload * SSE_INFLATION + acks * ACK_BYTES)
            packets = int(payload * SSE_INFLATION / 190) + acks
        else:
            ingress = int(payload + acks * ACK_BYTES)
            packets = int(payload / 1_400) + acks
        window = Window(start=T0)
        window.model_peers["160.79.104.10"] = PeerTraffic(
            egress=sent, ingress=ingress,
            egress_packets=acks * 2, ingress_packets=packets,
        )
        return PrincipalTelemetry(principal="arn:aws:iam::1:role/x", windows=[window])

    whole, streamed = telemetry(False), telemetry(True)
    _to_payload(whole.windows)
    _to_payload(streamed.windows)

    a = dataclasses.asdict(features.extract(whole))
    b = dataclasses.asdict(features.extract(streamed))
    assert abs(a["egress_ratio"] - b["egress_ratio"]) / a["egress_ratio"] < 0.05, (
        a["egress_ratio"], b["egress_ratio"]
    )
