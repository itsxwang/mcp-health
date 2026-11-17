"""Static site generator: pure HTML/CSS + one small vanilla JS file (theme,
scroll-reveal, donut sweep, stat count-up, table sort). No frameworks, no build
step, deployable to GitHub Pages as-is.

Design language (shared spec, design-language.md v2): self-hosted variable fonts
(Space Grotesk / Inter / JetBrains Mono, vendored woff2), #0b1220 token family,
dark+light themes, one animated SVG donut on the hero, staggered scroll-reveal,
reduced-motion kills everything, at most ONE gradient accent per viewport.

Output:
  site/index.html        sortable index table of every candidate
  site/server/<slug>.html  one detail page per server
  site/methodology.html  what a test is, what PASS means, limits, FAQ
  site/badges.html       embed instructions + badge gallery
  site/summary.html      how tests are run, environment, failure-mode breakdown
  site/badges/<slug>.svg   embeddable badges
  site/fonts/*.woff2     vendored variable fonts (copied from assets/fonts/)
  site/site.js           theme toggle + reveal + donut + count-up + sort
  site/data.json         full machine-readable results (shape unchanged)
  site/style.css
"""
from __future__ import annotations

import argparse
import html
import json
import math
import platform
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import badges, db  # noqa: E402

FONTS_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_FILES = ("inter-var.woff2", "jetbrainsmono-var.woff2", "spacegrotesk-var.woff2")

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

# Heuristic: which captured output lines get the red "error" treatment in the
# terminal panel. Presentation only — the text itself is always verbatim.
_ERR_RX = re.compile(
    r"error|Traceback|Exception|ECONNREFUSED|ENOENT|EACCES|exit code|cannot find|"
    r"not found|failed|refused|Missing|Invalid|undefined|denied|panic", re.IGNORECASE)


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
    dur = f'<span class="dur"> · {fmt_s(r["wall_s"])} run</span>' if r["wall_s"] is not None else ""
    return f'<span class="{cls}">{fmt_dt(r["tested_at"])}</span>{dur}'


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


DONUT_C = 360.0  # circles use pathLength="360" so dash math is percentage-like


def donut_svg(s: dict) -> str:
    """Hero donut: live pass/partial/fail split, recomputed from data at build.

    No-JS = static final state (correct). With JS the arcs sweep in once via
    stroke-dashoffset when scrolled into view; prefers-reduced-motion skips it.
    """
    tested = s["tested"]
    if not tested:
        return ""
    segs = []
    start = 0.0
    idx = 0
    for key, cls in (("pass", "seg-ok"), ("partial", "seg-warn"), ("fail", "seg-bad")):
        n = s[key]
        if not n:
            continue
        length = DONUT_C * n / tested
        off_final = -(start)               # dash begins at `start`
        off_initial = -(start) + length    # slid one segment counter-clockwise
        segs.append(
            f'<circle class="seg {cls}" cx="60" cy="60" r="50" pathLength="360"'
            f' stroke-dasharray="{length - 0.6:.2f} {DONUT_C - length + 0.6:.2f}"'
            f' style="--i:{idx};--s0:{off_initial:.2f};--s1:{off_final:.2f}" aria-hidden="true"></circle>')
        start += length
        idx += 1
    pass_pr = 100.0 * s["pass"] / tested
    return f'''<div class="donut-wrap" aria-label="Pass rate: {pass_pr:.0f}% of {tested} tested servers pass">
<svg class="donut" width="212" height="212" viewBox="0 0 120 120" role="img">
<circle class="track" cx="60" cy="60" r="50" pathLength="360"></circle>
{''.join(segs)}
<text class="donut-num" x="60" y="58" text-anchor="middle" data-count="{pass_pr:.0f}" data-suffix="%">{pass_pr:.0f}%</text>
<text class="donut-lab" x="60" y="76" text-anchor="middle">pass rate</text>
</svg>
<div class="donut-legend">
<span><i class="lg lg-ok"></i>pass <b class="num">{s['pass']}</b></span>
<span><i class="lg lg-warn"></i>partial <b class="num">{s['partial']}</b></span>
<span><i class="lg lg-bad"></i>fail <b class="num">{s['fail']}</b></span>
</div>
</div>'''


