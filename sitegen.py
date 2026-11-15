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

DEFAULT_BASE = "https://itsxwang.github.io/mcp-health"
GITHUB_REPO = "itsxwang/mcp-health"
CONTACT_EMAIL = "xion.dev7@gmail.com"
STALE_DAYS = 7

SUBMIT_BODY = """### MCP server submission

Package registry: npm            <!-- npm or pypi -->
Package name: @scope/name
Version (optional):
Repository URL:
Required env vars (names only):
Notes:

---
Submitted via the MCP Health Index site. Open issues are ingested by the daily
harness cron and tested in the next batch — check the server's page within ~24h."""

import urllib.parse as _up

SUBMIT_URL = (
    f"https://github.com/{GITHUB_REPO}/issues/new"
    + "?labels=server-submission"
    + "&title=" + _up.quote_plus("Server test request: <package name>")
    + "&body=" + _up.quote(SUBMIT_BODY)
)

STATUS_LABEL = {"pass": "PASS", "partial": "PARTIAL", "fail": "FAIL", "untested": "UNTESTED"}


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


def abs_url(rel: str, base: str) -> str:
    return base.rstrip("/") + "/" + rel.lstrip("/")


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


def is_stale(tested_at: str | None, now=None) -> bool:
    """Stale = tested more than STALE_DAYS days ago."""
    if not tested_at:
        return False
    try:
        t = time.mktime(time.strptime(str(tested_at)[:19], "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return False
    return (time.time() - t) > STALE_DAYS * 86400


def tested_stamp(r) -> str:
    """HTML snippet: last-tested date + duration, visually flagged when stale."""
    if not r["tested_at"]:
        return '<span class="mute">not yet</span>'
    cls = "stale" if is_stale(r["tested_at"]) else "fresh"
    dur = f" · {fmt_s(r['wall_s'])} run" if r["wall_s"] is not None else ""
    return f'<span class="{cls}" title="test duration{dur[1:] if dur else ""}">{fmt_dt(r["tested_at"])}</span>{dur}'


def pass_rate(rows) -> tuple[int, int, float, float]:
    s = stats(rows)
    tested = s["tested"]
    if not tested:
        return 0, 0, 0.0, 0.0
    pr = 100.0 * s["pass"] / tested
    fail_pr = 100.0 * (s["fail"] + s["partial"]) / tested  # anything short of a full PASS
    return tested, s["pass"], pr, fail_pr


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


def page_shell(title: str, desc: str, body: str, base: str, sub: bool = False,
               og_image_rel: str = "og/index.png") -> str:
    rel = "../" if sub else ""
    og = abs_url(og_image_rel, base)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{esc(og)}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{esc(og)}">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{rel}style.css">
</head>
<body>
{body}
<footer>
<a href="{rel}index.html">Index</a> ·
<a href="{rel}methodology.html">Methodology</a> ·
<a href="{rel}badges.html">Badges</a> ·
<a href="{rel}summary.html">Run environment</a> ·
<a href="{rel}data.json">data.json</a> ·
<a href="{SUBMIT_URL}">Get your server tested</a> ·
<a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>
<br>Generated {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())} by the MCP Health Index harness.
Automated smoke tests of public MCP servers — not a security audit.
Not affiliated with Anthropic or the Model Context Protocol project.</footer>
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
    tested_n, pass_n, pass_pr, fail_pr = pass_rate(rows)
    headline = (f"{fail_pr:.0f}% of MCP servers we tested don't pass a basic smoke test"
                if tested_n else "isolated smoke tests, run daily")
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
        trows.append(f'''<tr{' class="stalerow"' if is_stale(r["tested_at"]) else ''}>
<td data-v="{esc(r['name'] or r['slug'])}"><a href="server/{esc(r['slug'])}.html">{esc(r['title'] or r['slug'])}</a>
<div class="sub">{esc(r['name'])}</div></td>
<td data-v="{esc(r['source'] or '')}">{esc(r['source'] or '–')}</td>
<td data-v="{esc(st)}">{status_chip(st)}</td>
<td data-v="{r['tool_count'] if r['tool_count'] is not None else -1}">{r['tool_count'] if r['tool_count'] is not None else '–'}</td>
<td data-v="{r['handshake_s'] if r['handshake_s'] is not None else -1}">{fmt_s(r['handshake_s'])}</td>
<td data-v="{r['call_s'] if r['call_s'] is not None else -1}">{fmt_ms(r['call_s'])}</td>
<td data-v="{esc(r['tested_at'] or '0000')}">{tested_stamp(r)}</td>
<td data-v="{esc(r['license'] or '')}">{esc(r['license'] or '–')}</td>
<td data-v="{esc(r['pushed_at'] or '')}">{fmt_dt(r['pushed_at'])}</td>
<td data-v="{r['stars'] or 0}">{fmt_int(r['stars'])}</td>
</tr>''')
    body = f'''
<header class="hero">
<h1>MCP Health Index</h1>
<p class="tagline">We actually <em>ran</em> the MCP servers. Every tested entry was installed and
smoke-tested live as an isolated stdio MCP client: install → handshake → list tools → one
read-only tool call. Not another curated list — a live health check, re-run daily.</p>
<p class="headline">{esc(headline)}</p>
<p>{''.join(chips)} <span class="chip">tested {tested_n}/{s['total']}</span></p>
<p class="subnote">v0 scope: stdio servers from the
<a href="https://official.modelcontextprotocol.xyz">official MCP registry</a> plus verified
npm packages. Full method, guarantees and limits: <a href="methodology.html">methodology</a>.</p>
<div class="cta-box">
<strong>Get your server tested</strong> — open a pre-filled GitHub issue; the daily harness
cron ingests it and tests your server in the next batch. Free, automatic, no signup.
<a class="btn" href="{SUBMIT_URL}">Open the submission form →</a>
</div>
</header>
<main>
<table id="tbl">
<thead><tr>
<th data-sort="str">Server</th><th data-sort="str">Source</th><th data-sort="str">Status</th>
<th data-sort="num">Tools</th><th data-sort="num">Boot (handshake)</th><th data-sort="num">Call</th>
<th data-sort="str">Tested</th><th data-sort="str">License</th><th data-sort="str">Last push</th><th data-sort="num">★</th>
</tr></thead>
<tbody>
{''.join(trows)}
</tbody></table>
<p class="subnote">Common failure stages: {fails_html or '–'}.
Dates older than {STALE_DAYS} days are marked <span class="stale">stale</span>.</p>
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
    html_doc = page_shell(
        "MCP Health Index — live smoke-tested MCP servers",
        f"{headline} — {tested_n} MCP servers smoke-tested live in isolation: install, "
        "handshake, tool list, one harmless call. Re-run daily.", body, base)
    (out / "index.html").write_text(html_doc, encoding="utf-8")


def methodology_page(rows, con, out: Path, base: str) -> None:
    s = stats(rows)
    tested_n, pass_n, pass_pr, fail_pr = pass_rate(rows)
    net_row = con.execute("SELECT value FROM meta WHERE key='net_isolation'").fetchone()
    net_state = net_row["value"] if net_row else "unknown"
    meta_rows = con.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
    meta_html = "".join(f"<tr><td><code>{esc(m['key'])}</code></td><td>{esc(m['value'])}</td></tr>"
                        for m in meta_rows)
    body = f'''
<header class="hero"><h1>Methodology</h1>
<p class="tagline">Credibility is the product. This page is the full, honest description of
what a test is, what it proves, what it does not prove, and where the sandbox stops.</p></header>
<main>
<div class="statbar">
<span class="big">{tested_n}</span><label>servers smoke-tested live</label>
<span class="big ok">{pass_pr:.0f}%</span><label>full PASS rate</label>
<span class="big bad">{fail_pr:.0f}%</span><label>don't fully pass</label>
<span class="big">{s['total']}</span><label>candidates queued</label>
</div>

<h2>What a run is — exactly</h2>
<ol>
<li><strong>Ephemeral install</strong> — npm: fresh <code>npm install --prefix $TMP</code>;
PyPI: fresh <code>uv venv $TMP/venv</code> + <code>uv pip install</code>. Nothing is ever
installed globally; the temp directory is deleted after the run. Install (network allowed)
is a separate step from run.</li>
<li><strong>Handshake</strong> — we act as an MCP client over stdio with the official
<code>mcp</code> SDK and complete the protocol <code>initialize</code>. Latency is recorded.</li>
<li><strong>Capability discovery</strong> — <code>tools/list</code> and
<code>resources/list</code>, with latency and tool count.</li>
<li><strong>One harmless call</strong> — we pick a tool whose <em>name</em> matches read-only
verbs (list/get/read/search/describe/…) and never matches destructive verbs
(delete/create/send/exec/…). Arguments are built <em>only</em> from the tool's
<em>required</em> schema fields, with inert values (enum's first value, the declared
default, or a literal like <code>"test"</code>/1/false; path-like fields get the empty
throwaway working directory). Returned content is truncated. If no tool passes the
name filter, the call is skipped and the server is PARTIAL — we never fire a blind call.</li>
</ol>

<h2>What PASS means</h2>
<p>The package, installed from its declared registry on the test date, spoke MCP over
stdio well enough to: boot under our resource limits, complete the initialize handshake,
list its tools, and answer one read-only call without an error result.</p>

<h2>What PASS does <strong>not</strong> prove</h2>
<ul>
<li><strong>Not a security audit.</strong> We do not read or review server code. "PASS"
means it boots and responds — nothing about the code's intent or quality.</li>
<li><strong>Not protocol correctness.</strong> One round-trip is not a conformance suite.</li>
<li><strong>Not a guarantee for your configuration.</strong> Servers with required API keys
are tested with an inert placeholder (<code>mcp-health-index-placeholder</code>) — many
credential-dependent servers therefore show FAIL/PARTIAL by design.</li>
<li><strong>Not permanent.</strong> Results are stamped with the test date. Anything older
than {STALE_DAYS} days is marked stale on the site until the daily run refreshes it.</li>
</ul>

<h2>Isolation that exists</h2>
<ul>
<li>Resource limits on every server process: 60 CPU-seconds, 2 GiB address space,
2048-task cap (fork-bomb protection), 64 MiB file writes, 1024 fds, no core dumps.</li>
<li>A sanitized environment: no inherited tokens or secrets, throwaway
<code>HOME</code>/<code>TMPDIR</code>, ephemeral installs in a temp dir that is deleted.</li>
<li>Hard wall clock: every candidate runs in its own OS session/process group; a SIGALRM
watchdog and the orchestrator both end with <code>SIGKILL</code> to the entire group —
a hung or fork-bombing server cannot outlive its budget.</li>
<li>Network namespace isolation: <strong>{esc(net_state)}</strong></li>
</ul>

<h2>What isolation does NOT protect against</h2>
<ul>
<li><strong>Network egress{'' if 'unavailable' not in net_state else ''}.</strong>
{('On this host unprivileged namespaces are blocked, so server processes ran with normal network access. ' if 'unavailable' in net_state else '')}
Mitigations that reduce (not eliminate) exposure: the environment contains no secrets
and nothing valuable is on disk. See the README SAFETY section for the full story.</li>
<li><strong>The sandbox is not a security boundary.</strong> rlimits bound resources, not
capabilities. A truly malicious package inside those limits can still make outbound
network calls. That is why runs happen on throwaway machines (CI runners), not
workstations.</li>
<li><strong>Tool-call selection is heuristic.</strong> Name-based read-only detection can
mislabel a tool; inputs are inert, but a read-only call can still trigger an external
API call with our placeholder credentials.</li>
</ul>

<h2>Cadence</h2>
<p>The harness runs <strong>daily</strong> (GitHub Actions cron or local cron). Each run
tests a batch of not-yet-tested candidates from the queue ({s['untested']} currently
waiting) and re-tests priority items. <a href="summary.html">Exact run environment and
history →</a></p>

<h2>Get your server tested</h2>
<div class="cta-box">
Open a pre-filled GitHub issue with your package name; the daily workflow ingests open
submission issues automatically, adds them to the queue, and tests them in the next
batch — results appear on this site within ~24h. No signup, no cost.
<a class="btn" href="{SUBMIT_URL}">Open the submission form →</a>
</div>

<h2>FAQ</h2>
<details><summary>Is this affiliated with Anthropic or the MCP project?</summary>
<p>No. The MCP Health Index is an independent, community project. It uses the public
MCP registry API and the open-source <code>mcp</code> SDK. Not affiliated with
Anthropic, and not part of the Model Context Protocol project.</p></details>
<details><summary>How do you test without running malicious code?</summary>
<p>We <em>do</em> run third-party code — on purpose, inside an isolation layer
(ephemeral installs, resource limits, no secrets, hard kill, own process group) on a
throwaway machine. See the "Isolation that exists" and "does NOT protect against"
sections above, plus the SAFETY section in the repo's
<a href="https://github.com/{GITHUB_REPO}#readme">README</a>. There is no perfect
sandbox here; the honest answer is "limits + throwaway environment", not "safety".</p></details>
<details><summary>Why did my server fail?</summary>
<p>Every server's page shows the exact captured error and the server's stderr tail from
the run — see the index link from the announcement or search the table. The most common
causes: (1) the package requires API keys at boot, (2) the package is a CLI tool rather
than a stdio MCP server (prints usage and exits), (3) declared bin/entrypoint doesn't
resolve, (4) it needs services (databases, daemons, browsers) we don't provide.</p></details>
<details><summary>How do I get a re-test?</summary>
<p><a href="{SUBMIT_URL}">Open the submission form</a> with your package name — open
issues are ingested by the daily cron and re-queued automatically. For urgent re-tests,
email <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p></details>
</main>'''
    html_doc = page_shell(
        "Methodology — MCP Health Index",
        f"How {tested_n} MCP servers are smoke-tested: ephemeral install, isolated handshake, "
        "tool listing and one harmless call — plus what PASS does and does not prove.",
        body, base)
    (out / "methodology.html").write_text(html_doc, encoding="utf-8")


def badges_page(rows, out: Path, base: str) -> None:
    tested = [r for r in rows if r["tested_at"]]
    top = sorted(tested, key=lambda r: (-(r["stars"] or 0), r["name"]))[:30]
    gallery = []
    for r in top:
        md = badges.badge_markdown(base, r["slug"])
        gallery.append(f'''<div class="badge-card">
<img src="badges/{esc(r['slug'])}.svg" alt="{esc(r['title'])} badge" loading="lazy">
<div class="sub">{esc(r['title'] or r['slug'])} · {STATUS_LABEL.get(r['status'] or 'untested', 'UNTESTED')}</div>
<pre class="call">{esc(md)}</pre>
<a href="server/{esc(r['slug'])}.html">report →</a>
</div>''')
    body = f'''
<header class="hero"><h1>Badges</h1>
<p class="tagline">Every tested server gets an embeddable SVG badge — the live result of its
smoke test, regenerated on each daily run. Put it in your README to show your server
boots and answers.</p></header>
<main>
<h2>How to embed</h2>
<p>Copy the markdown below into your README (replace the URL with your server's badge):</p>
<pre class="call">[![MCP Health: PASS](&lt;{base}/badges/YOUR-SLUG.svg&gt;)](&lt;{base}/server/YOUR-SLUG.html&gt;)</pre>
<p>Or plain HTML:</p>
<pre class="call">&lt;a href="{base}/server/YOUR-SLUG.html"&gt;&lt;img src="{base}/badges/YOUR-SLUG.svg" alt="MCP Health" width="208" height="20"&gt;&lt;/a&gt;</pre>
<p class="subnote">Badges are static SVG files regenerated on each daily run — no JS, no
tracking, no requests to third parties. Every badge links to the server's full test report.</p>
<h2>Badge gallery — most-starred tested servers</h2>
<div class="badge-grid">{''.join(gallery)}</div>
<p class="subnote">Looking for your server? Find it in the <a href="index.html">index</a> —
its page has the exact snippet. Want a badge for a server we haven't tested?
<a href="{SUBMIT_URL}">Submit it</a>.</p>
</main>'''
    html_doc = page_shell("Badges — MCP Health Index",
                          "Embeddable SVG badges for smoke-tested MCP servers, regenerated daily.",
                          body, base)
    (out / "badges.html").write_text(html_doc, encoding="utf-8")


def write_robots_sitemap(rows, out: Path, base: str) -> None:
    b = base.rstrip("/")
    (out / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n"
        f"Sitemap: {b}/sitemap.xml\n", encoding="utf-8")
    urls = [("/index.html", None), ("/methodology.html", None), ("/summary.html", None),
            ("/badges.html", None), ("/data.json", None)]
    today = time.strftime("%Y-%m-%d")
    entries = []
    for path, _ in urls:
        entries.append(f"  <url><loc>{b}{path}</loc><lastmod>{today}</lastmod></url>")
    for r in rows:
        lastmod = (r["tested_at"] or today)[:10]
        entries.append(f"  <url><loc>{b}/server/{esc(r['slug'])}.html</loc><lastmod>{lastmod}</lastmod></url>")
    sm = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.w3.org/9/9/sitemap.xml">\n' + "\n".join(entries) + "\n</urlset>\n")
    sm = sm.replace("http://www.w3.org/9/9/sitemap.xml", "http://www.sitemaps.org/schemas/sitemap/0.9")
    (out / "sitemap.xml").write_text(sm, encoding="utf-8")


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
    try:
        spec_data = json.loads(r["spec_json"] or "{}")
    except Exception:
        spec_data = {}
    phases = detail.get("phases") or {}
    lt = phases.get("list_tools") or {}
    tools = lt.get("tools") or []
    cl = phases.get("call") or {}
    tool_rows = "".join(
        f"<tr><td><code>{esc(t['name'])}</code></td><td>{esc(t.get('description') or '')}</td>"
        f"<td><code>{esc(', '.join(t.get('required_args') or []))}</code></td></tr>"
        for t in tools[:40])
    md = badges.badge_markdown(base, slug)
    html_snippet = (f'<a href="{abs_url(f"server/{slug}.html", base)}">'
                    f'<img src="{abs_url(f"badges/{slug}.svg", base)}" alt="MCP Health" '
                    f'width="208" height="20"></a>')
    badge_rel = f"../badges/{slug}.svg"
    origin_text = esc(r['origin'] or '')
    if r["origin"] == "submit" and spec_data.get("issue_number"):
        origin_text = f'GitHub issue <a href="https://github.com/{GITHUB_REPO}/issues/{spec_data["issue_number"]}">#{spec_data["issue_number"]}</a>'
    stale_note = (' <span class="stale">stale — will be refreshed by the next daily run</span>'
                  if is_stale(r["tested_at"]) else "")
    tested_line = (f"last tested <strong>{fmt_dt(r['tested_at'])}</strong>"
                   f"{f' · full run took {fmt_s(r['wall_s'])}' if r['wall_s'] is not None else ''}"
                   f"{stale_note}") if r["tested_at"] else "not yet tested (queued)"
    spec_info = f'''
<h2>Spec</h2>
<table><tbody>
<tr><td>Registry name</td><td>{esc(r['name'])}</td></tr>
<tr><td>Source</td><td>{esc(r['source'])} — <code>{esc(r['identifier'] or '')}{('@' + str(r['version'])) if r['version'] else ''}</code> ({origin_text})</td></tr>
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
        if ph is None and name != "call":
            continue
        if ph is None:
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
&nbsp; stage: <code>{esc(r['stage'] or '–')}</code> · {tested_line}</p>
<div class="embed-box">
<strong>Embed this badge</strong> in your README — it updates with every daily re-test:
<pre class="call">{esc(md)}</pre>
<p class="subnote">HTML: <code>{esc(html_snippet)}</code> · raw SVG: <a href="{abs_url('badges/' + slug + '.svg', base)}">{abs_url('badges/' + slug + '.svg', base)}</a> · <a href="../badges.html">badge instructions</a></p>
</div></header>
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
<p>Markdown (copy-paste into your README):</p>
<pre class="call">{esc(md)}</pre>
<p class="subnote">See <a href="../badges.html">badges</a> for HTML snippets and the gallery.</p>
<h2>Dispute or fix a result</h2>
<p>Wrong package metadata, a flaky run, or your server needs credentials to boot?
<a href="{SUBMIT_URL}">Open a submission issue</a> and it will be re-tested by the daily
run, or email <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
<p><a href="../index.html">← back to the index</a></p>
</main>'''
    html_doc = page_shell(f"MCP Health Index — {r['title'] or slug}",
                          f"Live smoke-test report for MCP server {r['name']}: "
                          f"{STATUS_LABEL.get(r['status'] or 'untested', 'UNTESTED')}"
                          f"{', ' + str(r['tool_count']) + ' tools' if r['tool_count'] is not None else ''}.",
                          body, base, sub=True,
                          og_image_rel=f"og/{slug}.png")
    (out / "server").mkdir(parents=True, exist_ok=True)
    (out / "server" / f"{slug}.html").write_text(html_doc, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/results.db")
    ap.add_argument("--out", default="site")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help="public site base URL for badge markdown")
    ap.add_argument("--skip-og", action="store_true", help="skip OG image generation")
    args = ap.parse_args()
    con = db.connect(Path(args.db))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = load(con)
    write_badges(rows, out, args.base_url)
    data_json(rows, con, out, args.base_url)
    index_page(rows, con, out, args.base_url)
    methodology_page(rows, con, out, args.base_url)
    badges_page(rows, out, args.base_url)
    summary_page(rows, con, out, args.base_url)
    write_robots_sitemap(rows, out, args.base_url)
    n_og = 0
    if not args.skip_og:
        try:
            import ogimg
            if ogimg.available():
                tested_n, pass_n, pass_pr, fail_pr = pass_rate(rows)
                s = stats(rows)
                headline = (f"{fail_pr:.0f}% of MCP servers we tested don't pass a basic smoke test"
                            if tested_n else "isolated smoke tests, run daily")
                if ogimg.index_image(out / "og" / "index.png", headline, s, args.base_url):
                    n_og += 1
                for r in rows:
                    if ogimg.server_image(out / "og" / f"{r['slug']}.png",
                                          r["slug"], r["title"] or r["slug"],
                                          r["status"] or "untested", r["tool_count"],
                                          r["call_s"], r["tested_at"], args.base_url):
                        n_og += 1
        except Exception as e:  # noqa: BLE001 — OG images are optional polish
            print(f"og images skipped: {e}")
    for r in rows:
        detail_page(r, out, args.base_url)
    (out / "style.css").write_text(CSS, encoding="utf-8")
    print(f"site: wrote {len(rows)} server pages, badges, index, methodology, badges page, "
          f"{n_og} og images → {out}/")
    con.close()
    return 0


CSS = ''':root{--bg:#0d1117;--fg:#c9d1d9;--muted:#8b949e;--link:#58a6ff;--card:#161b22;--border:#30363d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
main{max-width:1100px;margin:0 auto;padding:0 20px 40px}
header.hero{max-width:1100px;margin:0 auto;padding:34px 20px 10px}
h1{font-size:1.9rem;margin:.2em 0}h2{margin-top:1.6em;font-size:1.15rem}
.tagline{color:var(--fg);max-width:52rem}.subnote{color:var(--muted);font-size:.9rem}
.headline{font-size:1.35rem;font-weight:700;max-width:48rem;margin:.4em 0}
.stale{color:#d29922;font-weight:600}.fresh{color:var(--muted)}
.mute{color:var(--muted)}tr.stalerow td{opacity:.62}
.chip{display:inline-block;padding:2px 10px;border-radius:99px;background:var(--card);border:1px solid var(--border);font-size:.8rem;margin-right:4px}
.chip.ok{color:#7ee787;border-color:#2ea043}.chip.warn{color:#e3b341;border-color:#d29922}
.chip.bad{color:#ff7b72;border-color:#cf222e}.chip.mute{color:var(--muted)}
.cta-box,.embed-box{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin:16px 0;max-width:52rem}
.embed-box pre.call{margin:10px 0 4px}
.btn{display:inline-block;margin-top:8px;padding:6px 14px;border-radius:8px;border:1px solid var(--link);color:var(--link);font-weight:600}
.btn:hover{background:#1c2a3a;text-decoration:none}
.statbar{display:flex;flex-wrap:wrap;gap:26px;margin:18px 0 8px}
.statbar .big{font-size:2rem;font-weight:800;display:block}
.statbar .big.ok{color:#7ee787}.statbar .big.bad{color:#ff7b72}
.statbar label{color:var(--muted);font-size:.8rem}
details{border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin:10px 0;background:var(--card)}
details summary{cursor:pointer;font-weight:600}
.badge-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
.badge-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px}
.badge-card pre.call{font-size:.72rem;max-height:5rem;overflow:auto}
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
