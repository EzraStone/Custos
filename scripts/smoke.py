#!/usr/bin/env python3
"""Stand the whole product up and drive it in a browser.

Builds a wire batch from the A0 corpus, scans it into a fresh database, serves
the control plane with the built console mounted, and runs console/e2e/smoke.mjs
against it. Everything is torn down afterwards, including the database — the
point is to prove a cold start works, and a smoke that reuses state stops
proving that on the second run.

This is the only test that exercises the built bundle. Everything under
console/src mocks fetch, which cannot catch a request shape the server rejects
or a static mount that shadows an API route.

    make smoke

Skips rather than fails when the pieces are not present: no built console, no
playwright-core, no browser. It is a verification you run, not a gate.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "tok-smoke"
ACCOUNT = "447120043318"


def skip(why: str) -> int:
    print(f"smoke: skipped — {why}")
    return 0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


def main() -> int:
    venv = ROOT / ".venv"
    python = venv / "bin" / "python"
    if not python.exists():
        return skip("no virtualenv; run make setup")
    if not (ROOT / "console" / "dist" / "index.html").is_file():
        return skip("no console build; run make console")
    if not (ROOT / "console" / "node_modules" / "playwright-core").is_dir():
        return skip("playwright-core is not installed; npm i --no-save playwright-core")
    chromium = find_chromium()
    if not chromium or not Path(chromium).exists():
        return skip("no chromium; set CHROMIUM to a browser binary")

    work = Path(tempfile.mkdtemp(prefix="custos-smoke-"))
    port = free_port()
    server: subprocess.Popen | None = None
    try:
        print("smoke: building a batch from the A0 corpus")
        batch = work / "batch.json"
        built = subprocess.run(
            [str(python), "-c", BUILD_BATCH, str(batch)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if built.returncode != 0:
            print(built.stderr.strip() or built.stdout.strip())
            return 1
        print(f"smoke: {built.stdout.strip()}")

        print("smoke: scanning it into a fresh database")
        db = work / "smoke.db"
        scan = subprocess.run(
            [str(venv / "bin" / "custos"), "--db", str(db), "scan", str(batch)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        # `custos scan` exits 1 when it finds unsanctioned agents, deliberately,
        # so it composes with CI. Here that is the success case: a corpus that
        # produced nothing to look at would give the console an empty register
        # and the smoke nothing to prove.
        if scan.returncode not in (0, 1):
            print(scan.stderr.strip() or scan.stdout.strip())
            return 1
        if scan.returncode == 0:
            print("smoke: the scan found no unsanctioned agents; nothing to drive")
            return 1
        print("       " + "\n       ".join(scan.stdout.strip().splitlines()[-2:]))

        print(f"smoke: serving the control plane and console on :{port}")
        server = subprocess.Popen(
            [str(venv / "bin" / "uvicorn"), "custos.api.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT,
            env={**os.environ,
                 "CUSTOS_DB": str(db),
                 "CUSTOS_TOKENS": f"{ACCOUNT}:{TOKEN}",
                 "CUSTOS_CONSOLE_DIR": str(ROOT / "console" / "dist")},
        )
        base = f"http://127.0.0.1:{port}"
        if not wait_for(f"{base}/healthz"):
            print("smoke: the control plane never became healthy")
            return 1

        print("smoke: driving the console\n")
        return subprocess.run(
            ["node", str(ROOT / "console" / "e2e" / "smoke.mjs")],
            cwd=ROOT / "console",
            env={**os.environ, "BASE": base, "TOKEN": TOKEN, "CHROMIUM": chromium},
            check=False,
        ).returncode
    finally:
        if server and server.poll() is None:
            server.send_signal(signal.SIGTERM)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        shutil.rmtree(work, ignore_errors=True)


# Run in the venv's interpreter, where both packages are installed.
BUILD_BATCH = """
import json, sys
from custos_a0.batchbridge import build_batch
batch = build_batch()
body = batch.model_dump(mode="json")
open(sys.argv[1], "w").write(json.dumps(body))
print(f"{len(body['flows']):,} flow records across {len(body['principals'])} principals")
"""


if __name__ == "__main__":
    sys.exit(main())
