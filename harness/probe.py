"""Act as an MCP client over stdio using the official `mcp` SDK.

Phases per server:
  1. initialize / handshake   (protocol negotiation)
  2. list_tools               (capability discovery)
  3. list_resources           (secondary capability, failures tolerated)
  4. ONE harmless tool call   (read-only-looking tool, conservative inputs)

Tool-call safety policy:
  * pick tools whose NAME matches read-only verbs (list/get/read/search/...)
  * skip any tool whose name matches risky verbs (delete/create/send/exec/...)
  * build arguments only from REQUIRED schema fields: enum -> first value,
    default -> default, string -> "test", number -> 1, boolean -> False
  * truncate any returned content (never echo large server output)

Every phase is bounded by asyncio.wait_for; the OS-level parent (run.py) plus
a SIGALRM watchdog (oneshot.py) guarantee the whole tree dies on hang.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from . import sandbox

SAFE_RX = re.compile(
    r"(^|[^a-z])(list|ls|get|read|search|query|find|fetch|describe|info|health|status|ping|"
    r"schema|help|doc|docs|suggest|count|browse|summary|whoami|version|capabilities|show|view|"
    r"check|lookup|cat|echo|hello|greet|welcome|time|current|now|recent|latest|top|available|"
    r"supported|explain|preview|index|scan|inspect|peek|head|tail|measure|convert|calculate|"
    r"validate|profile|stats|overview|about)([^a-z]|$)",
    re.IGNORECASE,
)
RISKY_RX = re.compile(
    r"(delete|destroy|remove|\brm\b|create|make|new|update|write|save|insert|append|edit|modify|"
    r"send|post|submit|publish|run|exec|eval|execute|invoke|admin|install|deploy|set|config|add|"
    r"kill|drop|shutdown|restart|pay|purchase|order|transfer|trade|buy|sell|swap|mail|email|tweet|"
    r"notify|message|comment|push|commit|merge|fork|follow|like|favorite|upvote|ban|block|mute|"
    r"invite|grant|revoke|reset|rotate|login|auth|token|register|sign|approve|reject|close|toggle)",
    re.IGNORECASE,
)


def _fmt_exc(e: BaseException) -> str:
    """Readable message for anyio's nested ExceptionGroups."""
    if isinstance(e, BaseExceptionGroup):
        inner = _fmt_exc(e.exceptions[0]) if e.exceptions else ""
        return f"{type(e).__name__}: {inner}"[:400]
    msg = str(e).strip()
    return f"{type(e).__name__}: {msg}"[:400]


def choose_tool(tools: list[dict]) -> dict | None:
    """First tool that looks read-only by name and not risky."""
    for t in tools:
        n = (t.get("name") or "")
        if SAFE_RX.search(n) and not RISKY_RX.search(n):
            return t
    return None


def conservative_args(tool: dict) -> dict:
    """Only REQUIRED input fields; values are inert and safe by default.

    Heuristics stay strictly local/safe:
      path-ish -> "." (the throwaway cwd), timezone-ish -> "UTC",
      everything else: enum[0] / declared default / "test" / 1 / false.
    No real URLs, hosts or file paths are ever constructed.
    """
    schema = tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else None
    if not schema:
        return {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") or []
    args: dict = {}
    for key in required:
        p = props.get(key) if isinstance(props.get(key), dict) else {}
        if "const" in p:
            v = p["const"]
        elif isinstance(p.get("enum"), list) and p["enum"]:
            v = p["enum"][0]
        elif p.get("default") is not None:
            v = p["default"]
        else:
            t = p.get("type")
            k = (key or "").lower()
            if t == "string":
                if re.search(r"path|file|dir|folder|filename", k):
                    v = "."
                elif re.search(r"timezone|tz|zone", k):
                    v = "UTC"
                else:
                    v = "test"
            elif t in ("integer", "number"):
                v = 1
            elif t == "boolean":
                v = False
            elif t == "array":
                v = []
            elif t == "object":
                v = {}
            else:
                v = "test"
        args[key] = v
    return args


def _content_preview(result) -> tuple[str, bool]:
    """Flatten a CallToolResult into a short preview + isError flag."""
    parts: list[str] = []
    is_error = bool(getattr(result, "isError", False))
    for item in (getattr(result, "content", None) or [])[:5]:
        itype = getattr(item, "type", "?")
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text[:400])
        else:
            parts.append(f"[{itype}]")
        if len("".join(parts)) > 1200:
            break
    preview = "".join(parts)[:1200]
    return preview, is_error


