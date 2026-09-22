#!/usr/bin/env bash
# kimi_offload.sh — run one task on Kimi (Moonshot) from inside a Claude Code
# session, and print the result.
#
# Why this exists: a hook can set the model of a *sub-agent* (task_brief.py),
# but nothing can move the running session, or a sub-agent, off Anthropic's
# API — the base URL is process-wide. The one way to spend Moonshot dollars
# instead of Max quota on a piece of work is a second `claude -p` process
# with its own environment. That is what this is. The parent model calls it
# from Bash when the route card says so (quota strained, work light or
# standard) and gets plain text back.
#
#   bash kimi_offload.sh [--tier light|standard|heavy] [--cwd DIR] "the task"
#   echo "the task" | bash kimi_offload.sh --tier light
#
#   light     → kimi-k2.7-code      cheap and quick: grep, list, summarise, rename
#   standard  → kimi-k3[1m]         (default) routine implementation
#   heavy     → kimi-k3[1m]         same model, max effort; you probably should
#                                    not be offloading heavy work at all
#
# Key: MOONSHOT_API_KEY from the environment, then
# ~/.claude/skill_router_cache/env.json (refresh_env.py caches it from Doppler
# shared/prd at SessionStart), then Doppler directly. No key → exit 2, and the
# parent does the work itself.
#
# The child runs with SKILL_ROUTER_OFF=1 so none of the router's hooks fire
# inside it, --strict-mcp-config with no servers (the MCP tool list alone is
# ~255k tokens, billed at full price on Moonshot every turn), and
# --dangerously-skip-permissions because nobody is there to answer a prompt.
# Every call pays ~30k tokens of system prompt (~$0.10 cache write on K3), so
# batch small things into one task rather than calling this in a loop.

set -uo pipefail

TIER="standard"
CWD="$PWD"
TASK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="${2:-standard}"; shift 2;;
    --tier=*) TIER="${1#--tier=}"; shift;;
    --cwd) CWD="${2:-$PWD}"; shift 2;;
    --cwd=*) CWD="${1#--cwd=}"; shift;;
    -h|--help) sed -n '2,32p' "$0"; exit 0;;
    *) TASK="${TASK:+$TASK }$1"; shift;;
  esac
done
[ -n "$TASK" ] || [ -t 0 ] || TASK="$(cat)"
[ -n "$TASK" ] || { echo "kimi_offload: no task given" >&2; exit 2; }

case "$TIER" in
  light)    MODEL="kimi-k2.7-code"; EFFORT="medium";;
  heavy)    MODEL="kimi-k3[1m]";    EFFORT="max";;
  *)        MODEL="kimi-k3[1m]";    EFFORT="high";;
esac

CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
[ -x "$CLAUDE_BIN" ] || CLAUDE_BIN="$(command -v claude || true)"
[ -n "$CLAUDE_BIN" ] || { echo "kimi_offload: claude binary not found" >&2; exit 2; }

KEY="${MOONSHOT_API_KEY:-}"
if [ -z "$KEY" ] && [ -r "$HOME/.claude/skill_router_cache/env.json" ]; then
  KEY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("MOONSHOT_API_KEY",""))' \
         "$HOME/.claude/skill_router_cache/env.json" 2>/dev/null || true)"
fi
if [ -z "$KEY" ]; then
  DOPPLER="$(command -v doppler || echo "$HOME/.local/bin/doppler")"
  [ -x "$DOPPLER" ] && KEY="$("$DOPPLER" secrets get MOONSHOT_API_KEY --project shared --config prd --plain 2>/dev/null || true)"
fi
[ -n "$KEY" ] || { echo "kimi_offload: MOONSHOT_API_KEY not set, not cached, not in Doppler shared/prd" >&2; exit 2; }

LOG="$HOME/.claude/skill_router_log.jsonl"
START=$(date +%s)

# The task goes in on stdin: --mcp-config is variadic and would swallow a
# positional prompt as one more config path.
OUT="$(cd "$CWD" && printf '%s' "$TASK" | \
  SKILL_ROUTER_OFF=1 \
  ANTHROPIC_BASE_URL="https://api.moonshot.ai/anthropic" \
  ANTHROPIC_AUTH_TOKEN="$KEY" \
  ANTHROPIC_MODEL="$MODEL" ANTHROPIC_DEFAULT_OPUS_MODEL="$MODEL" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL" ANTHROPIC_DEFAULT_HAIKU_MODEL="kimi-k2.7-code" \
  CLAUDE_CODE_SUBAGENT_MODEL="$MODEL" \
  CLAUDE_CODE_EFFORT_LEVEL="$EFFORT" \
  CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000 \
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  "$CLAUDE_BIN" -p --output-format text --dangerously-skip-permissions \
    --strict-mcp-config --mcp-config '{"mcpServers":{}}' 2>&1)"
RC=$?

# One line in the router log so weekly-analysis can count offloads.
python3 - "$LOG" "$TIER" "$MODEL" "$RC" "$(( $(date +%s) - START ))" "$(printf '%s' "$TASK" | wc -w | tr -d ' ')" <<'EOF' 2>/dev/null || true
import json, sys, time, os
log, tier, model, rc, secs, words = sys.argv[1:]
os.makedirs(os.path.dirname(log), exist_ok=True)
with open(log, "a") as f:
    f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "kimi-offload",
                        "tier": tier, "model": model, "rc": int(rc), "seconds": int(secs),
                        "task_words": int(words)}) + "\n")
EOF

# Two harness warnings arrive on every Moonshot run and mean nothing here.
printf '%s\n' "$OUT" | grep -v -e '^⚠ claude.ai connectors are disabled' -e '^\[claude-code:unrecognized_model\]'
exit $RC
