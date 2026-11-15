"""SQLite persistence for candidates, run results, and run metadata.

Everything the site generator renders comes from here — no result is ever
invented in the site; untested candidates are shown as UNTESTED.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
  slug          TEXT PRIMARY KEY,
  name          TEXT, title TEXT, description TEXT,
  source        TEXT,            -- npm | pypi | command | unresolved
  identifier    TEXT, version TEXT, origin TEXT,
  repo_url      TEXT, stars INTEGER, license TEXT, pushed_at TEXT,
  spec_json     TEXT,
  tested_at     TEXT,
  status        TEXT,            -- pass | partial | fail | untested
  stage         TEXT,            -- install | handshake | list_tools | call
  install_ok    INTEGER, handshake_ok INTEGER,
  tool_count    INTEGER, resources_count INTEGER,
  call_ok INTEGER, call_tool TEXT,
  install_s REAL, handshake_s REAL, list_s REAL, call_s REAL, wall_s REAL,
  error TEXT, stderr_tail TEXT, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, finished_at TEXT,
  tested INTEGER, n_pass INTEGER, n_partial INTEGER, n_fail INTEGER,
  notes TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.commit()
    return con


def upsert_candidate(con: sqlite3.Connection, spec: dict, enrichment: dict | None = None) -> None:
    e = enrichment or {}
    con.execute(
        """INSERT INTO servers (slug, name, title, description, source, identifier, version,
             origin, repo_url, stars, license, pushed_at, spec_json, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'untested')
           ON CONFLICT(slug) DO UPDATE SET
             name=excluded.name, title=excluded.title, description=excluded.description,
             source=excluded.source, identifier=excluded.identifier, version=excluded.version,
             origin=excluded.origin, repo_url=excluded.repo_url,
             stars=COALESCE(excluded.stars, servers.stars),
             license=COALESCE(excluded.license, servers.license),
             pushed_at=COALESCE(excluded.pushed_at, servers.pushed_at),
             spec_json=excluded.spec_json""",
        (spec["slug"], spec.get("name"), spec.get("title"), spec.get("description"),
         spec.get("source"), spec.get("identifier"), spec.get("version"), spec.get("origin"),
         spec.get("repo_url"), e.get("stars"), e.get("license"), e.get("pushed_at"),
         json.dumps(spec, ensure_ascii=False)),
    )
    con.commit()


def enrich(con: sqlite3.Connection, slug: str, stars: int | None, license_: str | None, pushed_at: str | None) -> None:
    con.execute(
        """UPDATE servers SET stars=COALESCE(?, stars), license=COALESCE(?, license),
             pushed_at=COALESCE(?, pushed_at) WHERE slug=?""",
        (stars, license_, pushed_at, slug),
    )
    con.commit()


def record_result(con: sqlite3.Connection, result: dict) -> None:
    slug = result["slug"]
    phases = result.get("phases") or {}
    hs = phases.get("handshake") or {}
    lt = phases.get("list_tools") or {}
    rs = phases.get("list_resources") or {}
    cl = phases.get("call") or {}
    inst = result.get("install") or {}
    tools = lt.get("tools") or []
    con.execute(
        """UPDATE servers SET
             tested_at=datetime('now'), status=?, stage=?,
             install_ok=?, handshake_ok=?, tool_count=?, resources_count=?,
             call_ok=?, call_tool=?,
             install_s=?, handshake_s=?, list_s=?, call_s=?, wall_s=?,
             error=?, stderr_tail=?, detail_json=?
           WHERE slug=?""",
        (result.get("status"), result.get("stage"),
         1 if inst.get("ok") else 0,
         1 if hs.get("ok") else 0,
         lt.get("count") if lt.get("ok") else None,
         rs.get("count") if rs.get("ok") else None,
         1 if cl.get("attempted") and cl.get("ok") else (0 if cl.get("attempted") else None),
         cl.get("tool"),
         inst.get("seconds"), hs.get("seconds"), lt.get("seconds"), cl.get("seconds"),
         result.get("wall_seconds"),
         result.get("error") or "; ".join(result.get("errors") or [])[:500] or None,
         result.get("stderr_tail") or None,
         json.dumps(result, ensure_ascii=False, default=str), slug),
    )
    con.commit()


def candidates(con: sqlite3.Connection, only_untested: bool = True, limit: int | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT * FROM servers WHERE source IN ('npm','pypi','command') "
           + ("AND tested_at IS NULL " if only_untested else "")
           + "ORDER BY (origin <> 'submit'), (stars IS NULL), stars DESC, name ASC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql).fetchall()


def meta_set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
    con.commit()


def meta_get(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def record_run(con: sqlite3.Connection, started: str, tested: int, n_pass: int, n_partial: int, n_fail: int, notes: str) -> None:
    con.execute(
        "INSERT INTO runs(started_at, finished_at, tested, n_pass, n_partial, n_fail, notes) VALUES(?,datetime('now'),?,?,?,?,?)",
        (started, tested, n_pass, n_partial, n_fail, notes),
    )
    con.commit()
