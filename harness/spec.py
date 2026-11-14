"""Canonical server-spec model, derived from official MCP registry records.

A "spec" is the JSON recipe the harness needs to install and run one MCP
server in isolation. v0 supports stdio-transport servers installed from npm
or PyPI (ephemeral), plus raw commands for fallback specs.
"""
from __future__ import annotations

import re
from typing import Any


def slugify(name: str) -> str:
    """Filesystem-safe unique slug from a registry name like 'io.github.user/pkg'."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s).strip("-.")
    return s or "unknown"


def _pick(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _stdio_transport(p: dict) -> bool:
    t = _pick(p, "transport", "transport_type", default=None)
    if t is None:
        return True  # no transport declared => assume local stdio
    if isinstance(t, dict):
        t = t.get("type", "stdio")
    return str(t).lower() in ("stdio", "local")


def _runtime_args(p: dict) -> list[str]:
    """Best-effort positional/named CLI args declared by the registry package."""
    out: list[str] = []
    for a in _pick(p, "arguments", "runtime_arguments", "package_arguments", default=[]) or []:
        if not isinstance(a, dict):
            continue
        t = (a.get("type") or "").lower()
        name = a.get("name") or ""
        val = _pick(a, "value", "default")
        placeholder = (a.get("placeholder") or "").strip("<> ")
        try:
            if t == "positional":
                out.append(str(val) if val not in (None, "") else (placeholder or ""))
            elif t == "named":
                if a.get("type_flag"):  # {"type":"named","typeFlag":...}? defensive
                    pass
                if val not in (None, ""):
                    out += [f"--{name}", str(val)]
                elif placeholder:
                    out += [f"--{name}", placeholder]
                elif a.get("required"):
                    out += [f"--{name}", "test"]
            elif t == "boolean":
                out.append(f"--{name}")
        except Exception:
            continue
    return [x for x in out if x != ""]


def spec_from_registry_record(rec: dict) -> dict | None:
    """Turn one registry API record into a runnable spec, or None if not testable."""
    server = rec.get("server") or rec
    name = server.get("name") or ""
    if not name:
        return None
    packages = server.get("packages") or []
    candidates = [
        p for p in packages
        if isinstance(p, dict)
        and str(_pick(p, "registry_type", "registryType", default="")).lower() in ("npm", "pypi")
        and _stdio_transport(p)
    ]
    if not candidates:
        return None  # remote-only / docker-only servers are out of scope for v0
    pkg = candidates[0]
    source = str(_pick(pkg, "registry_type", "registryType")).lower()
    identifier = pkg.get("identifier") or ""
    if not identifier:
        return None
    repo = server.get("repository")
    if isinstance(repo, dict):
        repo = repo.get("url") or repo.get("source_url")
    return {
        "slug": slugify(name),
        "name": name,
        "title": server.get("title") or name,
        "description": (server.get("description") or "")[:400],
        "source": source,
        "identifier": identifier,
        "version": pkg.get("version"),
        "env_required": [
            v.get("name") for v in (_pick(pkg, "environment_variables", "environmentVariables") or [])
            if isinstance(v, dict) and v.get("required") and v.get("name")
        ],
        "cli_args": _runtime_args(pkg),
        "repo_url": repo,
        "origin": "registry",
        "published_at": (rec.get("_meta") or {}).get("io.modelcontextprotocol.registry/official", {}).get("publishedAt"),
    }


def spec_from_npm_fallback(full_name: str, repo_url: str, npm_doc: dict, stars: int | None) -> dict | None:
    """GitHub-search fallback: a repo matched to a real npm package (checked live)."""
    if not npm_doc:
        return None
    name = f"github:{full_name}"
    return {
        "slug": slugify(full_name),
        "name": name,
        "title": npm_doc.get("name") or full_name,
        "description": (npm_doc.get("description") or "")[:400],
        "source": "npm",
        "identifier": npm_doc["name"],
        "version": npm_doc.get("version"),
        "env_required": [],
        "cli_args": [],
        "repo_url": repo_url,
        "origin": "github-search",
        "stars": stars,
        "published_at": None,
    }
