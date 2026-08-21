"""The Python schema and the Go wire types must describe the same bytes.

They are in different languages and cannot share a definition, so drift between
them is a real risk — and a silent one. A field the API ignores looks exactly
like a field the collector never sent, and the failure surfaces as findings
quietly going missing rather than as an error.

This test parses the Go source and compares field for field. It is the reason
the duplication is acceptable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from custos.api import schema

GO_WIRE = Path(__file__).resolve().parents[2] / "collector" / "internal" / "wire" / "wire.go"

# Go struct name -> Python model
PAIRS = {
    "FlowRecord": schema.FlowRecord,
    "InboundRequest": schema.InboundRequest,
    "PrincipalFacts": schema.PrincipalFacts,
    "Attachment": schema.Attachment,
    "Batch": schema.Batch,
    "Collection": schema.Collection,
}

_STRUCT = re.compile(r"type (\w+) struct \{(.*?)\n\}", re.DOTALL)
_JSON_TAG = re.compile(r'json:"([^",]+)')


def go_structs() -> dict[str, set[str]]:
    """Extract JSON field names from the Go wire types."""
    source = GO_WIRE.read_text()
    out: dict[str, set[str]] = {}
    for name, body in _STRUCT.findall(source):
        fields = set(_JSON_TAG.findall(body))
        if fields:
            out[name] = fields
    return out


@pytest.fixture(scope="module")
def structs():
    if not GO_WIRE.exists():
        pytest.skip(f"collector source not present at {GO_WIRE}")
    parsed = go_structs()
    assert parsed, "no Go structs with JSON tags were parsed; the regex has rotted"
    return parsed


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_field_names_match_the_go_wire_types(structs, name):
    go_fields = structs.get(name)
    assert go_fields is not None, f"Go wire type {name} is missing"

    model = PAIRS[name]
    python_fields = set(model.model_fields)

    only_go = go_fields - python_fields
    only_python = python_fields - go_fields
    assert not only_go, f"{name}: collector sends fields the API does not accept: {only_go}"
    assert not only_python, f"{name}: API expects fields the collector never sends: {only_python}"


def test_every_go_wire_type_has_a_python_model(structs):
    """A new Go wire type with no Python counterpart ships data nothing reads."""
    missing = set(structs) - set(PAIRS)
    assert not missing, f"Go wire types with no API model: {missing}"


def test_sec18_no_payload_shaped_fields_on_either_side(structs):
    """The same guard as the Go side, applied to both at once."""
    suspicious = {
        "body", "payload", "prompt", "completion", "content", "message",
        "text", "request_body", "response_body", "query", "input", "output",
        "url", "user_agent", "client", "headers",
    }
    for name, fields in structs.items():
        leaked = fields & suspicious
        assert not leaked, f"SEC-18: Go {name} carries {leaked}"
    for name, model in PAIRS.items():
        leaked = set(model.model_fields) & suspicious
        assert not leaked, f"SEC-18: API {name} carries {leaked}"


def test_api_rejects_unknown_fields_rather_than_ignoring_them():
    """Ignoring an unknown field reads as compliance while being nothing of the
    kind: a modified collector could ship anything and the API would accept it."""
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="extra_forbidden"):
        schema.Batch(
            account_id="1",
            window_start="2026-08-10T12:00:00Z",
            window_end="2026-08-10T13:00:00Z",
            prompt="you are a helpful assistant",
        )
