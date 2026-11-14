"""Static site generator: pure HTML/CSS (+ ~15 lines of vanilla JS for table
sorting). No frameworks, no build step, deployable to GitHub Pages as-is.

Output:
  site/index.html        sortable index table of every candidate
  site/server/<slug>.html  one detail page per server
  site/summary.html      how tests are run, environment, failure-mode breakdown
  site/badges/<slug>.svg   embeddable badges
  site/data.json         full machine-readable results
  site/style.css
"""
from __future__ import annotations

import argparse
import html
import json
import platform
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import badges, db  # noqa: E402

DEFAULT_BASE = "https://YOUR-GITHUB-USERNAME.github.io/mcp-health-index"

STATUS_LABEL = {"pass": "PASS", "partial": "PARTIAL", "fail": "FAIL", "untested": "UNTESTED"}


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def fmt_s(v) -> str:
    if v is None:
        return "–"
    try:
        return f"{float(v):.2f}s"
    except (TypeError, ValueError):
        return "–"


def fmt_ms(v) -> str:
    if v is None:
        return "–"
    try:
        return f"{int(float(v) * 1000)}ms"
    except (TypeError, ValueError):
        return "–"


def fmt_dt(v) -> str:
    if not v:
        return "–"
    return str(v)[:10]


def fmt_int(v) -> str:
    return "–" if v is None else f"{int(v):,}"


def load(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM servers WHERE source IN ('npm','pypi','command') "
        "ORDER BY (stars IS NULL), stars DESC, name ASC"
    ).fetchall()


def stats(rows) -> dict:
    s = {"total": len(rows), "pass": 0, "partial": 0, "fail": 0, "untested": 0,
         "tested": 0, "tools_sum": 0, "tools_n": 0, "hs_sum": 0.0, "hs_n": 0,
         "call_sum": 0.0, "call_n": 0}
    for r in rows:
        st = r["status"] or "untested"
        s[st] = s.get(st, 0) + 1
        if r["tested_at"]:
            s["tested"] += 1
        if r["tool_count"] is not None:
            s["tools_sum"] += r["tool_count"]
            s["tools_n"] += 1
        if r["handshake_s"] is not None:
            s["hs_sum"] += r["handshake_s"]
            s["hs_n"] += 1
        if r["call_s"] is not None:
            s["call_sum"] += r["call_s"]
            s["call_n"] += 1
    return s


def status_chip(status: str) -> str:
    cls = {"pass": "ok", "partial": "warn", "fail": "bad", "untested": "mute"}.get(status, "mute")
    return f'<span class="chip {cls}">{STATUS_LABEL.get(status, "UNTESTED")}</span>'


def page_shell(title: str, desc: str, body: str, base: str, sub: bool = False) -> str:
    rel = "../" if sub else ""
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(desc)}">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{rel}style.css">
</head>
<body>
{body}
<footer>Generated {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())} by the MCP Health Index harness.
Automated smoke tests of public MCP servers — not a security audit.
<a href="{rel}index.html">Index</a> ·
<a href="{rel}summary.html">Method &amp; environment</a> ·
<a href="{rel}data.json">data.json</a></footer>
</body></html>'''


def write_badges(rows, out: Path, base: str) -> None:
    bdir = out / "badges"
    bdir.mkdir(parents=True, exist_ok=True)
    for r in rows:
        svg = badges.badge_svg(r["status"] or "untested", r["tool_count"], r["call_s"], r["stage"])
        (bdir / f"{r['slug']}.svg").write_text(svg, encoding="utf-8")


def data_json(rows, con, out: Path, base: str) -> None:
    con_rows = con.execute("SELECT key, value FROM meta").fetchall()
    meta = {r["key"]: r["value"] for r in con_rows}
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": base,
        "environment": meta,
        "servers": [
            {k: r[k] for k in (
                "slug", "name", "title", "description", "source", "identifier", "version",
                "origin", "repo_url", "stars", "license", "pushed_at", "tested_at",
                "status", "stage", "install_ok", "handshake_ok", "tool_count",
                "resources_count", "call_ok", "call_tool", "install_s", "handshake_s",
                "list_s", "call_s", "wall_s", "error", "stderr_tail")}
            for r in rows
        ],
    }
    (out / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def index_page(rows, con, out: Path, base: str) -> None:
    s = stats(rows)
    fail_stage_counts: dict[str, int] = {}
    for r in rows:
        if r["status"] in ("fail", "partial"):
            k = f'{r["status"]}:{r["stage"] or "?"}'
            fail_stage_counts[k] = fail_stage_counts.get(k, 0) + 1
    fails_html = ", ".join(f"<code>{esc(k)} × {v}</code>" for k, v in
                           sorted(fail_stage_counts.items(), key=lambda kv: -kv[1])[:12])
    chips = [
        f'<span class="chip ok">PASS {s["pass"]}</span>',
        f'<span class="chip warn">PARTIAL {s["partial"]}</span>',
        f'<span class="chip bad">FAIL {s["fail"]}</span>',
        f'<span class="chip mute">UNTESTED {s["untested"]}</span>',
    ]
    trows = []
    for r in rows:
        st = r["status"] or "untested"
        trows.append(f'''<tr>
