"""Every documented invariant must name a test, and that test must exist.

The whole mechanism rests on one thing: breaking an invariant requires
deliberately editing a test named after it. That only works if the names in the
document match tests that actually run.

A rename, a moved file, or an invariant added without a test all break the
guarantee silently — the document keeps claiming a property and nothing checks
it. This is the test that notices.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INVARIANTS = ROOT / "docs" / "SECURITY-INVARIANTS.md"

# Test names are cited in backticks. Go tests are TitleCase, Python are snake.
_CITED = re.compile(r"`(Test[A-Za-z0-9_]+|test_[a-z0-9_]+\*?)`")


@pytest.fixture(scope="module")
def cited_tests() -> set[str]:
    if not INVARIANTS.exists():
        pytest.skip("invariants document not present")
    names = set(_CITED.findall(INVARIANTS.read_text()))
    assert names, "no test names cited; the citation format has drifted"
    return names


@pytest.fixture(scope="module")
def existing_tests() -> str:
    """Every test name defined anywhere in the repository, as one blob."""
    out = []
    for pattern in ("controlplane/tests/*.py", "a0/tests/*.py"):
        for path in ROOT.glob(pattern):
            out.append(path.read_text())
    for path in ROOT.glob("collector/**/*_test.go"):
        out.append(path.read_text())
    return "\n".join(out)


def test_every_cited_test_exists(cited_tests, existing_tests):
    missing = sorted(
        name for name in cited_tests
        if name.rstrip("*") not in existing_tests
    )
    assert not missing, (
        f"the invariants document cites tests that do not exist: {missing}. "
        "Either the test was renamed and the document was not, or the "
        "invariant is claimed and unenforced."
    )


def test_every_invariant_cites_at_least_one_test(cited_tests):
    """An invariant with no named test is a promise, not a guarantee."""
    text = INVARIANTS.read_text()
    sections = re.split(r"\n## (SEC-\d+)", text)[1:]

    uncited = []
    for name, body in zip(sections[::2], sections[1::2], strict=False):
        if not _CITED.search(body):
            uncited.append(name)

    assert not uncited, f"invariants claimed with no enforcing test: {uncited}"


def test_the_documented_count_matches_the_sections():
    """The README says how many invariants there are. A mismatch means one was
    added or removed without the front door noticing."""
    text = INVARIANTS.read_text()
    sections = set(re.findall(r"\n## (SEC-\d+)", text))

    readme = (ROOT / "README.md").read_text()
    listed = set(re.findall(r"\*\*(SEC-\d+)\*\*", readme))

    assert sections == listed, (
        f"documented in invariants but not the README: {sections - listed}; "
        f"in the README but not documented: {listed - sections}"
    )


def test_no_invariant_test_is_skipped_in_ci():
    """A named test that CI does not run is a guarantee nobody checks."""
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.exists():
        pytest.skip("workflow not present")

    text = workflow.read_text()
    invariants_job = text[text.index("invariants:"):] if "invariants:" in text else ""
    assert invariants_job, "there is no invariants job in CI"

    for sec in re.findall(r"\n## (SEC-\d+)", INVARIANTS.read_text()):
        assert sec in invariants_job, (
            f"{sec} is documented but its job step does not mention it"
        )


def test_git_is_available_for_the_repository_checks():
    """Guards the fixtures above: if the glob found nothing, every assertion
    passes vacuously."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
