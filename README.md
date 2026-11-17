# MCP Health Index

**We actually ran the MCP servers.** Every entry in the public index was installed and
smoke-tested live as an isolated MCP client over stdio: ephemeral install → protocol
handshake → tool/resource listing → one read-only tool call with inert arguments.
Results — including every failure — are published as a static site with per-server
detail pages and embeddable badges.

> The MCP ecosystem has ~63,000 registered servers and no directory runs automated
> compatibility tests. Smithery's hosted service shut down Oct 31, 2026. This project
> fills that gap from the bottom: run the code, publish what happens.

## What's in the index

| status | meaning |
|---|---|
| **PASS** | installed, handshook, listed tools, and answered one read-only call |
| **PARTIAL** | booted + listed tools, but the call was skipped by policy (no read-only-looking tool name) or failed (e.g. missing API key) |
| **FAIL** | would not install, or died before/during the handshake |
| **UNTESTED** | candidate registered but not yet run by the harness |

A `PASS` is *not* a security audit or a guarantee of protocol correctness. It means:
"this package, installed from its declared registry, speaks MCP over stdio well enough
to complete a full client round-trip today."

## Architecture

```
official MCP registry API ─┐
                           ├─→ fetch.py ─→ SQLite (data/results.db)
GitHub stars search ───────┘   (candidates + stars/license/freshness)
GitHub submission issues ──┘   (intake.py parses open server-submission issues)
        │  (+ verified npm-repo fallback when the registry is thin)
        ▼
   run.py  ── spawns per candidate ──▶  python -m harness.oneshot
        │                                  │ 1. install (ephemeral: npm --prefix / uv venv)
        │                                  │ 2. sandbox wrap (rlimits, fresh env, optional net-ns)
        │                                  │ 3. MCP client via official `mcp` SDK:
        │                                  │    initialize → list_tools → list_resources
        │                                  │    → ONE read-only call w/ inert args
        │                                  └─ writes data/results/<slug>.json
        ▼
   sitegen.py ──▶ site/  (pure HTML/CSS + ~15 lines vanilla JS, GitHub-Pages ready)
        ├── index.html          sortable table, status chips, live pass-rate headline
        ├── server/<slug>.html  full run report per server (+ embed snippet, freshness)
        ├── methodology.html    what a test is, what PASS means, limits, FAQ
        ├── badges.html         embed instructions + badge gallery
        ├── summary.html        exact run environment
        ├── badges/<slug>.svg   shields-style badge per server
        ├── og/*.png            1200×630 OG cards (Pillow, generated locally)
        ├── robots.txt / sitemap.xml
        └── data.json           machine-readable full results
```

Components:

- **`harness/sandbox.py`** — resource limits, sanitized env, network-namespace detection
- **`harness/limitwrap.py`** — exec wrapper applying rlimits to the actual server process
- **`harness/install.py`** — ephemeral npm / PyPI installs, entrypoint resolution
- **`harness/probe.py`** — MCP client behavior + tool-call safety policy
- **`harness/oneshot.py`** — one candidate per OS process group, SIGALRM watchdog
- **`harness/run.py`** — worker pool orchestrator (SIGKILL to the whole process group)
- **`harness/fetch.py`** — candidate sourcing (registry → GitHub → npm, in that order)
- **`harness/db.py`** — SQLite persistence
- **`harness/badges.py`** / **`sitegen.py`** — badges + static site

## SAFETY — running arbitrary MCP server code

MCP servers are **arbitrary third-party code** that will be executed. An npm/PyPI
package that an attacker publishes can do anything the host user can. This harness
runs untrusted code on purpose; here is exactly what is and isn't contained.

### What isolation exists (and is enforced)

1. **Ephemeral installs.** npm: fresh `npm install --prefix <tmpdir>`; PyPI: fresh
   `uv venv <tmp>/venv` + `uv pip install`. Nothing is installed globally; the temp
   dir is deleted after the run. Install (network allowed) is a separate step from
   run so the run can be isolated.
2. **Resource limits on the server process** (`harness/limitwrap.py`, via
   `setrlimit` before `exec`):
   - `RLIMIT_CPU` 60s per process, `RLIMIT_AS` 2 GiB address space,
     `RLIMIT_NPROC` 2048 tasks (fork-bomb cap), `RLIMIT_FSIZE` 64 MiB per file,
     `RLIMIT_NOFILE` 1024, no core dumps.
   - Note: `RLIMIT_NPROC` counts **all tasks of the host user**, so it must sit
     above an existing desktop/CI baseline (~900 tasks here) — it is a cap, not a
     per-sandbox guarantee.
