import io
import json
import logging

from custos.logging import FORBIDDEN_KEYS, configure, event, get, redact


def capture() -> io.StringIO:
    stream = io.StringIO()
    configure(stream=stream)
    return stream


def test_events_are_json_lines():
    stream = capture()
    event(get("test"), "batch.ingested", account_id="447120043318", agents_found=5)

    record = json.loads(stream.getvalue().strip())
    assert record["event"] == "batch.ingested"
    assert record["account_id"] == "447120043318"
    assert record["agents_found"] == 5
    assert record["level"] == "info"
    assert record["at"].endswith("+00:00")


def test_software_describing_fields_survive():
    """Principals, endpoints, and byte counts describe software, not people."""
    kept = redact({
        "principal": "arn:aws:iam::1:role/finance-close",
        "dstaddr": "160.79.104.10",
        "bytes": 286432,
        "agent_id": "agt_1",
    })
    assert len(kept) == 4


def test_person_describing_fields_are_dropped():
    for key in ("url", "user_agent", "client_ip", "prompt", "token", "authorization"):
        assert redact({key: "sensitive"}) == {}, key


def test_redaction_is_case_insensitive():
    assert redact({"Authorization": "Bearer x", "USER_AGENT": "Mozilla"}) == {}


def test_forbidden_keys_never_reach_a_log_line():
    stream = capture()
    event(get("test"), "request", **{key: "leaked" for key in FORBIDDEN_KEYS})
    assert "leaked" not in stream.getvalue()


# Values are never inspected, only keys. Deciding what a string is means
# guessing, and the only reliable way to keep prompt text out of a log is to
# never have a field that could hold it.
def test_values_are_not_inspected():
    kept = redact({"principal": "this string happens to look like a prompt"})
    assert len(kept) == 1


def test_exceptions_are_captured_as_a_field():
    stream = capture()
    log = get("test")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("batch.failed", extra={"fields": {"account_id": "1"}})

    record = json.loads(stream.getvalue().strip())
    assert "ValueError: boom" in record["error"]
    assert record["account_id"] == "1"


def test_configure_replaces_existing_handlers():
    """Otherwise every reconfiguration doubles the output, and a log that
    repeats itself is one nobody trusts to be complete."""
    capture()
    stream = capture()
    event(get("test"), "once")
    assert len(stream.getvalue().strip().splitlines()) == 1


def test_level_is_respected():
    stream = io.StringIO()
    configure(level="WARNING", stream=stream)
    get("test").info("suppressed")
    logging.getLogger("test").warning("kept")
    assert "suppressed" not in stream.getvalue()
    assert "kept" in stream.getvalue()
