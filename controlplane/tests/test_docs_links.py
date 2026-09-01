"""Every relative link and image in the documentation resolves.

A broken link in a README is not caught by anything else in this repository:
it renders as normal text on GitHub, or as a missing-image icon, and the first
person to notice is a reader who was trusting it. Screenshots are the worst
case — an image that fails to load looks like a rendering problem rather than a
missing file, so nobody reports it.

Only relative targets are checked. External URLs are somebody else's uptime.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# ![alt](target) and [text](target), capturing the target. Anchors and external
# schemes are filtered out below rather than in the pattern, so a malformed one
# shows up as a failure instead of silently not matching.
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

DOCS = ["README.md", *(str(p.relative_to(ROOT)) for p in sorted((ROOT / "docs").rglob("*.md")))]


def _targets(text: str) -> list[str]:
    out = []
    for target in _LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append(target.split("#", 1)[0])
    return [t for t in out if t]


@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_resolve(doc: str) -> None:
    path = ROOT / doc
    if not path.exists():
        pytest.skip(f"{doc} not present")

    broken = [t for t in _targets(path.read_text()) if not (path.parent / t).exists()]
    assert not broken, f"{doc} links to files that do not exist: {broken}"


def test_the_readme_shows_the_console() -> None:
    """The screenshots are the only thing in the README that shows the product
    rather than describing it. Losing them silently would be a real loss."""
    readme = (ROOT / "README.md").read_text()
    images = [t for t in _targets(readme) if t.endswith(".png")]
    assert images, "the README no longer shows the console"
    for image in images:
        assert (ROOT / image).stat().st_size > 0, f"{image} is empty"