3. **Sanitized environment.** The server gets `PATH`, `HOME`/`TMPDIR` (throwaway
   dir), `LANG`, `TERM` — and nothing else. No tokens, API keys or credentials from
   the operator's environment ever reach the server. Registry-declared *required*
   env vars are filled with the inert placeholder `mcp-health-index-placeholder`.
4. **Hard kill.** Each candidate runs as its own OS session/process group. A SIGALRM
   watchdog inside the runner, plus the orchestrator's wall clock (300 s), end with
   `SIGKILL` to the **entire process group** — a hung or fork-bombing server cannot
   outlive its budget or take other workers down.
5. **Network namespace isolation when available** (bwrap/unshare `--unshare-net`),
   auto-detected with the same rlimits as a real run. On hosts that forbid
   unprivileged user/network namespaces, the run records
   `net_isolation: OFF (unavailable…)` in `summary.html` instead of pretending.

### What isolation does NOT protect against — read this

- **Network egress is NOT restricted in the default v0 runs on such hosts.** A server
  can phone home, exfiltrate anything it can read, or scan the network. Mitigations
  that *reduce* (not eliminate) this: the environment contains no secrets, HOME is
  empty, and nothing valuable is on disk in the sandbox. Still: run this tool on a
  throwaway machine/CI runner, not your workstation.
- **The sandbox is not a security boundary.** rlimits bound *resources*, not
  capabilities. A determined malicious package inside those limits can still make
  outbound connections, attempt kernel exploits, or hammer third-party APIs. Only a
  real sandbox (VM/gVisor/firejail+netns) changes that.
- **Tool-call selection is heuristic.** We pick a tool whose *name* matches read-only
  verbs (list/get/read/search/…) and whose name doesn't match destructive verbs. A
  server can lie in a name. Arguments are built only from *required* schema fields
  with inert values (enum[0]/declared default/`"test"`/1/false; path-ish → the empty
  throwaway cwd; timezone → `UTC`). Even so, a "read-only" call can trigger external
  API usage with our placeholder credentials.
- **`PASS` ≠ safe.** It is a boot-and-respond signal, not an audit. We do not read or
  review server code.

### Operating guidance

- Run on a dedicated user account / CI runner; treat every temp dir as hostile.
- The two kill switches (SIGALRM + orchestrator SIGKILL) mean a runaway test dies at
  the wall clock; a result file that never appears is recorded as `wall_timeout`.
- Results are never invented: candidates not yet run are `UNTESTED` and are shown
  that way on the site.

## Roadmap

- **v0.1 — "Verified Compatible" badges** ($49–99/mo, crypto checkout handled by the
  separate payments project): daily re-runs for subscribers, badge embeds, first
  breakage-alert emails.
- **v0.2 — breakage alerts** ($19/mo): diff today's run vs yesterday's per server
  (tools added/removed, new handshake failure) → webhook/email alert. The SQLite
  history already supports this.
- **v1 — per-stack niche indexes**: finance MCP servers, data/ETL MCP servers, etc.
  — the registry's `packages` + description keywords make the slices trivial.
- Remote (streamable-http) server testing with a similar client probe.
- `--net-isolate on` runs where namespaces are available; compare pass rates.

## Reproduce

See `LAUNCH_CHECKLIST.md` (§Daily operation). Live site:
https://itsxwang.github.io/mcp-health/ · submit your server via a pre-filled
[GitHub issue](https://github.com/itsxwang/mcp-health/issues/new?labels=server-submission).
Requirements: Python ≥3.10 (the official `mcp` SDK is the only pinned runtime dep),
Node/npm for npm candidates, `uv` for PyPI candidates, network access.

## Contact

xion.dev7@gmail.com · not affiliated with Anthropic or the Model Context Protocol project.

## License

MIT. Test results are facts about public packages; publish freely with attribution.

## Related projects

- **Citation Monitor** — weekly AI-visibility digests ($19/mo, crypto-only): https://itsxwang.github.io/citation-monitor/
- **Cash Register** — the static crypto-checkout stack that sells our tools (BTC/ETH/SOL, keyless on-chain confirmation): https://itsxwang.github.io/cash-register/

Contact: xion.dev7@gmail.com
