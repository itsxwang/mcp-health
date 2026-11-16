#!/usr/bin/env bash
# Daily MCP Health run (local replacement for .github/workflows/daily.yml,
# whose scheduled Actions runs never fired). Mirrors the workflow steps:
#   intake -> batch of 40 (rotating, untested first) -> sitegen -> gh-pages.
# Must run under .venv/bin/python (the system python lacks the mcp SDK).
set -u
cd "$(dirname "$0")/.."
PY="$PWD/.venv/bin/python"

"$PY" -m harness.intake || true          # pick up submitted GitHub issues
"$PY" -m harness.run --limit 40 --workers 4 || true
"$PY" sitegen.py || true

# publish site/ to the orphan gh-pages branch (same steps as the workflow)
git config user.name "health-bot"
git config user.email "bot@users.noreply.github.com"
git fetch origin gh-pages --depth 1 || true
if git rev-parse --verify origin/gh-pages >/dev/null 2>&1; then
  git switch gh-pages
  git reset --hard origin/gh-pages
else
  git switch --orphan gh-pages
fi
find . -mindepth 1 -maxdepth 1 ! -name '.git' ! -name '.nojekyll' ! -name 'site' -exec rm -rf {} +
cp -a site/* .
touch .nojekyll
git add -A
git commit -m "deploy: MCP Health Index $(date -u +%FT%TZ)" || echo "no changes"
git push origin gh-pages
