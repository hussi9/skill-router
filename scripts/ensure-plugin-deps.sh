#!/usr/bin/env bash
# ensure-plugin-deps.sh — install missing node_modules for every Claude Code
# plugin cache that ships a package.json. Idempotent. Safe to run on every
# SessionStart; exits fast when everything is already installed.
#
# Why: plugins under ~/.claude/plugins/cache/<owner>/<name>/<version>/ ship
# package.json with runtime deps (e.g. claude-mem ships zod), but Claude Code
# does not auto-install them when the cache version directory is created.
# That left worker-service.cjs failing with "Cannot find module 'zod/v3'".
#
# Strategy:
# - Find every cache/<owner>/<name>/<version>/package.json (depth 4).
# - Skip if package.json declares no dependencies.
# - Skip if node_modules/ already exists in that directory.
# - Run `bun install` (preferred — plugins are bun-runtime) or fall back to npm.
# - Log to ~/.claude/logs/plugin-deps.log so failures are inspectable later.
#
# Designed to be invoked in the background from a SessionStart hook so it
# never blocks Claude Code startup.

set -uo pipefail

CACHE_DIR="${CLAUDE_PLUGIN_CACHE:-$HOME/.claude/plugins/cache}"
LOG_DIR="$HOME/.claude/logs"
LOG_FILE="$LOG_DIR/plugin-deps.log"

mkdir -p "$LOG_DIR" 2>/dev/null || true

log() {
  printf '%s [ensure-plugin-deps] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

[ -d "$CACHE_DIR" ] || { log "no cache dir at $CACHE_DIR, exiting"; exit 0; }

# Pick installer. Prefer bun (the runtime plugins actually use).
INSTALLER=""
if command -v bun >/dev/null 2>&1; then
  INSTALLER="bun install --silent"
elif [ -x "$HOME/.bun/bin/bun" ]; then
  INSTALLER="$HOME/.bun/bin/bun install --silent"
elif command -v npm >/dev/null 2>&1; then
  INSTALLER="npm install --no-audit --no-fund --silent"
else
  log "no bun or npm available, skipping"
  exit 0
fi

# jq is required to inspect dependencies. If absent, fall back to a grep
# heuristic (any package.json that mentions "dependencies").
HAS_JQ=0
command -v jq >/dev/null 2>&1 && HAS_JQ=1

installed=0
skipped=0
failed=0

# depth 4 = cache/<owner>/<plugin>/<version>/package.json
while IFS= read -r pkg; do
  dir=$(dirname "$pkg")

  # Skip if no dependencies block. With jq we can be precise; without, be lenient.
  if [ "$HAS_JQ" = "1" ]; then
    if ! jq -e '(.dependencies // {}) | length > 0' "$pkg" >/dev/null 2>&1; then
      skipped=$((skipped + 1))
      continue
    fi
  else
    grep -q '"dependencies"' "$pkg" 2>/dev/null || { skipped=$((skipped + 1)); continue; }
  fi

  # Already installed -> nothing to do.
  if [ -d "$dir/node_modules" ]; then
    skipped=$((skipped + 1))
    continue
  fi

  log "installing in $dir"
  if (cd "$dir" && eval "$INSTALLER") >>"$LOG_FILE" 2>&1; then
    installed=$((installed + 1))
    log "ok: $dir"
  else
    failed=$((failed + 1))
    log "FAILED: $dir (see install output above)"
  fi
done < <(find "$CACHE_DIR" -mindepth 4 -maxdepth 4 -name package.json -not -path "*/node_modules/*" 2>/dev/null)

log "summary: installed=$installed skipped=$skipped failed=$failed"
exit 0
