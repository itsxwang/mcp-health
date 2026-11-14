"""Orchestrator: run harness.oneshot on candidates with a worker pool.

Each candidate runs in its own OS process group (start_new_session), so a
hung or fork-bombing server is annihilated with SIGKILL to the whole group
without affecting other workers. Results are persisted per candidate as soon
as they land, so a crash never loses progress.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import db, sandbox

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "data" / "results"
SPECS_DIR = ROOT / "data" / "specs"


def run_one(row, timeout: float, net_flag: str) -> dict:
    slug = row["slug"]
    spec_path = SPECS_DIR / f"{slug}.json"
    out_path = RESULTS_DIR / f"{slug}.json"
    spec_path.write_text(row["spec_json"], encoding="utf-8")
    if out_path.exists():
        out_path.unlink()
    cmd = [sys.executable, "-m", "harness.oneshot", str(spec_path), str(out_path)]
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "MCPHI_TMPBASE": "/tmp/opencode/mcphi-runs",
           "MCPHI_NPM_CACHE": "/tmp/opencode/mcphi-npm-cache",
           "MCPHI_UV_CACHE": "/tmp/opencode/mcphi-uv-cache"}
    for d in ("MCPHI_TMPBASE", "MCPHI_NPM_CACHE", "MCPHI_UV_CACHE"):
        Path(env[d]).mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    note = None
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        note = "parent wall-timeout: SIGKILL to process group"
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            p.wait(timeout=10)
        except Exception:
            pass
    result: dict
    if out_path.is_file():
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception as e:
            result = {"slug": slug, "status": "fail", "stage": "crash",
                      "error": f"result file unreadable: {e}"}
    else:
        result = {"slug": slug, "status": "fail", "stage": "wall_timeout",
                  "error": note or "no result file (hard crash?)"}
    result.setdefault("slug", slug)
    result.setdefault("status", "fail")
    result.setdefault("stage", "unknown")
    if note:
        result["error"] = note
        result["stage"] = "wall_timeout"
    result["_parent_seconds"] = round(time.perf_counter() - started, 2)
    return result


def apply_net_flag(net_flag: str) -> bool:
    if net_flag == "off":
        return False
    available, method, _ = sandbox.detect_network_isolation()
    if net_flag == "on" and not available:
        print(f"!! network isolation requested but {method}")
    return available


def main() -> int:
    ap = argparse.ArgumentParser(description="run the MCP Health Index harness")
    ap.add_argument("--db", default="data/results.db")
    ap.add_argument("--limit", type=int, default=0, help="max candidates to test (0=all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=300.0, help="per-server wall seconds")
    ap.add_argument("--force", action="store_true", help="retest already-tested servers")
    ap.add_argument("--net-isolate", choices=["auto", "on", "off"], default="auto")
    args = ap.parse_args()

    con = db.connect(Path(args.db))
    rows = db.candidates(con, only_untested=not args.force, limit=args.limit or None)
    net_isolate = apply_net_flag(args.net_isolate)
    avail, method, _ = sandbox.detect_network_isolation()
    net_state = method if avail else "OFF (unavailable: server processes get network egress)"
    if avail and args.net_isolate == "off":
        net_state = "OFF (disabled by flag)"
    db.meta_set(con, "net_isolation", net_state)
    db.meta_set(con, "python", sys.version.split()[0])
    db.meta_set(con, "started_run_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    print(f"harness: {len(rows)} candidates | net isolation: {net_state}")
    counts = {"pass": 0, "partial": 0, "fail": 0, "untested": 0}
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _task(row):
        res = run_one(row, args.timeout, args.net_isolate)
        try:
            db.record_result(con, res)
        except Exception as e:
            print(f"  !! db error for {res.get('slug')}: {e}")
        return res

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_task, row) for row in rows]
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            counts[res.get("status", "fail")] = counts.get(res.get("status", "fail"), 0) + 1
            phases = res.get("phases") or {}
            hs = (phases.get("handshake") or {}).get("seconds")
            tc = (phases.get("list_tools") or {}).get("count")
            print(f"[{done}/{len(rows)}] {res['slug'][:48]:48s} "
                  f"{res.get('status','?'):8s} stage={res.get('stage','?'):12s} "
                  f"tools={tc if tc is not None else '-':>4s} hs={hs if hs is not None else '-'}s "
                  f"wall={res.get('_parent_seconds','?')}s")

    db.meta_set(con, "last_finished_run_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    db.record_run(con, started_at, done, counts["pass"], counts["partial"], counts["fail"], json.dumps(counts))
    print(f"done: {counts}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
