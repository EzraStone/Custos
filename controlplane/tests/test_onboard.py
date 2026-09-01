"""Onboarding material.

The failure this guards against is not a crash. It is sending a customer a
tfvars file with the wrong external ID, or a token that does not match the one
in the control plane's configuration — both of which surface as an
authorisation error an hour later, to someone who was doing us a favour.
"""

import json

import pytest

from custos.cli import main
from custos.onboard import InvalidAccount, generate

from .conftest import prose

ACCOUNT = "447120043318"
ENDPOINT = "https://api.custos.dev"


def test_generates_credentials_of_a_usable_length():
    o = generate(ACCOUNT, ENDPOINT)
    assert len(o.token) >= 30
    assert len(o.external_id) >= 16, "AWS requires at least 2; guessable is the concern"


def test_every_run_produces_different_credentials():
    """Re-running must not reissue the same token, or a rotation would be a
    no-op that looks like a rotation."""
    assert generate(ACCOUNT, ENDPOINT).token != generate(ACCOUNT, ENDPOINT).token


# The tfvars, the collector environment, and the control plane configuration
# must all carry the same values. A mismatch surfaces as an authorisation error
# an hour later, to someone who was doing us a favour.
def test_the_same_external_id_appears_in_the_tfvars_and_the_collector_env():
    o = generate(ACCOUNT, ENDPOINT)
    assert o.external_id in o.tfvars
    assert o.external_id in o.collector_env
    assert o.external_id in o.message


def test_the_token_matches_between_the_collector_and_the_control_plane():
    o = generate(ACCOUNT, ENDPOINT)
    assert o.token in o.collector_env
    assert o.tokens_env == f"{ACCOUNT}:{o.token}"


def test_the_customer_message_never_contains_the_token():
    """The token is ours to configure, not theirs to hold. Sending it in the
    same message as their Terraform would put it in their ticket system."""
    o = generate(ACCOUNT, ENDPOINT)
    assert o.token not in o.message


def test_the_message_leads_with_what_it_does_not_do():
    """The first question every time. Answering it before it is asked is what
    makes thirty minutes possible."""
    message = generate(ACCOUNT, ENDPOINT).message
    head = prose(message[: message.index("1.")])
    assert "read-only" in head
    assert "creates no compute" in head
    assert "terraform destroy" in head


def test_the_message_states_what_we_take_from_access_logs():
    message = prose(generate(ACCOUNT, ENDPOINT).message)
    assert "discarded at parse time" in message
    assert "60% to 100%" in message


# A typo produces a role nobody can assume and a confusing hour for someone
# else.
@pytest.mark.parametrize("bad", ["nope", "12345", "4471200433180", "", "  "])
def test_malformed_account_ids_are_refused(bad):
    with pytest.raises(InvalidAccount):
        generate(bad, ENDPOINT)


def test_plaintext_endpoints_are_refused():
    with pytest.raises(ValueError, match="https"):
        generate(ACCOUNT, "http://api.custos.dev")


def test_cli_prints_the_material_and_warns_it_is_not_stored(capsys):
    assert main(["onboard", "--account", ACCOUNT, "--endpoint", ENDPOINT]) == 0
    out = capsys.readouterr().out
    assert "cannot be recovered" in out
    assert "CUSTOS_TOKENS" in out
    assert "Send this to the customer" in out


def test_cli_json_output_is_scriptable(capsys):
    assert main(["onboard", "--account", ACCOUNT, "--endpoint", ENDPOINT, "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["account_id"] == ACCOUNT
    assert record["tokens_env"].startswith(f"{ACCOUNT}:")


def test_cli_rejects_a_bad_account_without_a_traceback(capsys):
    assert main(["onboard", "--account", "nope", "--endpoint", ENDPOINT]) == 2
    assert "not a 12-digit AWS account ID" in capsys.readouterr().err
