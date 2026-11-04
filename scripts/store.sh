#!/usr/bin/env bash
# Persist research state on the `research-data` branch (kept separate from code).
#   store.sh pull   -> copy stored raw data, outcomes, reports, research state into the workspace
#   store.sh push   -> copy them back, enforce the size cap, commit and push
# Uses a git worktree so it reuses the credentials actions/checkout configured.
# Normalized tables are NOT stored: they are rebuilt from raw (pmr normalize-all).
set -euo pipefail
BRANCH=research-data
STORE=_store
CAP_MB=${CAP_MB:-900}
PATHS=(data/raw data/outcomes reports state)

case "${1:-}" in
  pull)
    if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git fetch --quiet --depth 1 origin "$BRANCH:$BRANCH"
      git worktree add --quiet "$STORE" "$BRANCH"
    else
      git worktree add --quiet --orphan -b "$BRANCH" "$STORE"
    fi
    for p in "${PATHS[@]}"; do
      if [ -d "$STORE/$p" ]; then mkdir -p "$p" && cp -a "$STORE/$p/." "$p/"; fi
    done
    ;;
  push)
    for p in "${PATHS[@]}"; do
      if [ -d "$p" ]; then mkdir -p "$STORE/$p" && cp -a "$p/." "$STORE/$p/"; fi
    done
    SIZE=$(du -sm --exclude=.git "$STORE" | cut -f1)
    echo "store size: ${SIZE} MB (cap ${CAP_MB} MB)"
    if [ "$SIZE" -gt "$CAP_MB" ]; then
      echo "::error::research-data would exceed ${CAP_MB} MB; not committing. Prune old raw runs first."
      exit 1
    fi
    cd "$STORE"
    git config user.name "pm-research-bot"
    git config user.email "actions@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then echo "nothing to commit"; exit 0; fi
    git commit --quiet -m "${2:-update research data} [${GITHUB_RUN_ID:-local}]"
    git push --quiet origin "HEAD:$BRANCH"
    ;;
  *) echo "usage: $0 pull|push [message]"; exit 2 ;;
esac
