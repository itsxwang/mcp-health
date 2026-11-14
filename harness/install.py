"""Ephemeral installers: turn a spec into a runnable argv without polluting the host.

npm packages : fresh `npm install --prefix <tmpdir>`; the declared bin (or
               main / exports fallback) is executed with `node`.
PyPI packages: fresh `uv venv <tmpdir>/venv` + `uv pip install`; the console
               script (or `python -m module` fallback) is executed.
raw command  : executed as-is (fallback path).

Everything lands inside a throwaway temp dir that the caller deletes, so
there is no persistent global install state. Install (network allowed) is
deliberately separated from run (network isolation possible) because tools
like npx conflate the two.
"""
from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .sandbox import safe_env

NPM_NODE_OPTIONS = "--max-old-space-size=384"  # keeps node's virtual memory under RLIMIT_AS


def _run(cmd, env, cwd, timeout, what) -> dict:
    t0 = time.perf_counter()
    try:
        r = subprocess.run(
            cmd, env=env, cwd=str(cwd), timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
        )
        return {
            "ok": r.returncode == 0, "seconds": round(time.perf_counter() - t0, 3), "error": None,
            "stdout_tail": (r.stdout or "")[-800:], "stderr_tail": (r.stderr or "")[-1500:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "seconds": round(time.perf_counter() - t0, 3),
                "error": f"{what}: timeout after {timeout}s", "stdout_tail": "", "stderr_tail": ""}
    except FileNotFoundError as e:
        return {"ok": False, "seconds": round(time.perf_counter() - t0, 3),
                "error": f"{what}: {e}", "stdout_tail": "", "stderr_tail": ""}


def _npm_entrypoint(pkgdir: Path, identifier: str) -> tuple[list[str] | None, str]:
    """Resolve the executable entry of an installed npm package."""
    pj = pkgdir / "package.json"
    if not pj.is_file():
        return None, "package.json missing after install"
    try:
        data = json.loads(pj.read_text(errors="replace"))
    except Exception as e:
        return None, f"package.json unparseable: {e}"
    short = identifier.rsplit("/", 1)[-1]
    bin_ = data.get("bin")
    entry = None
    if isinstance(bin_, dict) and bin_:
        entry = bin_.get(short) or next(iter(bin_.values()))
    elif isinstance(bin_, str):
        entry = bin_
    if entry:
        path = (pkgdir / entry).resolve()
        if path.is_file():
            return ["node", str(path)], "bin"
        return None, f"bin entry not found on disk: {entry}"
    main = data.get("main")
    if main and (pkgdir / main).is_file():
        return ["node", str(pkgdir / main)], "main"
    exports = data.get("exports")
    if isinstance(exports, dict):
        for key in (".", "index", "default"):
            v = exports.get(key)
            cand = v.get("import") or v.get("default") if isinstance(v, dict) else v
            if isinstance(cand, str) and cand.endswith(".js"):
                path = (pkgdir / cand.lstrip("./")).resolve()
                if path.is_file():
                    return ["node", str(path)], "exports"
    return None, "no bin/main/exports entrypoint declared"


def _pypi_entrypoint(venv: Path, identifier: str) -> tuple[list[str] | None, str]:
    """Resolve console script (or -m module) of an installed PyPI package."""
    sp_dirs = list((venv / "lib").glob("python3.*/site-packages"))
    if not sp_dirs:
        return None, "site-packages not found"
    sp = sp_dirs[0]
    scripts: dict[str, str] = {}
    for dist_info in sorted(sp.glob("*.dist-info")):
        ep = dist_info / "entry_points.txt"
        if ep.is_file():
            try:
                cp = configparser.ConfigParser()
                cp.read(ep, encoding="utf-8")
                for section in ("console_scripts", "gui_scripts"):
                    if cp.has_section(section):
                        for name, target in cp.items(section):
                            scripts[name] = target
            except Exception:
                continue
    short = identifier.replace("-", "_")
    pick = scripts.get(short) or scripts.get(identifier) or (next(iter(scripts), None) if scripts else None)
    if pick:
        binp = venv / "bin" / pick
        if binp.is_file():
            return [str(binp)], "console_script"
    mod = identifier.replace("-", "_")
    if (sp / mod).is_dir() or (sp / f"{mod}.py").is_file():
        return [str(venv / "bin" / "python"), "-m", mod], "python -m"
    return None, f"no console script / module entry (scripts={sorted(scripts)[:5]})"


def install_and_build_argv(spec: dict, tmpdir: Path, timeout: float = 180.0) -> dict:
    """Install the server into tmpdir; return {ok, argv, method, seconds, error, run_env}."""
    source = spec.get("source")
    env = safe_env(tmpdir, {"npm_config_cache": str(tmpdir / "npm-cache"),
                            "npm_config_update_notifier": "false",
                            "UV_CACHE_DIR": str(tmpdir / "uv-cache"),
                            "UV_PYTHON_INSTALL_DIR": str(tmpdir / "uv-python")})
    if source == "npm":
        return _install_npm(spec, tmpdir, env, timeout)
    if source == "pypi":
        return _install_pypi(spec, tmpdir, env, timeout)
    if source == "command":
        argv = spec.get("command")  # raw commands carry their argv in 'command'
        if not argv:
            return {"ok": False, "argv": [], "method": "raw", "seconds": 0.0,
                    "error": "spec has no command", "run_env": {}}
        return {"ok": True, "argv": argv, "method": "raw", "seconds": 0.0, "error": None,
                "run_env": {}}
    return {"ok": False, "argv": [], "method": source, "seconds": 0.0,
            "error": f"unsupported source '{source}' (no run recipe)", "run_env": {}}


def _install_npm(spec, tmpdir: Path, env, timeout) -> dict:
    npm = shutil.which("npm")
    if not npm:
        return {"ok": False, "argv": [], "method": "npm", "seconds": 0.0,
                "error": "npm not on PATH (node toolchain unavailable)", "run_env": {}}
    ident, ver = spec["identifier"], spec.get("version")
    ref = f"{ident}@{ver}" if ver else ident
    r = _run([npm, "install", "--prefix", str(tmpdir), "--no-audit", "--no-fund",
              "--loglevel=error", ref], env, tmpdir, timeout, "npm install")
    if not r["ok"]:
        err = r["error"] or f"npm install failed: {(r['stderr_tail'] or r['stdout_tail'] or '')[-300:]}"
        return {"ok": False, "argv": [], "method": "npm", "seconds": r["seconds"],
                "error": err, "run_env": {"install_stderr": r["stderr_tail"]}}
    pkgdir = tmpdir / "node_modules" / ident
    argv, how = _npm_entrypoint(pkgdir, ident)
    if not argv:
        return {"ok": False, "argv": [], "method": "npm", "seconds": r["seconds"],
                "error": how, "run_env": {}}
    argv = argv + list(spec.get("cli_args") or [])
    run_env = {"NODE_OPTIONS": NPM_NODE_OPTIONS}
    return {"ok": True, "argv": argv, "method": f"npm:{how}", "seconds": r["seconds"],
            "error": None, "run_env": run_env}


def _install_pypi(spec, tmpdir: Path, env, timeout) -> dict:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv).is_file():
        uv = shutil.which("uv")
    if not uv:
        return {"ok": False, "argv": [], "method": "pypi", "seconds": 0.0,
                "error": "uv not installed (needed for ephemeral PyPI venvs)", "run_env": {}}
    venv = tmpdir / "venv"
    r = _run([uv, "venv", str(venv), "--python", sys.executable], env, tmpdir, min(timeout, 60), "uv venv")
    if not r["ok"]:
        return {"ok": False, "argv": [], "method": "pypi", "seconds": r["seconds"],
                "error": r["error"] or f"uv venv failed: {r['stderr_tail'][-300:]}", "run_env": {}}
    ident, ver = spec["identifier"], spec.get("version")
    ref = f"{ident}=={ver}" if ver else ident
    r = _run([uv, "pip", "install", "--python", str(venv / "bin" / "python"),
              "--no-compile", ref], env, tmpdir, timeout, "uv pip install")
    if not r["ok"]:
        err = r["error"] or f"uv pip install failed: {(r['stderr_tail'] or '')[-300:]}"
        return {"ok": False, "argv": [], "method": "pypi", "seconds": r["seconds"],
                "error": err, "run_env": {}}
    argv, how = _pypi_entrypoint(venv, ident)
    if not argv:
        return {"ok": False, "argv": [], "method": "pypi", "seconds": r["seconds"],
                "error": how, "run_env": {}}
    argv = argv + list(spec.get("cli_args") or [])
    return {"ok": True, "argv": argv, "method": f"pypi:{how}", "seconds": r["seconds"],
            "error": None, "run_env": {}}
