# LAUNCH CHECKLIST — MCP Health Index v0

**Everything here is a human step.** The code, the data, and the site are already in
this repo; this file is the operating manual. Budget context: $0 services, free tiers
only, revenue in BTC/SOL/USDT via the separate crypto checkout project.

## 1. Deploy the static site to GitHub Pages (free)

1. Create a **public** GitHub repo, e.g. `<your-username>/mcp-health-index`.
2. Push this repo to it (the `site/` directory is committed and deployable as-is):
   ```bash
   git remote add origin https://github.com/<your-username>/mcp-health-index.git
   git push -u origin main
   ```
3. Repo → **Settings → Pages → Build and deployment → Source: Deploy from a branch →
   Branch: `main` / root** → Save. (Alternatively use the `gh-pages` folder trick or
   Actions; branch-from-root is the zero-config path.)
4. Site live at `https://<your-username>.github.io/mcp-health-index/` (allow ~2 min).
5. Regenerate with the real base URL so badge markdown embeds absolute URLs:
   ```bash
   .venv/bin/python sitegen.py --base-url https://<your-username>.github.io/mcp-health-index
   git add site && git commit -m "site: absolute badge URLs" && git push
   ```
6. Optional: add a CNAME file for a custom domain (still $0 if you own the domain).

## 2. Keep it fresh — schedule the daily run

Pick ONE free scheduler:

**Option A — GitHub Actions (free on public repos, no secrets needed):**
`.github/workflows/daily.yml`:
```yaml
name: daily-health-run
on:
  schedule:
    - cron: "0 6 * * *"   # 06:00 UTC daily
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install -r requirements.txt
      - uses: actions/setup-node@v4
        with: {node-version: "22"}
      - run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          echo "$HOME/.local/bin" >> $GITHUB_PATH
      - run: |
          .venv/bin/python -m harness.run --limit 40 --workers 4 || true
          .venv/bin/python sitegen.py --base-url https://<your-username>.github.io/mcp-health-index
      - run: |
          git config user.name "health-bot" && git config user.email "bot@users.noreply.github.com"
          git add -A && git commit -m "daily run $(date -u +%F)" || echo "no changes"
          git push
```
Notes: unauthenticated GitHub API rate limits are per-IP; runners rotate IPs, which
helps. `--limit 40` per day cycles through the backlog continuously. The hosted
runner is an ephemeral VM — that satisfies the "run hostile code on a throwaway
machine" guidance. (`|| true` keeps the workflow green when a run exits non-zero.)

**Option B — a machine you control + cron** (throwaway user!):
```cron
0 6 * * *  cd ~/mcp-health-index && PATH="$HOME/.local/bin:$PATH" .venv/bin/python -m harness.run --limit 40 && .venv/bin/python sitegen.py --base-url https://… && git add -A && git commit -m "daily" && git push
```

Either way: commit `data/results.db` and `site/` daily. The DB history is what
powers the future breakage-alert product.

## 3. Announce (in order)

1. **X / build-in-public** — post the punchline stat + screenshot of the index:
   "I smoke-tested 240 popular MCP servers as an isolated client. Only ~1 in 4 boots
   and answers a read-only call. #1 killer: servers that demand API keys at boot.
   Public index: <link>". Post the daily delta weekly.
2. **r/mcp** (reddit.com/r/mcp) — title: "I built an automated MCP Health Index —
   it actually runs the servers (isolated stdio client), here's the first batch of
   results". Body: method, isolation notes, top failure modes, link, invite feedback.
   Do not hide the "we also sell badges later" part — build-in-public transparency
   plays well; keep it one line at the end.
3. **Show HN** (news.ycombinator.com) — "Show HN: MCP Health Index — we ran 240 MCP
   servers so you don't have to". Lead with the failure-mode taxonomy table and the
   honest SAFETY section (HN respects the limits being documented).
4. **MCP community channels** — the MCP server developers' Discord/Slack and
   modelcontextprotocol GitHub discussions: post as a tool for authors ("check your
   server's page; if it's PARTIAL/FAIL you can see the exact boot error"). Authors
   are the future badge customers — engage, don't spam.
5. **Direct outreach** — DM/email authors of high-star servers that came out FAIL
   with a concrete fix hint from their stderr tail. That email IS the sales motion:
   "here's why your server fails bare install; we re-test daily; badges are $X/mo."

## 4. Revenue wiring (separate project handles checkout)

The shared crypto checkout (BTC/SOL/USDT — separate project) exposes a webhook that
flips a server to "verified" in this repo's data. Integration points already exist:

- **Badge tier ($49–99/mo)**: subscriber's slug gets `status="verified"` +
  daily re-test priority (`--only <slug>` loop). Site renders a gold badge variant;
  detail page shows a 30-day uptime sparkline (from the runs table).
- **Breakage alerts ($19/mo)**: cron job diffs `runs` history per subscriber slug;
  on change (tools delta, handshake flip), POST to the checkout project's alert
  dispatcher (email/Telegram). No payment logic ever lives in this repo.
- **Per-stack niche indexes (v1)**: same DB, `WHERE description LIKE '%finance%'…`
  slices, separate landing pages — SEO play, zero extra infra.

## 5. Daily operation (one operator, <30 min/day)

```bash
# fresh candidates (registry grows ~700/day; stars enrich monthly is fine)
PATH="$HOME/.local/bin:$PATH" .venv/bin/python -m harness.fetch data/results.db

# test a batch (throwaway machine or CI)
PATH="$HOME/.local/bin:$PATH" .venv/bin/python -m harness.run --limit 40 --workers 4

# rebuild + publish
PATH="$HOME/.local/bin:$PATH" .venv/bin/python sitegen.py --base-url https://<user>.github.io/mcp-health-index
git add -A && git commit -m "daily run + site" && git push
```

Ops notes:
- If GitHub core API is rate-limited (60/h unauth), star/license enrichment simply
  stays `unknown` — testing is not affected.
- Re-test individual servers after fixing your rlimits/config:
  `.venv/bin/python -m harness.run --only <slug-substring> --force`
- Watch for new harness-artifact failure modes (like the early EMFILE one) — a
  failure that reproduces identically outside the sandbox should be confirmed by a
  manual run before it's called a server bug.

## 6. Week-1 scorecard

- [ ] Site live on GitHub Pages with ≥150 tested servers
- [ ] Daily run running (Actions or cron) for ≥5 consecutive days
- [ ] ≥2 announcement posts live (X, r/mcp)
- [ ] ≥500 unique visitors on index page (GitHub Pages does not give analytics; use
      a free Cloudflare/analytics.plausibel-tier or count badges/lambda — or simply
      track badge SVG hits from your webserver/CDN logs later)
- [ ] ≥10 author conversations from FAIL reports
- [ ] ≥3 servers with authors requesting the paid badge → validates the $49–99 tier
      before building the checkout integration