async def _probe(argv, env, cwd, timeouts, net_isolate, tmpdir: Path) -> dict:
    phases: dict = {}
    errors: list[str] = []
    phase = "spawn"
    err_path = tmpdir / "server-stderr.log"
    errf = open(err_path, "w", encoding="utf-8", errors="replace")
    wrapped = sandbox.wrap_argv(argv, net_isolate)
    params = StdioServerParameters(
        command=wrapped[0], args=wrapped[1:], env=env, cwd=str(cwd),
    )
    net_method = None
    try:
        async with stdio_client(params, errlog=errf) as (read, write):
            async with ClientSession(read, write) as session:
                # 1) initialize/handshake
                phase = "handshake"
                t0 = time.perf_counter()
                init = await asyncio.wait_for(session.initialize(), timeouts["initialize"])
                phases["handshake"] = {
                    "ok": True,
                    "seconds": round(time.perf_counter() - t0, 3),
                    "error": None,
                    "server_info": {
                        "name": getattr(getattr(init, "serverInfo", None), "name", None)
                                or getattr(getattr(init, "server_info", None), "name", None),
                        "version": getattr(getattr(init, "serverInfo", None), "version", None)
                                   or getattr(getattr(init, "server_info", None), "version", None),
                        "protocol_version": getattr(init, "protocolVersion", None),
                    },
                }
                # 2) list_tools
                phase = "list_tools"
                t0 = time.perf_counter()
                lt = await asyncio.wait_for(session.list_tools(), timeouts["list"])
                tools = []
                for t in (getattr(lt, "tools", None) or []):
                    schema = getattr(t, "inputSchema", None)
                    req = list((schema or {}).get("required") or []) if isinstance(schema, dict) else []
                    tools.append({
                        "name": t.name,
                        "description": (getattr(t, "description", None) or "")[:150],
                        "required_args": req,
                    })
                phases["list_tools"] = {
                    "ok": True, "seconds": round(time.perf_counter() - t0, 3),
                    "error": None, "count": len(tools), "tools": tools,
                }
                # 3) list_resources (tolerated failure — many servers have none)
                phase = "list_resources"
                res_count, res_ok, res_err = 0, True, None
                try:
                    t0 = time.perf_counter()
                    rr = await asyncio.wait_for(session.list_resources(), timeouts["list"])
                    res_count = len(getattr(rr, "resources", None) or [])
                    phases["list_resources"] = {
                        "ok": True, "seconds": round(time.perf_counter() - t0, 3),
                        "error": None, "count": res_count,
                    }
                except Exception as e:
                    res_ok, res_err = False, _fmt_exc(e)
                    phases["list_resources"] = {
                        "ok": False, "seconds": None, "error": res_err, "count": 0,
                    }
                # 4) ONE harmless call
                phase = "call"
                tool = choose_tool(tools)
                if tool is None:
                    phases["call"] = {
                        "attempted": False, "ok": False, "tool": None, "args": None,
                        "seconds": None, "error": None, "is_error": False,
                        "note": "no read-only-looking tool name found; call skipped by policy",
                        "preview": None,
                    }
                else:
                    args = conservative_args(tool)
                    t0 = time.perf_counter()
                    try:
                        result = await asyncio.wait_for(
                            session.call_tool(
                                tool["name"], args,
                                read_timeout_seconds=float(timeouts["call"]),
                            ),
                            timeouts["call"] + 5,
                        )
                        preview, is_error = _content_preview(result)
                        phases["call"] = {
                            "attempted": True, "ok": not is_error,
                            "tool": tool["name"], "args": args,
                            "seconds": round(time.perf_counter() - t0, 3),
                            "error": None, "is_error": is_error, "preview": preview,
                            "note": None,
                        }
                    except Exception as e:
                        phases["call"] = {
                            "attempted": True, "ok": False, "tool": tool["name"], "args": args,
                            "seconds": round(time.perf_counter() - t0, 3),
                            "error": _fmt_exc(e), "is_error": False,
                            "preview": None, "note": None,
                        }
    except BaseException as e:  # noqa: BLE001 — we record every failure mode honestly
        errors.append(f"{phase}: {_fmt_exc(e)}")
        phases.setdefault(phase, {"ok": False, "error": errors[-1]})
        if phase not in ("handshake", "list_tools", "call", "list_resources"):
            phases["spawn"] = {"ok": False, "error": errors[-1]}
    finally:
        try:
            errf.flush()
            errf.close()
        except Exception:
            pass
        tail = ""
        try:
            tail = err_path.read_text(errors="replace")[-800:]
        except Exception:
            pass
    return {"phases": phases, "errors": errors, "stderr_tail": tail, "net_method": net_method}


def run_probe(argv, env, cwd, timeouts, net_isolate, tmpdir: Path) -> dict:
    """Sync wrapper; runs the async probe. Never raises."""
    try:
        return asyncio.run(_probe(argv, env, cwd, timeouts, net_isolate, tmpdir))
    except BaseException as e:  # hard crash inside asyncio loop itself
        return {
            "phases": {}, "errors": [f"fatal: {_fmt_exc(e)}"],
            "stderr_tail": "", "net_method": None,
        }
