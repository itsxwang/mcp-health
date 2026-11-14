"""Run ONE candidate end-to-end and write a result JSON file.

Invoked by run.py as its own OS process (own session/process group):
    python -m harness.oneshot <spec.json> <out.json>

Two independent kill switches guarantee no runaway server survives:
  * a SIGALRM watchdog kills the ENTIRE process group (the server child is in
    the same group) after WALL_SECONDS
  * the parent (run.py) also enforces a longer wall clock with SIGKILL to the
    process group

Results are never fabricated: if we get killed mid-run there is simply no
result file, and the parent records a wall-timeout for that candidate.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import sandbox
from .install import install_and_build_argv
from .probe import run_probe

WALL_SECONDS = 270  # SIGALRM; parent uses 300s with SIGKILL as backstop
TIMEOUTS = {"initialize": 25, "list": 20, "call": 25}


def _status_from_phases(install: dict, phases: dict) -> tuple[str, str]:
    if not install.get("ok"):
        return "fail", "install"
    hs = phases.get("handshake")
    if not hs or not hs.get("ok"):
        return "fail", "handshake"
    lt = phases.get("list_tools")
    if not lt or not lt.get("ok"):
        return "fail", "list_tools"
    call = phases.get("call") or {}
    if call.get("attempted"):
        return ("pass", "call") if call.get("ok") else ("partial", "call")
    return "partial", "list_tools"


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: python -m harness.oneshot spec.json out.json", file=sys.stderr)
        return 2
    spec_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    def _alarm(_sig, _frm):
        # Kill the whole group: oneshot + the spawned server + its children.
        os.killpg(os.getpgrp(), signal.SIGKILL)

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(WALL_SECONDS)

    t_start = time.perf_counter()
    net_isolate, net_method, _prefix = sandbox.detect_network_isolation()
    result: dict = {
        "slug": spec.get("slug"),
        "name": spec.get("name"),
        "status": "fail",
        "stage": "init",
        "error": None,
        "net_isolated": net_isolate,
        "env_placeholders": [],
        "isolation": {"method": net_method},
    }
    tmpdir = Path(tempfile.mkdtemp(prefix="mcphi-run-", dir=os.environ.get("MCPHI_TMPBASE", "/tmp/opencode")))
    try:
        inst = install_and_build_argv(spec, tmpdir, timeout=180.0)
        result["install"] = {
            "ok": inst["ok"], "method": inst["method"], "seconds": inst["seconds"],
            "error": inst["error"],
        }
        result["argv"] = inst.get("argv") if inst.get("ok") else None
        if inst.get("ok"):
            env = sandbox.safe_env(tmpdir, inst.get("run_env") or {})
            placeholders = list(spec.get("env_required") or [])
            for name in placeholders:
                env[name] = "mcp-health-index-placeholder"
            result["env_placeholders"] = placeholders
            probe = run_probe(inst["argv"], env, tmpdir, TIMEOUTS, net_isolate, tmpdir)
            phases = probe["phases"]
            result["phases"] = phases
            result["errors"] = probe["errors"]
            result["stderr_tail"] = probe["stderr_tail"]
            result["status"], result["stage"] = _status_from_phases(inst, phases)
        else:
            result["stage"] = "install"
            result["errors"] = [inst.get("error") or "install failed"]
    except BaseException as e:  # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"[:400]
        result["errors"] = (result.get("errors") or []) + [traceback.format_exc()[-500:]]
    finally:
        signal.alarm(0)
        result["wall_seconds"] = round(time.perf_counter() - t_start, 2)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as e:
            print(f"oneshot: failed to write result: {e}", file=sys.stderr)
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