<td data-v="{esc(r['name'] or r['slug'])}"><a href="server/{esc(r['slug'])}.html">{esc(r['title'] or r['slug'])}</a>
<div class="sub">{esc(r['name'])}</div></td>
<td data-v="{esc(r['source'] or '')}">{esc(r['source'] or '–')}</td>
<td data-v="{esc(st)}">{status_chip(st)}</td>
<td data-v="{r['tool_count'] if r['tool_count'] is not None else -1}">{r['tool_count'] if r['tool_count'] is not None else '–'}</td>
<td data-v="{r['handshake_s'] if r['handshake_s'] is not None else -1}">{fmt_s(r['handshake_s'])}</td>
<td data-v="{r['call_s'] if r['call_s'] is not None else -1}">{fmt_ms(r['call_s'])}</td>
<td data-v="{esc(r['license'] or '')}">{esc(r['license'] or '–')}</td>
<td data-v="{esc(r['pushed_at'] or '')}">{fmt_dt(r['pushed_at'])}</td>
<td data-v="{r['stars'] or 0}">{fmt_int(r['stars'])}</td>
</tr>''')
    body = f'''
<header class="hero">
<h1>MCP Health Index</h1>
<p class="tagline">We actually <em>ran</em> the MCP servers. Every entry below was installed and
smoke-tested live as an isolated stdio MCP client: install → handshake → list tools → one
read-only tool call. Not another curated list — a live health check.</p>
<p>{''.join(chips)} <span class="chip">tested {s['tested']}/{s['total']}</span></p>
<p class="subnote">v0 scope: stdio servers from the
<a href="https://official.modelcontextprotocol.xyz">official MCP registry</a>.
Run method and isolation details: <a href="summary.html">summary</a>.</p>
</header>
<main>
<table id="tbl">
<thead><tr>
<th data-sort="str">Server</th><th data-sort="str">Source</th><th data-sort="str">Status</th>
<th data-sort="num">Tools</th><th data-sort="num">Boot (handshake)</th><th data-sort="num">Call</th>
<th data-sort="str">License</th><th data-sort="str">Last push</th><th data-sort="num">★</th>
</tr></thead>
<tbody>
{''.join(trows)}
</tbody></table>
<p class="subnote">Common failure stages: {fails_html or '–'}.</p>
</main>
<script>
document.querySelectorAll('#tbl th[data-sort]').forEach(function(th){{
 th.addEventListener('click',function(){{
  var tb=document.querySelector('#tbl tbody'), rows=[].slice.call(tb.rows), i=th.cellIndex,
      num=th.dataset.sort==='num'; th.asc=!th.asc;
  rows.sort(function(a,b){{var A=a.cells[i].dataset.v,B=b.cells[i].dataset.v;
   if(num){{A=parseFloat(A);B=parseFloat(B);return (A>B)-(A<B);}}
   return A.localeCompare(B);}});
  if(!th.asc)rows.reverse(); rows.forEach(function(r){{tb.appendChild(r);}});
 }});
}});
</script>'''
    html_doc = page_shell("MCP Health Index — live smoke-tested MCP servers",
                          "Live-compat-tested MCP servers: install, handshake, tool list and one "
                          "harmless tool call, run in isolation.", body, base)
    (out / "index.html").write_text(html_doc, encoding="utf-8")


def summary_page(rows, con, out: Path, base: str) -> None:
    s = stats(rows)
    stages: dict[str, dict[str, int]] = {}
    for r in rows:
        if r["tested_at"] and r["status"] in ("pass", "partial", "fail"):
            stg = f"{r['status']}:{r['stage'] or '?'}"
            stages[stg] = stages.get(stg, 0) + 1
    stage_rows = "".join(
        f"<tr><td><code>{esc(k)}</code></td><td>{v}</td></tr>" for k, v in
        sorted(stages.items(), key=lambda kv: -kv[1]))
    meta_rows = con.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
    meta_html = "".join(f"<tr><td><code>{esc(m['key'])}</code></td><td>{esc(m['value'])}</td></tr>"
                        for m in meta_rows)
    net_iso_row = con.execute("SELECT value FROM meta WHERE key='net_isolation'").fetchone()
    net_state = net_iso_row["value"] if net_iso_row else "unknown"
    py = sys.version.split()[0]
    body = f'''
