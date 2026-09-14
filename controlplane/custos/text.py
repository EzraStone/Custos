"""Text a person typed, on its way into something somebody else reads.

The collector has had this discipline since SEC-23: what leaves a customer
account is a label they chose or a word AWS wrote, never free text, and never
a length that breaks the line it lands in.

The API had none of it. `operator`, `note` and `reason` are free text from the
only write surface this product has, and they end up in three places that
matter — the audit trail, which is the record of who granted what; the report's
provenance section, which is what a security team reads; and a column-aligned
terminal table.

Nothing here is about injection. The report escapes what it renders and the
store parameterises every query. It is about a name with a newline in it
breaking the artefact an operator reads before granting authority, and about a
field with no bound being a field somebody will eventually put a log file in.
"""

from __future__ import annotations

import unicodedata

OPERATOR = 128
"""A human identity. Long enough for `firstname.lastname@a-long-company.example`
and short enough that the audit trail stays a table."""

NOTE = 128
"""What a customer calls an endpoint. `llm-gateway`, `vllm`, `litellm (eu)`."""

REASON = 512
"""Why an agent was retired. A sentence, possibly two, and a paragraph is not
unreasonable — this one is read by whoever asks about the decision later."""

VALUE = 64
"""A CIDR or an AWS service name. The longest of either is well under this."""


def one_line(value: str, limit: int) -> str:
    """Collapse to one renderable line, bounded.

    Whitespace of any kind becomes a single space, because text that came from
    a copy-paste is still what the person meant. Other control characters are
    dropped: there is no reading of a name in which a bell character is part of
    it. Over the limit the value is cut and marked, so what a reader sees is
    visibly a prefix rather than a different value.
    """
    out: list[str] = []
    space = False
    for char in value:
        if char.isspace():
            space = bool(out)
        elif unicodedata.category(char).startswith("C"):
            # Dropped entirely, and not treated as a separator: a name with a
            # zero-width character in the middle of a word is one word.
            continue
        else:
            if space:
                out.append(" ")
                space = False
            out.append(char)
    text = "".join(out)
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text
