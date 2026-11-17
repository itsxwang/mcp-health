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

# publish site/ to the orphan gh-pages branch (isolated worktree deploy)
bash scripts/deploy_site.sh