<header class="hero"><h1>Method &amp; environment</h1>
<p class="tagline">How the MCP Health Index smoke-tests servers — and exactly what the isolation
does and does not guarantee.</p></header>
<main>
<h2>What a "run" is</h2>
<ol>
<li><strong>Install</strong> — npm package → fresh <code>npm install --prefix $TMP</code>; PyPI →
fresh <code>uv venv $TMP/venv</code> + <code>uv pip install</code>. Never installed globally.</li>
<li><strong>Handshake</strong> — act as an MCP client over stdio (official <code>mcp</code> SDK):
protocol initialize.</li>
<li><strong>List tools / resources</strong> — capability discovery, with latency.</li>
<li><strong>One harmless call</strong> — a tool whose <em>name</em> matches read-only verbs
(list/get/read/search/…), called with inert arguments built only from <em>required</em> schema
fields (enum→first value, default, or <code>"test"</code>/1/0). Names matching destructive verbs
are skipped. Returned content is truncated.</li>
</ol>
<h2>Isolation that exists</h2>
<ul>
<li>Resource limits on the server process: 60 CPU-s, 2 GiB address space, 64 child processes,
64 MiB file writes, 256 fds, no core dumps.</li>
<li>Fresh sanitized environment: no inherited tokens/secrets, throwaway HOME/TMPDIR, all installs
ephemeral in a temp dir that is deleted.</li>
<li>Hard wall clock: SIGALRM + parent SIGKILL to the entire process group (each candidate runs in
its own session).</li>
<li>Network namespace isolation: <strong>{esc(net_state)}</strong></li>
</ul>
<h2>What isolation does NOT protect against</h2>
<ul>
<li><strong>Network egress</strong> (current run): server code could exfiltrate data or scan the
network. Mitigated by the sanitized empty environment (nothing to steal) but not prevented.</li>
<li>Sandbox is not a security boundary against a truly malicious package — a determined attacker
inside the resource limits can still make outbound network calls. Treat every server as hostile.</li>
<li>Tool <em>calls</em> are heuristic: name-based read-only detection can mislabel a tool. Inputs
are inert, but a read-only call can still hit an external API with our placeholder credentials.</li>
<li>We do not audit the code; "PASS" means "booted, listed tools, answered one read-only call".</li>
</ul>
<h2>Results breakdown</h2>
<table><thead><tr><th>status:stage</th><th>servers</th></tr></thead>
<tbody>{stage_rows or '<tr><td colspan="2">–</td></tr>'}</tbody></table>
<h2>Run environment</h2>
<table><tbody>{meta_html}</tbody></table>
<p class="subnote">Harness: Python {esc(py)} · official <code>mcp</code> SDK 2.3.0 · node/npm for
npm candidates · uv for PyPI candidates.</p>
</main>'''
    html_doc = page_shell("MCP Health Index — method & environment",
                          "How the health tests are run, isolation guarantees and limits.", body, base)
    (out / "summary.html").write_text(html_doc, encoding="utf-8")


def detail_page(r, out: Path, base: str) -> None:
    slug = r["slug"]
    detail = {}
    try:
        detail = json.loads(r["detail_json"] or "{}")
    except Exception:
        pass
    phases = detail.get("phases") or {}
    lt = phases.get("list_tools") or {}
    tools = lt.get("tools") or []
    cl = phases.get("call") or {}
    tool_rows = "".join(
        f"<tr><td><code>{esc(t['name'])}</code></td><td>{esc(t.get('description') or '')}</td>"
        f"<td><code>{esc(', '.join(t.get('required_args') or []))}</code></td></tr>"
        for t in tools[:40])
    md = badges.badge_markdown(base, slug)
    badge_rel = f"../badges/{slug}.svg"
    spec_info = f'''