def trust_strip() -> str:
    ic = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>')
    items = [
        (ic.format('<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>'), "Re-run daily"),
        (ic.format('<path d="M12 22s8-3.6 8-10V5l-8-3-8 3v7c0 6.4 8 10 8 10z"/>'), "Isolated sandbox"),
        (ic.format('<path d="M4 17l6-5-6-5M12 19h8"/>'), "Exact errors shown"),
        (ic.format('<circle cx="12" cy="12" r="9"/><path d="M5.5 5.5l13 13"/>'), "Not affiliated with Anthropic"),
    ]
    return ('<ul class="trust">' +
            "".join(f'<li>{i}<span>{t}</span></li>' for i, t in items) + '</ul>')


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
<meta name="color-scheme" content="dark light">
<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#0b1220">
<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f6f8fd">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{esc(og)}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{esc(og)}">
<title>{esc(title)}</title>
<link rel="preload" href="{rel}fonts/spacegrotesk-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{rel}fonts/inter-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{rel}style.css">
<script>(function(){{var t=null;try{{t=localStorage.getItem("mcphi-theme")}}catch(e){{}}
if(!t)t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";
document.documentElement.dataset.theme=t;}})();</script>
</head>
<body>
<div class="topbar"><div class="wrap">
<a class="brand" href="{rel}index.html"><span class="brand-dot" aria-hidden="true"></span>MCP Health Index</a>
<button id="theme-toggle" type="button" aria-label="Switch color theme" title="Switch theme">
<svg class="ic-sun" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
<svg class="ic-moon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>
</button>
</div></div>
{body}
<footer><div class="wrap">
<nav class="foot-nav">
<a href="{rel}index.html">Index</a>
<a href="{rel}methodology.html">Methodology</a>
<a href="{rel}badges.html">Badges</a>
<a href="{rel}summary.html">Run environment</a>
<a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>
</nav>
<p>Re-run daily by an automated harness · automated smoke tests, not a security audit ·
not affiliated with Anthropic or the Model Context Protocol project ·
machine-readable results: <a href="{rel}data.json">data.json</a> ·
generated {time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}</p>
</div></footer>
<script src="{rel}site.js" defer></script>
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
    stats_chips = f'''
<div class="hero-stats">
<div class="stat"><span class="num" data-count="{tested_n}">{tested_n}</span><label>servers tested</label></div>
<div class="stat ok"><span class="num" data-count="{s['pass']}">{s['pass']}</span><label>full pass</label></div>
<div class="stat bad"><span class="num" data-count="{s['fail'] + s['partial']}">{s['fail'] + s['partial']}</span><label>didn't pass</label></div>
</div>'''
    trows = []
    for i, r in enumerate(rows):
        st = r["status"] or "untested"
        trows.append(f'''<tr data-reveal{' class="stalerow"' if is_stale(r["tested_at"]) else ''}>
<td data-v="{esc(r['name'] or r['slug'])}"><a href="server/{esc(r['slug'])}.html">{esc(r['title'] or r['slug'])}</a>
<div class="sub">{esc(r['name'])}</div></td>
<td data-v="{esc(r['source'] or '')}">{esc(r['source'] or '–')}</td>
<td data-v="{esc(st)}">{status_chip(st)}</td>
<td class="num" data-v="{r['tool_count'] if r['tool_count'] is not None else -1}">{r['tool_count'] if r['tool_count'] is not None else '–'}</td>
<td class="num" data-v="{r['handshake_s'] if r['handshake_s'] is not None else -1}">{fmt_s(r['handshake_s'])}</td>
<td class="num" data-v="{r['call_s'] if r['call_s'] is not None else -1}">{fmt_ms(r['call_s'])}</td>
<td class="num" data-v="{esc(r['tested_at'] or '0000')}">{tested_stamp(r)}</td>
<td data-v="{esc(r['license'] or '')}">{esc(r['license'] or '–')}</td>
<td class="num" data-v="{esc(r['pushed_at'] or '')}">{fmt_dt(r['pushed_at'])}</td>
<td class="num" data-v="{r['stars'] or 0}">{fmt_int(r['stars'])}</td>
</tr>''')
    body = f'''
<header class="hero"><div class="wrap">
<div class="hero-grid">
<div class="hero-copy">
<p class="eyebrow">automated, isolated smoke tests — not a curated list</p>
<h1>MCP Health Index</h1>
<p class="headline"><span data-count="{fail_pr:.0f}" data-suffix="%">{fail_pr:.0f}%</span> of MCP servers we tested
don't pass a basic smoke test</p>
<p class="tagline">We actually <em>ran</em> them. Every tested entry was installed in a
throwaway sandbox and driven as a real stdio MCP client: install → handshake → list tools
→ one read-only call. Results are recomputed from live runs and re-checked daily —
see <a href="methodology.html">exactly how</a>.</p>
{stats_chips}
</div>
{donut_svg(s)}
</div>
{trust_strip()}
<div class="cta-row">
<a class="btn btn-primary" href="{SUBMIT_URL}">Get your server tested — free</a>
<a class="btn btn-ghost" href="methodology.html">Read the methodology</a>
</div>
</div></header>
<main class="wrap">
<div class="card table-card">
<div class="table-scroll">
<table id="tbl">
<thead><tr>
<th data-sort="str">Server</th><th data-sort="str">Source</th><th data-sort="str">Status</th>
<th class="num" data-sort="num">Tools</th><th class="num" data-sort="num">Boot</th><th class="num" data-sort="num">Call</th>
<th class="num" data-sort="str">Tested</th><th data-sort="str">License</th><th class="num" data-sort="str">Last push</th><th class="num" data-sort="num">★</th>
</tr></thead>
<tbody>
{''.join(trows)}
</tbody></table>
</div>
</div>
<p class="subnote">Numbers on this page are computed at build time from the live result
database — never hand-edited. Common failure stages: {fails_html or '–'}.
Dates older than {STALE_DAYS} days are marked <span class="stale">stale</span>.
Untested servers ({s['untested']}) are queued for the daily batch.</p>
</main>'''
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
<header class="hero"><div class="wrap">
<p class="eyebrow">credibility is the product</p>
<h1>Methodology</h1>
<p class="tagline">The full, honest description of what a test is, what it proves, what it
does not prove, and where the sandbox stops.</p>
</div></header>
<main class="wrap">
<div class="statgrid" data-reveal>
<div class="card stat"><span class="num big" data-count="{tested_n}">{tested_n}</span><label>servers smoke-tested live</label></div>
<div class="card stat"><span class="num big ok" data-count="{pass_pr:.0f}" data-suffix="%">{pass_pr:.0f}%</span><label>full PASS rate</label></div>
<div class="card stat"><span class="num big bad" data-count="{fail_pr:.0f}" data-suffix="%">{fail_pr:.0f}%</span><label>don't fully pass</label></div>
<div class="card stat"><span class="num big" data-count="{s['total']}">{s['total']}</span><label>candidates in queue</label></div>
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
<div class="card cta-card">
<strong>Open a pre-filled GitHub issue</strong> with your package name; the daily workflow
ingests open submission issues automatically, adds them to the queue, and tests them in the
next batch — results appear on this site within ~24h. No signup, no cost.
<div class="cta-row"><a class="btn btn-primary" href="{SUBMIT_URL}">Open the submission form</a>
<a class="btn btn-ghost" href="summary.html">Exact run environment</a></div>
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
<header class="hero"><div class="wrap">
<p class="eyebrow">show your server's live test result</p>
<h1>Badges</h1>
<p class="tagline">Every tested server gets an embeddable SVG badge — the live result of its
smoke test, regenerated on each daily run. Put it in your README to show your server
boots and answers.</p>
</div></header>
<main class="wrap">
<div class="card" data-reveal>
<h3>How to embed</h3>
<p>Copy the markdown below into your README (replace the URL with your server's badge):</p>
<pre class="call">[![MCP Health: PASS](&lt;{base}/badges/YOUR-SLUG.svg&gt;)](&lt;{base}/server/YOUR-SLUG.html&gt;)</pre>
<p>Or plain HTML:</p>
<pre class="call">&lt;a href="{base}/server/YOUR-SLUG.html"&gt;&lt;img src="{base}/badges/YOUR-SLUG.svg" alt="MCP Health" width="208" height="20"&gt;&lt;/a&gt;</pre>
<p class="subnote">Badges are static SVG files regenerated on each daily run — no JS, no
tracking, no requests to third parties. Every badge links to the server's full test report.</p>
</div>
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
<header class="hero"><div class="wrap">
<p class="eyebrow">the receipts</p>
<h1>Method &amp; environment</h1>
<p class="tagline">How the MCP Health Index smoke-tests servers — and exactly what the isolation
does and does not guarantee.</p>
</div></header>
<main class="wrap">
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

    # The credibility artifact: the exact captured output, verbatim, in a styled
    # terminal panel (dark in both themes, JetBrains Mono, error lines colored).
    def term_panel(title: str, text: str, max_lines: int = 40) -> str:
        lines = [l for l in text.replace("\r", "").split("\n")]
        shown = [l for l in lines if l.strip()][:max_lines]
        hidden = len([l for l in lines if l.strip()]) - len(shown)
        if not shown:
            return ""
        rows = []
        for l in shown:
            cls = ("err" if _ERR_RX.search(l) else "mut") if len(shown) > 1 else "err"
            rows.append(f'<span class="tl {cls}">{esc(l)}</span>')
        more = f'<span class="tl mut">… +{hidden} more lines</span>' if hidden > 0 else ""
        return f'''<div class="term" data-reveal>
<div class="term-bar"><i></i><i></i><i></i><span>{esc(title)} — verbatim, not paraphrased</span></div>
<pre class="term-body">{''.join(rows)}{more}</pre>
</div>'''

    err_parts = []
    if r["error"]:
        err_parts.append(term_panel("captured error", str(r["error"])))
    if detail.get("errors"):
        err_parts.append(term_panel("harness errors", " | ".join(detail["errors"])))
    if r["stderr_tail"]:
        err_parts.append(term_panel("server stderr (tail)", r["stderr_tail"]))
    err_html = "".join(err_parts)

    spec_info = f'''
<div class="card" data-reveal>
<h3>Spec</h3>
<table class="kv"><tbody>
<tr><td>Registry name</td><td>{esc(r['name'])}</td></tr>
<tr><td>Source</td><td>{esc(r['source'])} — <code>{esc(r['identifier'] or '')}{('@' + str(r['version'])) if r['version'] else ''}</code> ({origin_text})</td></tr>
<tr><td>Repo</td><td>{('<a href="' + esc(r['repo_url']) + '">' + esc(r['repo_url']) + '</a>') if r['repo_url'] else '–'} <span class="num">★ {fmt_int(r['stars'])}</span></td></tr>
<tr><td>License</td><td>{esc(r['license'] or 'unknown')}</td></tr>
<tr><td>Last push</td><td><span class="num">{fmt_dt(r['pushed_at'])}</span></td></tr>
<tr><td>Description</td><td>{esc(r['description'] or '')}</td></tr>
</tbody></table>
</div>'''
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
    status = r["status"] or "untested"
    body = f'''
<header class="hero"><div class="wrap">
<p class="eyebrow">smoke-test report</p>
<div class="verdict">
<div class="verdict-chip v-{esc(status)}"><span>{STATUS_LABEL.get(status, 'UNTESTED')}</span></div>
<div class="verdict-meta">
<h1>{esc(r['title'] or slug)}</h1>
<p class="tagline"><span class="mute">stage:</span> <code>{esc(r['stage'] or '–')}</code>
<span class="dotsep">·</span> {tested_line}</p>
</div>
<img class="verdict-badge" src="{badge_rel}" alt="badge" width="208" height="20">
</div>
</div></header>
<main class="wrap">
{err_html if err_html else '<div class="card" data-reveal><p class="subnote">No error output was captured for this run' + (' — the server completed the smoke test cleanly.' if status == 'pass' else '.') + '</p></div>'}
<div class="card" data-reveal>
<h3>Run phases</h3>
<table class="phases"><thead><tr><th>phase</th><th>result</th><th>time</th><th>detail</th></tr></thead>
<tbody>{ph_rows}</tbody></table>
<p>Launch argv: <code>{esc(' '.join(argv))}</code>
{('· required env vars filled with placeholders: ' + esc(', '.join(detail.get('env_placeholders') or []))) if detail.get('env_placeholders') else ''}</p>
</div>
<div class="card" data-reveal>
<h3>Tools <span class="num">({r['tool_count'] if r['tool_count'] is not None else 0})</span></h3>
{('<table class="tools"><thead><tr><th>name</th><th>description</th><th>required args</th></tr></thead><tbody>' + tool_rows + '</tbody></table>') if tool_rows else '<p class="subnote">none listed</p>'}
{('<h4>Call result preview</h4><pre class="call">' + esc((cl.get('preview') or '')[:1200]) + '</pre>') if cl.get('preview') else ''}
</div>
{spec_info}
<div class="card embed-box" data-reveal>
<h3>Embed this badge</h3>
<p>It updates with every daily re-test. Markdown for your README:</p>
<pre class="call">{esc(md)}</pre>
<p class="subnote">HTML: <code>{esc(html_snippet)}</code> · raw SVG: <a href="{abs_url('badges/' + slug + '.svg', base)}">{abs_url('badges/' + slug + '.svg', base)}</a> · <a href="../badges.html">badge instructions</a></p>
</div>
<div class="card" data-reveal>
<h3>Dispute or fix a result</h3>
<p>Wrong package metadata, a flaky run, or your server needs credentials to boot?
<a href="{SUBMIT_URL}">Open a submission issue</a> and it will be re-tested by the daily
run, or email <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
<div class="cta-row"><a class="btn btn-ghost" href="../index.html">← back to the index</a></div>
</div>
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
    # vendored fonts (design-language.md: self-hosted woff2, never hotlinked)
    fdir = out / "fonts"
    fdir.mkdir(parents=True, exist_ok=True)
    for name in FONT_FILES:
        src = FONTS_DIR / name
        if src.is_file():
            (fdir / name).write_bytes(src.read_bytes())
        else:
            print(f"font missing: {src} (site will fall back to system fonts)")
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
    (out / "site.js").write_text(SITE_JS, encoding="utf-8")
    print(f"site: wrote {len(rows)} server pages, badges, index, methodology, badges page, "
          f"{n_og} og images, fonts, site.js → {out}/")
    con.close()
    return 0


SITE_JS = ''';(function(){
"use strict";
var d=document, root=d.documentElement;
var reduced=false;
try{ reduced=window.matchMedia("(prefers-reduced-motion: reduce)").matches; }catch(e){}
root.classList.add("js");

/* theme toggle: 250ms color transition, persisted, respects prefers-color-scheme */
var btn=d.getElementById("theme-toggle");
if(btn){ btn.addEventListener("click",function(){
  var cur=root.getAttribute("data-theme")==="light"?"dark":"light";
  root.classList.add("theming");
  root.setAttribute("data-theme",cur);
  try{ localStorage.setItem("mcphi-theme",cur); }catch(e){}
  setTimeout(function(){ root.classList.remove("theming"); },300);
});}

function onView(el,fn){
  if(reduced||!("IntersectionObserver" in window)){ fn(); return; }
  var io=new IntersectionObserver(function(es){
    if(es[0].isIntersecting){ io.disconnect(); fn(); }
  },{threshold:.35});
  io.observe(el);
}

/* hero donut: sweep stroke-dashoffset once, then rest (CSS drives the values) */
var donut=d.querySelector(".donut");
if(donut){ onView(donut,function(){ donut.classList.add("live"); });
  setTimeout(function(){ donut.classList.add("live"); },4000); /* safety net */ }

/* scroll reveal: fade+rise 12px, 500ms, staggered 60ms per batch */
var rev=[].slice.call(d.querySelectorAll("[data-reveal]"));
if("IntersectionObserver" in window && !reduced){
  var io2=new IntersectionObserver(function(es){
    var n=0;
    es.forEach(function(en){
      if(en.isIntersecting){
        en.target.style.transitionDelay=(n++*60)+"ms";
        en.target.classList.add("in");
        io2.unobserve(en.target);
      }
    });
  },{rootMargin:"0px 0px -6% 0px",threshold:0});
  rev.forEach(function(el){ io2.observe(el); });
}else{ rev.forEach(function(el){ el.classList.add("in"); }); }

/* stat counters: count up once in view (final value already in the HTML) */
[].slice.call(d.querySelectorAll("[data-count]")).forEach(function(el){
  onView(el,function(){
    var end=parseFloat(el.getAttribute("data-count")), suf=el.getAttribute("data-suffix")||"";
    if(reduced||isNaN(end)){ return; }
    var t0=null, dur=900;
    function step(ts){
      if(t0===null){ t0=ts; }
      var p=Math.min((ts-t0)/dur,1);
      el.textContent=Math.round(end*(1-Math.pow(1-p,3))).toLocaleString("en-US")+suf;
      if(p<1){ requestAnimationFrame(step); }
    }
    requestAnimationFrame(step);
  });
});

/* table sort (mono columns carry data-v for stable numeric compare) */
[].slice.call(d.querySelectorAll("#tbl th[data-sort]")).forEach(function(th){
  th.addEventListener("click",function(){
    var tb=document.querySelector("#tbl tbody"), rows=[].slice.call(tb.rows), i=th.cellIndex,
        num=th.getAttribute("data-sort")==="num";
    th.asc=!th.asc;
    [].slice.call(th.parentNode.children).forEach(function(h){ h.classList.remove("asc","desc"); });
    th.classList.add(th.asc?"asc":"desc");
    rows.sort(function(a,b){
      var A=a.cells[i].getAttribute("data-v"), B=b.cells[i].getAttribute("data-v");
      if(num){ A=parseFloat(A); B=parseFloat(B); return (A>B)-(A<B); }
      return String(A).localeCompare(String(B));
    });
    if(!th.asc){ rows.reverse(); }
    rows.forEach(function(r){ tb.appendChild(r); });
  });
});
})();'''

CSS = '''/* MCP Health Index — design-language.md v2 (shared company spec)
   fonts: self-hosted variable woff2, font-display:swap, latin subset */
@font-face{font-family:"Inter";font-style:normal;font-weight:100 900;font-display:swap;src:url("fonts/inter-var.woff2") format("woff2")}
@font-face{font-family:"JetBrains Mono";font-style:normal;font-weight:400 800;font-display:swap;src:url("fonts/jetbrainsmono-var.woff2") format("woff2")}
@font-face{font-family:"Space Grotesk";font-style:normal;font-weight:300 700;font-display:swap;src:url("fonts/spacegrotesk-var.woff2") format("woff2")}

:root{
  --bg:#0b1220; --card:#101a2e; --card2:#0d1526; --border:#1f2c47; --border2:#31456d;
  --ink:#e2e8f0; --muted:#9fb0d0; --accent:#8ab4ff; --accent2:#7ee0c0;
  --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
  --term:#081020; --term-ink:#cdd9f0; --term-mut:#8fa3c8; --term-border:#1c2a4a;
  --font-head:"Space Grotesk",ui-sans-serif,system-ui,sans-serif;
  --font-body:"Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --font-mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
[data-theme="light"]{
  --bg:#f6f8fd; --card:#ffffff; --card2:#eef2fa; --border:#dbe3f2; --border2:#b9c9e6;
  --ink:#131c33; --muted:#57678c; --accent:#2b63d9; --accent2:#0e9f8a;
  --ok:#047857; --warn:#a16207; --bad:#dc2626;
  --term:#0b1220; --term-ink:#cdd9f0; --term-mut:#8fa3c8; --term-border:#1c2a4a;
}
@media (prefers-color-scheme: light){ :root:not([data-theme]){
  --bg:#f6f8fd; --card:#ffffff; --card2:#eef2fa; --border:#dbe3f2; --border2:#b9c9e6;
  --ink:#131c33; --muted:#57678c; --accent:#2b63d9; --accent2:#0e9f8a;
  --ok:#047857; --warn:#a16207; --bad:#dc2626;
}}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:400 16px/1.6 var(--font-body);-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
::selection{background:rgba(138,180,255,.28)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
h1,h2,h3{font-family:var(--font-head);letter-spacing:-.02em;margin:0}
h1{font-size:44px;font-weight:700;line-height:1.08}
h2{font-size:24px;font-weight:700;line-height:1.2;margin:40px 0 14px}
h3{font-size:20px;font-weight:600;line-height:1.25;margin:0 0 10px}
h4{font-family:var(--font-head);font-size:16px;font-weight:600;margin:18px 0 8px}
p{margin:.5em 0}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px}
main.wrap{padding-top:34px;padding-bottom:56px}
.num,code,pre,.chip,.donut-num{font-family:var(--font-mono)}

/* top bar */
.topbar{border-bottom:1px solid var(--border);background:var(--bg)}
.topbar .wrap{display:flex;align-items:center;justify-content:space-between;height:54px}
.brand{display:flex;align-items:center;gap:9px;color:var(--ink);font:600 15px var(--font-head);letter-spacing:-.01em}
.brand:hover{text-decoration:none}
.brand-dot{width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px rgba(138,180,255,.15)}
#theme-toggle{display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:9px;border:1px solid var(--border);background:var(--card);color:var(--muted);cursor:pointer;transition:border-color .15s ease,color .15s ease,transform .15s ease}
#theme-toggle:hover{border-color:var(--border2);color:var(--ink);transform:translateY(-1px)}
[data-theme="light"] .ic-sun{display:none}
.ic-moon{display:none}
[data-theme="light"] .ic-moon{display:block}

/* hero */
.hero{padding:52px 0 44px}
.eyebrow{font:600 13px var(--font-body);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);margin:0 0 10px}
.headline{font:700 32px/1.15 var(--font-head);letter-spacing:-.02em;margin:14px 0 12px;max-width:34ch}
.tagline{color:var(--muted);max-width:56ch;margin:8px 0 0}
.tagline em{color:var(--ink);font-style:normal;font-weight:600}
.hero-grid{display:grid;grid-template-columns:minmax(0,1fr) 264px;gap:44px;align-items:center}
.hero-stats{display:flex;gap:30px;margin:28px 0 0;flex-wrap:wrap}
.stat .num{font-size:32px;font-weight:600;line-height:1;letter-spacing:-.02em}
.stat.ok .num{color:var(--ok)}
.stat.bad .num{color:var(--bad)}
.stat label{display:block;font-size:13px;color:var(--muted);margin-top:7px}

/* donut */
.donut-wrap{justify-self:center;text-align:center}
.donut{display:block}
.donut .track{fill:none;stroke:var(--border);stroke-width:13}
.donut .seg{fill:none;stroke-width:13;stroke-dashoffset:var(--s1,0);transform:rotate(-90deg);transform-origin:60px 60px}
.seg-ok{stroke:var(--ok)}
.seg-warn{stroke:var(--warn)}
.seg-bad{stroke:var(--bad)}
.js .donut:not(.live) .seg{stroke-dashoffset:var(--s0)}
.js .donut.live .seg{stroke-dashoffset:var(--s1);transition:stroke-dashoffset .9s cubic-bezier(.22,.61,.36,1) calc(140ms*var(--i,0))}
.donut-num{font-size:19px;font-weight:600;fill:var(--ink);letter-spacing:-.02em}
.donut-lab{font-size:8.5px;fill:var(--muted);font-family:var(--font-body)}
.donut-legend{display:flex;gap:16px;justify-content:center;font:500 13px var(--font-body);color:var(--muted);margin-top:12px}
.donut-legend b{font-weight:600;color:var(--ink);margin-left:4px}
.lg{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;vertical-align:0}
.lg-ok{background:var(--ok)}.lg-warn{background:var(--warn)}.lg-bad{background:var(--bad)}

/* trust strip + CTAs */
.trust{display:flex;gap:8px 26px;flex-wrap:wrap;list-style:none;margin:30px 0 0;padding:15px 20px;border:1px solid var(--border);border-radius:12px;background:var(--card)}
.trust li{display:flex;align-items:center;gap:9px;font:500 13px var(--font-body);color:var(--muted)}
.trust svg{color:var(--accent);flex:none}
.cta-row{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 20px;border-radius:10px;font:600 14px/1.2 var(--font-body);border:1px solid transparent;cursor:pointer;transition:transform .15s ease,box-shadow .15s ease,border-color .15s ease,background .15s ease}
.btn:hover{text-decoration:none}
.btn-primary{background:linear-gradient(135deg,#8ab4ff,#7ee0c0);color:#0b1220}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 22px rgba(126,224,192,.22)}
.btn-ghost{border-color:var(--border2);color:var(--accent)}
.btn-ghost:hover{border-color:var(--accent);transform:translateY(-2px)}

/* cards */
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px 22px;margin:18px 0;transition:transform .15s ease,border-color .15s ease,box-shadow .15s ease}
.card:hover{transform:translateY(-2px);border-color:var(--border2);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}
[data-theme="light"] .card:hover{box-shadow:0 8px 20px rgba(19,28,51,.08)}
.card .subnote{margin:8px 0 0}
.card h3:first-child{margin-top:2px}
.subnote{color:var(--muted);font-size:13px;line-height:1.6}
.mute{color:var(--muted)}
.dotsep{color:var(--muted);margin:0 6px}
.stale{color:var(--warn);font-weight:600}
.fresh{color:var(--muted)}

/* tables: zebra-free, hairline dividers, mono numerals */
.card.table-card{padding:0;overflow:hidden;margin-top:26px}
.table-scroll{overflow-x:auto}
@media (max-width:720px){ .table-scroll table{min-width:680px} }
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:10px 14px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
thead th{position:sticky;top:0;z-index:1;background:var(--card2);color:var(--muted);font:600 13px var(--font-body);cursor:pointer;user-select:none;white-space:nowrap}
thead th:hover{color:var(--ink)}
thead th.asc::after{content:" ↑"}
thead th.desc::after{content:" ↓"}
th.num,td.num{font-family:var(--font-mono);font-size:13px;letter-spacing:-.01em}
tbody tr{transition:background .15s ease}
tbody tr:hover td{background:var(--card2)}
tr.stalerow td{opacity:.6}
td .sub{color:var(--muted);font-size:13px;font-weight:400;margin-top:2px}
table.kv td:first-child{color:var(--muted);width:9.5em;white-space:nowrap}

/* chips */
.chip{display:inline-block;padding:2px 10px;border-radius:99px;border:1px solid var(--border);font-size:12px;font-weight:600;letter-spacing:.02em;color:var(--muted)}
.chip.ok{color:var(--ok);border-color:rgba(52,211,153,.45);background:rgba(52,211,153,.09)}
.chip.warn{color:var(--warn);border-color:rgba(251,191,36,.45);background:rgba(251,191,36,.09)}
.chip.bad{color:var(--bad);border-color:rgba(248,113,113,.45);background:rgba(248,113,113,.09)}

/* verdict banner (server pages) */
.verdict{display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.verdict-chip{padding:9px 20px;border-radius:12px;font:700 20px var(--font-mono);letter-spacing:.05em;border:1px solid var(--border);color:var(--muted)}
.verdict-chip.v-pass{color:var(--ok);border-color:rgba(52,211,153,.5);background:rgba(52,211,153,.1)}
.verdict-chip.v-partial{color:var(--warn);border-color:rgba(251,191,36,.5);background:rgba(251,191,36,.1)}
.verdict-chip.v-fail{color:var(--bad);border-color:rgba(248,113,113,.5);background:rgba(248,113,113,.1)}
.verdict-meta{min-width:0}
.verdict-meta h1{font-size:32px}
.verdict-badge{margin-left:auto;flex:none;border-radius:4px}

/* terminal panel: dark in BOTH themes, mono, error lines colored */
.term{background:var(--term);border:1px solid var(--term-border);border-radius:12px;overflow:hidden;margin:16px 0}
.term-bar{display:flex;align-items:center;gap:7px;padding:9px 14px;border-bottom:1px solid var(--term-border);font:500 12px var(--font-mono);color:var(--term-mut)}
.term-bar i{width:10px;height:10px;border-radius:50%;background:#31456d}
.term-bar i:nth-child(1){background:rgba(248,113,113,.55)}
.term-bar i:nth-child(2){background:rgba(251,191,36,.55)}
.term-bar i:nth-child(3){background:rgba(52,211,153,.55)}
.term-bar span{margin-left:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.term-body{margin:0;padding:14px 16px;font:400 12.5px/1.7 var(--font-mono);color:var(--term-ink);overflow:auto;max-height:340px;white-space:pre-wrap;word-break:break-word}
.tl{display:block}
.tl.err{color:var(--bad)}
.tl.mut{color:var(--term-mut)}

/* code blocks */
code{background:var(--card2);border:1px solid var(--border);padding:1px 6px;border-radius:5px;font-size:12.5px;font-weight:500;word-break:break-word}
pre.call{background:var(--term);border:1px solid var(--term-border);color:var(--term-ink);border-radius:10px;padding:12px 14px;overflow:auto;max-height:20rem;font-size:12.5px;line-height:1.6;white-space:pre-wrap;word-break:break-word;margin:.6em 0}
.embed-box pre.call, .badge-card pre.call{max-height:6rem}

/* stat grid (methodology) */
.statgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0 6px}
.statgrid .card{margin:0;padding:18px 20px}
.statgrid .num{font-size:32px;font-weight:600;line-height:1.1;letter-spacing:-.02em}
.statgrid .num.ok{color:var(--ok)}
.statgrid .num.bad{color:var(--bad)}
.statgrid label{display:block;font-size:13px;color:var(--muted);margin-top:7px}
.cta-card{margin-top:26px}

/* FAQ */
details{border:1px solid var(--border);border-radius:12px;background:var(--card);margin:10px 0;transition:border-color .15s ease}
details:hover{border-color:var(--border2)}
details[open]{border-color:var(--border2)}
summary{display:flex;align-items:center;cursor:pointer;padding:14px 18px;font:600 15px var(--font-body);list-style:none}
summary::-webkit-details-marker{display:none}
summary::after{content:"›";margin-left:auto;color:var(--muted);font-size:18px;transition:transform .2s ease}
details[open] summary::after{transform:rotate(90deg)}
details p{padding:0 18px 14px;margin:0;color:var(--muted)}
details p code{color:var(--ink)}

/* badge gallery */
.badge-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px;margin:18px 0}
.badge-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;transition:transform .15s ease,border-color .15s ease}
.badge-card:hover{transform:translateY(-2px);border-color:var(--border2)}
.badge-card .sub{color:var(--muted);font-size:12.5px;margin:8px 0}
.badge-card pre.call{font-size:11.5px;margin:8px 0}

/* phases / tools tables on server pages */
.phases td:first-child code,.tools td:first-child code{white-space:nowrap}
.phases td,.tools td{font-size:13px}
.phases td:nth-child(2){white-space:nowrap}

/* reveal (gated on .js so no-JS users see everything) */
.js [data-reveal]{opacity:0;transform:translateY(12px)}
.js [data-reveal].in{opacity:1;transform:none;transition:opacity .5s ease-out,transform .5s ease-out}

/* theme switch: 250ms color transition (applied only while switching) */
.theming,.theming *{transition:background-color .25s ease,border-color .25s ease,color .25s ease,fill .25s ease,stroke .25s ease !important}

/* reduced motion: ALL animation off — non-negotiable (design-language.md §5) */
@media (prefers-reduced-motion: reduce){
  html{scroll-behavior:auto}
  *,*::before,*::after{animation:none !important;transition:none !important}
  .js [data-reveal]{opacity:1 !important;transform:none !important}
  .js .donut:not(.live) .seg{stroke-dashoffset:var(--s1) !important}
}

/* responsive */
@media (max-width:900px){
  .hero-grid{grid-template-columns:1fr;gap:30px}
  .donut-wrap{justify-self:start}
  .verdict-badge{margin-left:0}
  .statgrid{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:560px){
  .wrap{padding:0 16px}
  h1{font-size:32px}
  .verdict-meta h1{font-size:26px}
  .headline{font-size:24px}
  .stat .num{font-size:26px}
  .hero-stats{gap:20px}
  .verdict-chip{font-size:16px;padding:7px 14px}
  .topbar .wrap{height:48px}
}
@media print{
  .topbar,#theme-toggle,.cta-row{display:none}
  body{background:#fff;color:#000}
}'''

if __name__ == "__main__":
    sys.exit(main())
