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

from custos.classify import episodes, features, signals

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