<h2>Spec</h2>
<table><tbody>
<tr><td>Registry name</td><td>{esc(r['name'])}</td></tr>
<tr><td>Source</td><td>{esc(r['source'])} — <code>{esc(r['identifier'] or '')}{('@' + r['version']) if r['version'] else ''}</code> ({esc(r['origin'] or '')})</td></tr>
<tr><td>Repo</td><td>{('<a href="' + esc(r['repo_url']) + '">' + esc(r['repo_url']) + '</a>') if r['repo_url'] else '–'} ★ {fmt_int(r['stars'])}</td></tr>
<tr><td>License</td><td>{esc(r['license'] or 'unknown')}</td></tr>
<tr><td>Last push</td><td>{fmt_dt(r['pushed_at'])}</td></tr>
<tr><td>Description</td><td>{esc(r['description'] or '')}</td></tr>
</tbody></table>'''
    inst = (detail.get("install") or {})
    ph_rows = ""
    for name, key in (("install", None), ("handshake", "handshake"), ("list_tools", "list_tools"),
                      ("list_resources", "list_resources"), ("call", "call")):
        if name == "install":
            ph_rows += (f"<tr><td><code>install</code></td><td>{'✅' if inst.get('ok') else '❌'}</td>"
                        f"<td>{fmt_s(inst.get('seconds'))}</td><td>{esc(inst.get('method') or '')}"
                        f"{(' — ' + esc(inst.get('error'))) if inst.get('error') else ''}</td></tr>")
            continue
        ph = phases.get(name)
        if not ph and name != "call":
            continue
        ok = ph.get("ok") if name != "call" else (ph.get("ok") if ph.get("attempted") else None)
        icon = "✅" if ok else ("⏭" if (name == "call" and ph and not ph.get("attempted")) else "❌")
        note = ph.get("error") or ph.get("note") or ""
        if name == "call":
            note = f"tool={esc(ph.get('tool'))} args={esc(json.dumps(ph.get('args') or {}))}" + (f" — {note}" if note else "")
        ph_rows += (f"<tr><td><code>{esc(name)}</code></td><td>{icon}</td>"
                    f"<td>{fmt_s(ph.get('seconds')) if ph else '–'}</td><td>{note}</td></tr>")
    argv = detail.get("argv") or []
    body = f'''
