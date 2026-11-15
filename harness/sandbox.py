"""Isolation for running untrusted MCP server code.

What we enforce on every spawned server process (see README.md SAFETY for
exactly what this does and does NOT protect against):
  * hard rlimits: CPU-seconds, address space, child processes, file size,
    open file descriptors, core dumps disabled
  * a fresh, sanitized environment: no inherited tokens/secrets, fresh
    HOME and TMPDIR (inside a throwaway temp dir)
  * the whole process tree is killed on wall-clock timeout (parent sends
    SIGKILL to the process group)
  * OPTIONAL network isolation via a new network namespace (bwrap/unshare),
    auto-detected; many container hosts forbid user namespaces, in which
    case we say so honestly instead of pretending

Design note: resource limits are applied by executing a tiny wrapper
(harness/limitwrap.py) that setrlimit()s itself and then exec()s the real
server command, so the limits apply to the actual server process, not a
parent shell.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Applied to the server process (and, thanks to RLIMIT_NPROC, its children).
RLIMITS: dict[str, int] = {
    "RLIMIT_CPU": 60,                    # CPU-seconds per process
    "RLIMIT_AS": 2_147_483_648,          # 2 GiB virtual address space
    # NOTE: RLIMIT_NPROC counts ALL tasks (processes AND threads) of the real
    # uid, not just this tree — a desktop session can already hold ~900+. It
    # stays an effective fork-bomb cap but must sit above the host baseline.
    "RLIMIT_NPROC": 2048,
    "RLIMIT_FSIZE": 64 * 1024 * 1024,    # max single-file write: 64 MiB
    "RLIMIT_NOFILE": 1024,               # open fds (256 was too tight: servers
                                         # reading hundreds of files concurrently hit EMFILE)
    "RLIMIT_CORE": 0,                    # no core dumps
}

STATE_FILE = Path(os.environ.get("MCPHI_NETISO_STATE", "/tmp/opencode/mcphi-netiso.json"))

# In-process cache only. A persistent verdict caused 200+ runs to miss newly
# available namespace isolation after an rlimit fix — never cache across
# processes again.
_NETISO_CACHE: tuple[bool, str, list] | None = None


def detect_network_isolation() -> tuple[bool, str, list[str]]:
    """Detect whether we can run server processes inside a network namespace.

    Returns (available, human_explanation, prefix_argv).
    """
    global _NETISO_CACHE
    if _NETISO_CACHE is not None:
        return _NETISO_CACHE

    available, method, prefix = False, "not available on this host", []

    def _try_bwrap() -> bool:
        # Test with the SAME rlimits a real run applies, so the detection
        # reflects production behavior exactly.
        probe_cmd = [*limitwrap_command(), *["bwrap", "--unshare-net", "--bind", "/", "/",
                                             "--dev", "/dev", "--proc", "/proc"], "--", "true"]
        try:
            r = subprocess.run(probe_cmd, capture_output=True, timeout=15)
            return r.returncode == 0
        except Exception:
            return False

    if shutil.which("bwrap") and _try_bwrap():
        available, method = True, "bubblewrap --unshare-net"
        prefix = ["bwrap", "--unshare-net", "--bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    if not available and shutil.which("unshare"):
        probe_cmd = [*limitwrap_command(), "unshare", "-n", "--", "true"]
        try:
            r = subprocess.run(probe_cmd, capture_output=True, timeout=15)
            if r.returncode == 0:
                available, method = True, "unshare -n (network namespace)"
                prefix = ["unshare", "-n", "--"]
        except Exception:
            pass
    if not available:
        method = ("unavailable on this host (no unprivileged user/network namespaces) "
                  "- server processes ran with normal network egress")
    _NETISO_CACHE = (available, method, prefix)
    return available, method, prefix


def limitwrap_command() -> list[str]:
    """argv prefix that applies RLIMITS then exec()s whatever follows after '--'."""
    wrapper = Path(__file__).with_name("limitwrap.py")
    return [sys.executable, str(wrapper), json.dumps(RLIMITS)]


def wrap_argv(argv: list[str], net_isolate: bool) -> list[str]:
    """Build the full argv for the server: rlimit wrapper + optional ns prefix + server."""
    out = limitwrap_command()
    if net_isolate:
        _, _, prefix = detect_network_isolation()
        if prefix:
            out += prefix
    out += ["--", *argv]
    return out


def safe_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal, secret-free environment for untrusted code.

    Deliberately does NOT inherit os.environ: any tokens/keys/creds present in
    the operator's environment (or CI) never reach server processes.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "SHELL": "/bin/sh",
        "MCP_HEALTH_INDEX_RUN": "1",
    }
    if extra:
        env.update(extra)
    return env
