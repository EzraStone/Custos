"""Boot the whole product against corpus data, for things that need to look at it.

Both `make smoke` and `make screenshots` need the same five minutes of setup: a
batch built from the A0 corpus, scanned into a fresh database, served by a
control plane with the console mounted. This is that, once.

Everything is temporary. The database is thrown away at the end, because the
point of both callers is that a cold start works, and a stack that reuses state
stops proving that on the second run.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
ACCOUNT = "447120043318"
# A second account nothing was scanned into, so the fleet picker has two rows
# to show. It is real as far as the token store is concerned and empty as far
# as the register is concerned, which is exactly the case worth photographing.
FLEET_ACCOUNT = "209384756102"


class Missing(Exception):
    """A precondition is absent, so the caller should skip rather than fail."""


@dataclass(frozen=True)
class Stack:
    base: str
    token: str
    fleet_token: str
    chromium: str


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(url: str, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def find_chromium() -> str:
    explicit = os.getenv("CHROMIUM")
    if explicit:
        return explicit
    root = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    matches = sorted(root.glob("chromium-*/chrome-linux/chrome"))
    return str(matches[-1]) if matches else ""


def check_preconditions() -> str:
    """Return the chromium path, or raise Missing naming what is absent."""
    if not (VENV / "bin" / "python").exists():
        raise Missing("no virtualenv; run make setup")
    if not (ROOT / "console" / "dist" / "index.html").is_file():
        raise Missing("no console build; run make console")
    if not (ROOT / "console" / "node_modules" / "playwright-core").is_dir():
        raise Missing("playwright-core is not installed; npm i --no-save playwright-core")
    chromium = find_chromium()
    if not chromium or not Path(chromium).exists():
        raise Missing("no chromium; set CHROMIUM to a browser binary")
    return chromium


# Run inside the venv's interpreter, where both packages are importable.
_BUILD_BATCH = """
import json, sys
from custos_a0.batchbridge import build_batch
body = build_batch().model_dump(mode="json")
open(sys.argv[1], "w").write(json.dumps(body))
print(f"{len(body['flows']):,} flow records across {len(body['principals'])} principals")
"""


@contextmanager
def serving(quiet: bool = False) -> Iterator[Stack]:
    """Yield a running control plane with the console mounted and data in it."""
    def say(message: str) -> None:
        if not quiet:
            print(message)

    chromium = check_preconditions()
    work = Path(tempfile.mkdtemp(prefix="custos-stack-"))
    server: subprocess.Popen | None = None
    try:
        say("stack: building a batch from the A0 corpus")
        batch = work / "batch.json"
        built = subprocess.run(
            [str(VENV / "bin" / "python"), "-c", _BUILD_BATCH, str(batch)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if built.returncode != 0:
            raise RuntimeError(built.stderr.strip() or built.stdout.strip())
        say(f"stack: {built.stdout.strip()}")

        say("stack: scanning it into a fresh database")
        db = work / "stack.db"
        scan = subprocess.run(
            [str(VENV / "bin" / "custos"), "--db", str(db), "scan", str(batch)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        # `custos scan` exits 1 when it finds unsanctioned agents, deliberately,
        # so it composes with CI. Here that is the success case: a scan that
        # found nothing would leave both callers with an empty register.
        if scan.returncode not in (0, 1):
            raise RuntimeError(scan.stderr.strip() or scan.stdout.strip())
        if scan.returncode == 0:
            raise RuntimeError("the scan found no unsanctioned agents; nothing to show")
        say("       " + scan.stdout.strip().splitlines()[-1])

        port = free_port()
        token, fleet_token = "tok-stack", "tok-fleet"
        say(f"stack: serving the control plane and console on :{port}")
        server = subprocess.Popen(
            [str(VENV / "bin" / "uvicorn"), "custos.api.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT,
            env={**os.environ,
                 "CUSTOS_DB": str(db),
                 "CUSTOS_TOKENS": ",".join([
                     f"{ACCOUNT}:{token}",
                     f"{ACCOUNT}:{fleet_token}",
                     f"{FLEET_ACCOUNT}:{fleet_token}",
                 ]),
                 "CUSTOS_CONSOLE_DIR": str(ROOT / "console" / "dist")},
        )
        base = f"http://127.0.0.1:{port}"
        if not wait_for(f"{base}/healthz"):
            raise RuntimeError("the control plane never became healthy")

        yield Stack(base=base, token=token, fleet_token=fleet_token, chromium=chromium)
    finally:
        if server and server.poll() is None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        shutil.rmtree(work, ignore_errors=True)


def run_node(script: Path, stack: Stack, extra: dict[str, str] | None = None) -> int:
    return subprocess.run(
        ["node", str(script)],
        cwd=ROOT / "console",
        env={**os.environ,
             "BASE": stack.base,
             "TOKEN": stack.token,
             "FLEET_TOKEN": stack.fleet_token,
             "CHROMIUM": stack.chromium,
             **(extra or {})},
        check=False,
    ).returncode
