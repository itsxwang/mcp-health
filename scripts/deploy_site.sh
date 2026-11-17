#!/usr/bin/env bash
# Deploy site/ to the gh-pages branch via a git WORKTREE (isolated checkout).
# Why not switch branches in-place: the old approach ran a find-based cleanup on
# the gh-pages checkout which deleted untracked paths (.venv), and git silently
# replaced ignored files (site/) with stale tracked copies on every switch.
set -euo pipefail
cd "$(dirname "$0")/.."

WT=/tmp/mcphi-ghp
git worktree add "$WT" gh-pages 2>/dev/null || git worktree list | grep -q "$WT" || {
  echo "worktree add failed" >&2; exit 1; }
( cd "$WT" && git reset -q --hard origin/gh-pages )

# replace all published content with the fresh build (keep .git/.nojekyll)
find "$WT" -mindepth 1 -maxdepth 1 ! -name '.git' ! -name '.nojekyll' -exec rm -rf {} +
cp -a site/. "$WT"/
touch "$WT/.nojekyll"

# guard: refuse to ship a build without the design-v2 markers
grep -q donut "$WT/index.html" || { echo "index.html missing donut marker — aborting deploy" >&2; exit 1; }
grep -q "fonts/" "$WT/index.html" || { echo "index.html missing font refs — aborting deploy" >&2; exit 1; }

cd "$WT"
git add -A
git commit -qm "deploy: MCP Health Index $(date -u +%FT%TZ)" || echo "no changes"
git push -q origin gh-pages
echo "gh-pages deployed."
