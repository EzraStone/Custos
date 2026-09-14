"""Bounding the free text that comes in through the only write surface."""

from __future__ import annotations

from custos.text import one_line


def test_a_newline_becomes_a_space():
    """An operator name with a newline lands in the audit trail and in a
    column-aligned terminal table."""
    assert one_line("ezra\nstone", 128) == "ezra stone"


def test_runs_of_whitespace_collapse():
    assert one_line("  llm   gateway\t(eu)  ", 128) == "llm gateway (eu)"


def test_a_control_character_is_dropped_without_splitting_a_word():
    """A zero-width character in the middle of a word is one word, and a bell
    is not part of anybody's name."""
    assert one_line("gate​way", 128) == "gateway"
    assert one_line("gate\x07way", 128) == "gateway"


def test_a_long_value_is_cut_and_marked():
    """What a reader sees has to be visibly a prefix rather than a different
    value."""
    got = one_line("a" * 200, 32)
    assert got == "a" * 32 + "…"


def test_a_value_at_the_limit_is_untouched():
    assert one_line("a" * 32, 32) == "a" * 32


def test_text_of_only_whitespace_is_empty():
    """And the caller decides what an empty value means — for `operator` it is
    a refusal, for `note` it is simply nothing."""
    assert one_line(" \t\n ", 128) == ""


def test_ordinary_text_is_unchanged():
    """The common case must cost nothing. Every one of these is a real value
    somebody would type."""
    for value in (
        "ezra@custos.dev", "llm-gateway", "10.0.7.0/24",
        "Decommissioned in INC-4471", "vllm (staging)",
    ):
        assert one_line(value, 512) == value
