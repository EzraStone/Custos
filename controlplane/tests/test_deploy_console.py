"""The image must put the console where it tells the control plane to look.

These are two independent lines in a Dockerfile — a COPY destination and an
ENV — and nothing connects them. If they drift, the image still builds, every
other test still passes, the container still starts, and the console is a 404.
The failure only shows up when a customer opens the page.

So the pair is asserted here rather than discovered there.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    if not DOCKERFILE.exists():
        pytest.skip("deploy/Dockerfile not present")
    return DOCKERFILE.read_text()


def test_console_is_copied_where_the_env_var_points(dockerfile: str) -> None:
    copied = re.search(
        r"^COPY\s+--from=console\s+\S+\s+(\S+)\s*$", dockerfile, re.MULTILINE
    )
    assert copied, "the image no longer copies the built console out of its stage"

    declared = re.search(r"CUSTOS_CONSOLE_DIR=(\S+)", dockerfile)
    assert declared, "the image copies the console but never tells the app where it is"

    assert copied.group(1).rstrip("/") == declared.group(1).rstrip("/"), (
        "the console is copied to one path and looked for at another; "
        "the image would build and serve a 404"
    )


def test_the_console_stage_builds_from_the_lockfile(dockerfile: str) -> None:
    # npm install in an image resolves the dependency tree at build time, so
    # the bytes shipped to a customer need not be the bytes CI tested.
    assert "npm ci" in dockerfile
    assert not re.search(r"^\s*RUN\s+npm\s+install", dockerfile, re.MULTILINE)


def test_the_build_context_excludes_node_modules() -> None:
    if not DOCKERIGNORE.exists():
        pytest.fail("no .dockerignore: every build uploads console/node_modules")
    patterns = {
        line.strip()
        for line in DOCKERIGNORE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    # A bare "node_modules/" is matched against the whole path and so only
    # excludes one at the context root, which is not where ours lives.
    assert "**/node_modules" in patterns, (
        "node_modules must be excluded with a pattern that matches at any depth"
    )


def test_the_runtime_image_carries_no_build_tooling(dockerfile: str) -> None:
    # The console ships as static files served by the control plane. If Node
    # ends up in the runtime image, either someone turned the console into a
    # second service or a stage boundary was lost — both change what a customer
    # is being asked to run, and what their scanner will report.
    runtime = "FROM " + dockerfile.split("\nFROM ")[-1]
    base = runtime.splitlines()[0]
    assert "node" not in base.lower(), f"runtime image is built on Node: {base}"
    assert not re.search(
        r"^\s*RUN\b.*\b(npm|npx|node|yarn|pnpm)\b", runtime, re.MULTILINE
    ), "the runtime stage runs a Node tool"