<header class="hero"><h1>{esc(r['title'] or slug)}</h1>
<p class="tagline"><img src="{badge_rel}" alt="badge"> {status_chip(r['status'] or 'untested')}
&nbsp; stage: <code>{esc(r['stage'] or '–')}</code></p></header>
<main>
{spec_info}
<h2>Run</h2>
<table><thead><tr><th>phase</th><th>result</th><th>time</th><th>detail</th></tr></thead>
<tbody>{ph_rows}</tbody></table>
<p>Launch argv: <code>{esc(' '.join(argv))}</code>
{('· required env vars filled with placeholders: ' + esc(', '.join(detail.get('env_placeholders') or []))) if detail.get('env_placeholders') else ''}</p>
<h2>Tools ({r['tool_count'] if r['tool_count'] is not None else 0})</h2>
{('<table><thead><tr><th>name</th><th>description</th><th>required args</th></tr></thead><tbody>' + tool_rows + '</tbody></table>') if tool_rows else '<p class="subnote">none listed</p>'}
{('<h2>Call result preview</h2><pre class="call">' + esc((cl.get('preview') or '')[:1200]) + '</pre>') if cl.get('preview') else ''}
{('<h2>Server stderr (tail)</h2><pre class="call">' + esc(r['stderr_tail'] or '') + '</pre>') if r['stderr_tail'] else ''}
{('<h2>Errors</h2><pre class="call">' + esc(' | '.join(detail.get('errors') or [])) + '</pre>') if detail.get('errors') else ''}
<h2>Embed the badge</h2>
<pre class="call">{esc(md)}</pre>
<p><a href="../index.html">← back to the index</a></p>
</main>'''
    html_doc = page_shell(f"MCP Health Index — {r['title'] or slug}",
                          f"Live smoke-test report for MCP server {r['name']}.", body, base, sub=True)
    (out / "server").mkdir(parents=True, exist_ok=True)
    (out / "server" / f"{slug}.html").write_text(html_doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/results.db")
    ap.add_argument("--out", default="site")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help="public site base URL for badge markdown")
    args = ap.parse_args()
    con = db.connect(Path(args.db))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = load(con)
    write_badges(rows, out, args.base_url)
    data_json(rows, con, out, args.base_url)
    index_page(rows, con, out, args.base_url)
    summary_page(rows, con, out, args.base_url)
    for r in rows:
        detail_page(r, out, args.base_url)
    css = CSS
    (out / "style.css").write_text(css, encoding="utf-8")
    print(f"site: wrote {len(rows)} server pages, badges, index, summary → {out}/")
    con.close()
    return 0


CSS = ''':root{--bg:#0d1117;--fg:#c9d1d9;--muted:#8b949e;--link:#58a6ff;--card:#161b22;--border:#30363d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
main{max-width:1100px;margin:0 auto;padding:0 20px 40px}
header.hero{max-width:1100px;margin:0 auto;padding:34px 20px 10px}
h1{font-size:1.9rem;margin:.2em 0}h2{margin-top:1.6em;font-size:1.15rem}
.tagline{color:var(--fg);max-width:52rem}.subnote{color:var(--muted);font-size:.9rem}
.chip{display:inline-block;padding:2px 10px;border-radius:99px;background:var(--card);border:1px solid var(--border);font-size:.8rem;margin-right:4px}
.chip.ok{color:#7ee787;border-color:#2ea043}.chip.warn{color:#e3b341;border-color:#d29922}
.chip.bad{color:#ff7b72;border-color:#cf222e}.chip.mute{color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:.88rem;background:var(--card);border:1px solid var(--border);border-radius:8px;overflow:hidden}
th,td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
th{background:#1b2129;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap}
tr:hover td{background:#1c2333}
td .sub{color:var(--muted);font-size:.78rem}
code{background:#21262d;padding:1px 5px;border-radius:4px;font-size:.85rem}
pre.call{background:#010409;border:1px solid var(--border);border-radius:8px;padding:12px;overflow:auto;max-height:22rem;font-size:.8rem;white-space:pre-wrap}
footer{max-width:1100px;margin:30px auto;padding:10px 20px;border-top:1px solid var(--border);color:var(--muted);font-size:.8rem}'''

if __name__ == "__main__":
    sys.exit(main())
