"""Rebuild the SQLite DB from the durable per-run result files.

data/results/<slug>.json are the raw, never-fabricated outputs of every oneshot
run (tracked in git). This script re-creates the DB rows from them so a lost DB
never means lost results. Run fetch.py first so the candidate rows exist.

    python -m harness.fetch data/results.db
    python -m harness.rebuild data/results.db
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import db

RESULTS_DIR = Path("data/results")


def rebuild(db_path: Path) -> dict:
    con = db.connect(db_path)
    stats = {"files": 0, "replayed": 0, "missing_rows": [], "statuses": {}}
    for f in sorted(RESULTS_DIR.glob("*.json")):
        stats["files"] += 1
        try:
            result = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            stats["missing_rows"].append(f"{f.name}: unparseable ({e})")
            continue
        slug = result.get("slug") or f.stem
        result.setdefault("slug", slug)
        result.setdefault("status", "fail")
        result.setdefault("stage", "unknown")
        row = con.execute("SELECT 1 FROM servers WHERE slug=?", (slug,)).fetchone()
        if not row:
            # row vanished from candidates: re-insert minimal record so the
            # recorded result is never dropped
            con.execute(
                """INSERT INTO servers (slug, name, source, identifier, origin, status, spec_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (slug, result.get("name") or slug, "npm", slug, "recovered",
                 result.get("status"), json.dumps({"slug": slug})),
            )
            stats["missing_rows"].append(slug)
        db.record_result(con, result)
        stats["replayed"] += 1
        st = result.get("status")
        stats["statuses"][st] = stats["statuses"].get(st, 0) + 1
    con.close()
    return stats


if __name__ == "__main__":
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/results.db")
    print(json.dumps(rebuild(db_path), indent=2))
