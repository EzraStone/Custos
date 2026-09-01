"""Shared test helpers."""

from __future__ import annotations


def prose(text: str) -> str:
    """Collapse whitespace before asserting on a rendered sentence.

    Source line wrapping is not meaningful in HTML or in a message written to
    be pasted into a ticket. A test that breaks when a paragraph is rewrapped is
    a test that gets deleted rather than fixed, so every assertion on prose goes
    through this.
    """
    return " ".join(text.split())
