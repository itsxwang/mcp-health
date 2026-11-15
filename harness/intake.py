"""Issue intake: turn submitted GitHub issues into harness candidates.

This is what makes the site's claim true: "new issues are auto-picked-up by the
daily harness cron". The daily workflow runs this BEFORE the harness batch; open
issues with a parseable package spec become candidates (origin="submit") that are
tested FIRST in the next run.

Parsing is strict: an issue only becomes a candidate if it declares a registry
(npm/pypi) and a syntactically valid package name. Nothing is fabricated: an
unparseable issue is skipped and simply stays open in the tracker.

No GitHub auth is required at runtime (60 req/h unauthenticated is ample for a
low-traffic tracker); set GH_TOKEN env var in CI for a higher limit.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import db, spec as specmod

DEFAULT_REPO = "itsxwang/mcp-health"
UA = "mcp-health-index/0.1 (issue intake)"
NPM_NAME = re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")
PYPI_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _get_json(url: str) -> dict | list:
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def parse_issue(body: str) -> dict | None:
    """Extract a candidate spec from an issue body, or None if unparseable.

    Expected (the prefilled form on the site generates exactly this):
        Package registry: npm            (or pypi)
        Package name: @scope/name
        Version (optional): 1.2.3
        Repository URL: https://github.com/...
        Required env vars (names only): A, B
        Notes: ...
    """
    if not body:
        return None

    def field(label_regex: str) -> str:
        m = re.search(rf"{label_regex}\s*[:：]\s*([^\n]*)", body, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    registry = field(r"Package registry").lower()
    name = field(r"Package name")
    version = field(r"Version \(optional\)")
    repo = field(r"Repository URL")
    envs = field(r"Required env vars")
    if registry not in ("npm", "pypi") or not name:
        # free-form fallback: "npm: @scope/name" or "pypi: pkg-name"
        m = re.search(r"\b(npm|pypi)\s*[:：]\s*([@\w./-]+)", body, re.IGNORECASE)
        if m:
            registry, name = m.group(1).lower(), m.group(2)
    name = name.strip().strip("`\"' ")
    version = version.strip().strip("`\"' ")
    if registry not in ("npm", "pypi") or not name:
        return None
    ok = NPM_NAME.match(name) if registry == "npm" else PYPI_NAME.match(name)
    if not ok:
        return None
    if version in ("-", "latest", "n/a", "none"):
        version = ""
    env_required = [e.strip() for e in envs.split(",") if e.strip()] if envs else []
    issue_num = 0
    m = re.search(r"Issue number:\s*(\d+)", body)
    if m:
        issue_num = int(m.group(1))
    return {
        "slug": specmod.slugify(name),
        "name": name,
        "title": name,
        "description": f"Submitted via GitHub issue #{issue_num}" if issue_num else
                       "Submitted via GitHub issue",
        "source": registry,
        "identifier": name,
        "version": version or None,
        "env_required": env_required,
        "cli_args": [],
        "repo_url": repo or None,
        "origin": "submit",
        "issue_number": issue_num,
        "published_at": None,
    }


def ingest(db_path: Path, repo: str = DEFAULT_REPO, max_issues: int = 30) -> dict:
    stats = {"open_issues": 0, "parsed": 0, "skipped": 0, "added": 0, "updated": 0, "errors": []}
    try:
        issues = _get_json(
            f"https://api.github.com/repos/{repo}/issues"
            f"?state=open&per_page={max_issues}&sort=created&direction=desc")
    except Exception as e:  # noqa: BLE001
        stats["errors"].append(f"issues fetch failed: {e}")
        return stats
    con = db.connect(db_path)
    for it in issues:
        if it.get("pull_request"):  # PRs use the same endpoint; skip
            continue
        stats["open_issues"] += 1
        parsed = parse_issue(it.get("body") or "")
        if not parsed:
            stats["skipped"] += 1
            continue
        parsed["issue_number"] = it.get("number", 0)
        parsed["description"] = f"Submitted via GitHub issue #{it.get('number', 0)}"
        stats["parsed"] += 1
        existing = con.execute("SELECT origin, tested_at FROM servers WHERE slug=?", (parsed["slug"],)).fetchone()
        db.upsert_candidate(con, parsed)
        if existing:
            stats["updated"] += 1
        else:
            stats["added"] += 1
    db.meta_set(con, "last_intake_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    con.close()
    return stats


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/results.db")
    repo = os.environ.get("MCPHEALTH_REPO", DEFAULT_REPO)
    print(json.dumps(ingest(db_path, repo), indent=2))
