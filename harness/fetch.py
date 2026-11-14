"""Candidate fetcher.

Primary source: official MCP registry public API (no auth).
  https://registry.modelcontextprotocol.io/v0/servers
Enrichment: GitHub stars-search (top-starred topic:mcp-server repos) gives
stars/license/last-push for free in the search response; per-repo core API
calls are spent sparingly (60/hour unauthenticated) for the rest.
Fallback: if the registry is unreachable, GitHub-search top repos are matched
against the npm registry by exact name (verified live, never guessed).
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import db, spec

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
GH_SEARCH_URL = "https://api.github.com/search/repositories?q=topic:mcp-server&sort=stars&order=desc&per_page=100&page={page}"
NPM_URL = "https://registry.npmjs.org/{name}"
UA = "mcp-health-index/0.1 (+https://github.com/your-name/mcp-health-index)"


def http_get_json(url: str, timeout: int = 30, retries: int = 2) -> dict:
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {type(last_err).__name__}: {last_err}")


def fetch_registry(max_pages: int = 12, page_size: int = 100) -> list[dict]:
    """Page through the official registry; returns raw server records."""
    records: list[dict] = []
    cursor: str | None = None
    for page in range(max_pages):
        params = {"version": "latest", "limit": str(page_size)}
        if cursor:
            params["cursor"] = cursor
        url = f"{REGISTRY_URL}?{urllib.parse.urlencode(params)}"
        data = http_get_json(url)
        got = data.get("servers") or []
        records.extend(got)
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if not cursor or not got:
            break
    return records


def fetch_github_search(pages: int = 2) -> list[dict]:
    items: list[dict] = []
    for page in range(1, pages + 1):
        try:
            data = http_get_json(GH_SEARCH_URL.format(page=page))
            items.extend(data.get("items") or [])
            if data.get("incomplete_results"):
                break
        except Exception:
            break
    return items


def normalize_repo(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    u = url.strip().rstrip(".git").replace("http://", "https://")
    if "github.com/" not in u:
        return None
    path = u.split("github.com/", 1)[1].strip("/")
    parts = [p for p in path.split("/") if p][:2]
    if len(parts) != 2:
        return None
    return "/".join(parts)


def fetch_repo_info(full_name: str) -> dict | None:
    try:
        return http_get_json(f"https://api.github.com/repos/{full_name}", timeout=15)
    except Exception:
        return None


def npm_lookup(name: str) -> dict | None:
    """Live npm registry lookup; returns minimal doc or None (404 = not found)."""
    try:
        data = http_get_json(NPM_URL.format(name=urllib.parse.quote(name, safe="")) , timeout=20)
        if isinstance(data, dict) and data.get("name") and (data.get("bin") or data.get("main") or data.get("exports")):
            return data
        return None
    except Exception:
        return None


def build_candidates(db_path: Path, gh_repo_budget: int = 45) -> dict:
    """Populate the DB with testable candidates + enrichment. Returns stats."""
    con = db.connect(db_path)
    stats = {"registry_records": 0, "stdio_specs": 0, "gh_search_repos": 0,
             "enriched_from_search": 0, "per_repo_calls": 0, "npm_fallback_specs": 0,
             "registry_ok": True, "errors": []}

    # 1) Official registry (preferred)
    try:
        records = fetch_registry()
        stats["registry_records"] = len(records)
    except Exception as e:
        records = []
        stats["registry_ok"] = False
        stats["errors"].append(f"registry fetch failed: {e}")

    repo_to_slug: dict[str, str] = {}
    for rec in records:
        try:
            sp = spec.spec_from_registry_record(rec)
        except Exception as e:
            stats["errors"].append(f"spec parse failed: {e}")
            continue
        if not sp:
            continue
        stats["stdio_specs"] += 1
        db.upsert_candidate(con, sp)
        repo = spec.normalize_repo(sp.get("repo_url"))
        if repo:
            repo_to_slug[repo] = sp["slug"]

    # 2) GitHub stars search: popularity proxy + free license/freshness metadata
    gh_items = fetch_github_search(pages=2)
    stats["gh_search_repos"] = len(gh_items)
    star_map: dict[str, dict] = {}
    for it in gh_items:
        full = it.get("full_name") or ""
        if not full:
            continue
        lic = ((it.get("license") or {}).get("spdx_id") or None)
        star_map[full.lower()] = {
            "stars": it.get("stargazers_count"), "license": None if lic in ("NOASSERTION", "Other") else lic,
            "pushed_at": it.get("pushed_at"), "description": it.get("description"),
        }

    matched_repos = set()
    for repo, slug in repo_to_slug.items():
        info = star_map.get(repo.lower())
        if info:
            db.enrich(con, slug, info["stars"], info["license"], info["pushed_at"])
            stats["enriched_from_search"] += 1
            matched_repos.add(repo.lower())

    # 3) GitHub-search fallback (also when registry died): match top repos to real npm pkgs
    fallback_done = 0
    for it in gh_items[:80]:
        full = it.get("full_name") or ""
        if not full or full.lower() in matched_repos:
            continue
        info = star_map.get(full.lower(), {})
        short = full.split("/")[-1]
        npm_doc = None
        ident = None
        for cand_name in (f"@{full.replace('/', '/')}", short):
            if fallback_done >= 120:
                break
            npm_doc = npm_lookup(cand_name)
            fallback_done += 1
            if npm_doc:
                ident = cand_name
                break
        if npm_doc and ident:
            npm_doc["name"] = ident
            sp = spec.spec_from_npm_fallback(full, it.get("html_url"), npm_doc, info.get("stars"))
            if sp:
                existing = con.execute("SELECT 1 FROM servers WHERE slug=?", (sp["slug"],)).fetchone()
                if not existing:
                    db.upsert_candidate(con, sp, {"stars": info.get("stars"),
                                                  "license": info.get("license"),
                                                  "pushed_at": info.get("pushed_at")})
                    stats["npm_fallback_specs"] += 1
    stats["npm_fallback_lookups"] = fallback_done

    # 4) Per-repo GitHub enrichment for registry candidates missing stars (budget-bound)
    remaining = gh_repo_budget
    for row in con.execute(
        "SELECT slug, repo_url, stars FROM servers WHERE repo_url IS NOT NULL AND stars IS NULL"
    ).fetchall():
        if remaining <= 0:
            break
        repo = row["repo_url"]
        info = fetch_repo_info(repo)
        stats["per_repo_calls"] += 1
        remaining -= 1
        if info:
            lic = (info.get("license") or {}).get("spdx_id")
            db.enrich(con, row["slug"], info.get("stargazers_count"),
                      None if lic in (None, "NOASSERTION", "Other") else lic,
                      info.get("pushed_at"))

    con.close()
    return stats


if __name__ == "__main__":
    import sys
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/results.db")
    print(json.dumps(build_candidates(db_path), indent=2))
