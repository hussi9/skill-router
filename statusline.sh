#!/usr/bin/env bash
# Claude Code statusline — model · dir · git · active skill · context · cost · time · lines
#
# INSTALL:
#   cp statusline.sh ~/.claude/statusline.sh
#   chmod +x ~/.claude/statusline.sh
#
# Then add to ~/.claude/settings.json:
#   "statusLine": {
#     "type": "command",
#     "command": "~/.claude/statusline.sh",
#     "padding": 2
#   }
#
# SKILL TRACKING (optional — shows active skill in statusline):
#   Add the hook from settings-hooks.json to your settings.json PostToolUse section.
#   Skills are logged to ~/.claude/skill_usage.log and shown for 120s after firing.

input=$(cat)

# ── Parse all fields via one Python call ──────────────────────────────────────
parse_py='
import sys, json, os
try:
    d = json.loads(sys.argv[1])
except:
    d = {}
home = os.path.expanduser("~")

m = d.get("model", {})
model_raw = (m.get("display_name","") if isinstance(m,dict) else str(m)) or ""

p = d.get("workspace", {}).get("current_dir", "")
if p == home:
    cwd = "~"
elif p.startswith(home + "/"):
    cwd = "~/" + os.path.relpath(p, home)
else:
    cwd = os.path.basename(p) or "?"

cw = d.get("context_window", {})
ctx_pct   = cw.get("used_percentage", 0)
ctx_used  = cw.get("used_tokens", 0)
ctx_total = cw.get("total_tokens", 0)

c = d.get("cost", {})
cost_usd    = float(c.get("total_cost_usd", 0) or 0)
duration_ms = int(c.get("total_duration_ms", 0) or 0)
added       = int(c.get("total_lines_added", 0) or 0)
removed     = int(c.get("total_lines_removed", 0) or 0)
turns       = int(c.get("turn_count", 0) or d.get("session", {}).get("turn_count", 0) or 0)
cache_tok   = int(c.get("cache_read_tokens", 0) or 0)

print(f"{model_raw}|{cwd}|{ctx_pct}|{cost_usd:.4f}|{duration_ms}|{added}|{removed}|{turns}|{ctx_used}|{ctx_total}|{cache_tok}")
'

raw=$(python3 -c "$parse_py" "$input" 2>/dev/null)
IFS='|' read -r model_raw cwd_raw ctx_pct cost_raw duration_ms \
               lines_added lines_removed turns_raw ctx_used ctx_total cache_tokens <<< "$raw"

# ── Shorten model name ─────────────────────────────────────────────────────────
case "$model_raw" in
  *opus*)   model_short="opus"   ;;
  *sonnet*) model_short="sonnet" ;;
  *haiku*)  model_short="haiku"  ;;
  *)        model_short="${model_raw##*-}" ;;
esac

# ── Git info ───────────────────────────────────────────────────────────────────
git_str=""
real_cwd="${cwd_raw/#\~/$HOME}"
if git -C "$real_cwd" rev-parse --git-dir &>/dev/null 2>&1; then
  branch=$(git -C "$real_cwd" symbolic-ref --short HEAD 2>/dev/null \
           || git -C "$real_cwd" rev-parse --short HEAD 2>/dev/null)
  upstream=$(git -C "$real_cwd" rev-parse --abbrev-ref '@{u}' 2>/dev/null)
  ab=""
  if [ -n "$upstream" ]; then
    ahead=$(git  -C "$real_cwd" rev-list --count "@{u}..HEAD" 2>/dev/null)
    behind=$(git -C "$real_cwd" rev-list --count "HEAD..@{u}" 2>/dev/null)
    [ "${ahead:-0}"  -gt 0 ] && ab+="↑${ahead}"
    [ "${behind:-0}" -gt 0 ] && ab+="↓${behind}"
  fi
  dirty=$(git -C "$real_cwd" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  dirty_mark=""
  [ "${dirty:-0}" -gt 0 ] && dirty_mark="✱${dirty}"
  git_str="⎇ ${branch}${dirty_mark}${ab:+ $ab}"
fi

# ── Context mini-bar (10 blocks) ───────────────────────────────────────────────
ctx_int=${ctx_pct%.*}; ctx_int=${ctx_int:-0}
filled=$(( ctx_int / 10 ))
empty=$(( 10 - filled ))
bar=""
for (( i=0; i<filled; i++ )); do bar+="▓"; done
for (( i=0; i<empty;  i++ )); do bar+="░"; done

# ── Duration ──────────────────────────────────────────────────────────────────
mins=$(( ${duration_ms:-0} / 60000 ))
secs=$(( (${duration_ms:-0} % 60000) / 1000 ))
duration=$(printf "%d:%02d" "$mins" "$secs")

# ── Cost formatting ────────────────────────────────────────────────────────────
cost_num="${cost_raw:-0}"
cost_display=$(python3 -c "print(f'\${float(\"$cost_num\"):.2f}')" 2>/dev/null || echo '$0.00')
cost_tier=0
[ "$(python3 -c "print(1 if float('${cost_num}') >= 0.5 else 0)" 2>/dev/null)" = "1" ] && cost_tier=1
[ "$(python3 -c "print(1 if float('${cost_num}') >= 1.0 else 0)" 2>/dev/null)" = "1" ] && cost_tier=2
[ "$(python3 -c "print(1 if float('${cost_num}') >= 2.0 else 0)" 2>/dev/null)" = "1" ] && cost_tier=3

# ── Mood indicator ────────────────────────────────────────────────────────────
mood="◆"
[ "${ctx_int:-0}" -ge 70 ] && mood="◈"
[ "${ctx_int:-0}" -ge 90 ] && mood="◉"
[ "${cost_tier:-0}" -ge 3 ] && mood="⚡"

# ── ANSI helpers ──────────────────────────────────────────────────────────────
R='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
BCYAN='\033[1;36m'
BYELLOW='\033[1;33m'
BGREEN='\033[1;32m'
BRED='\033[1;31m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
MAGENTA='\033[35m'
BLUE='\033[34m'
CYAN='\033[36m'
ORANGE='\033[38;5;208m'

# Context color
if   [ "${ctx_int:-0}" -ge 90 ]; then ctx_color=$BRED
elif [ "${ctx_int:-0}" -ge 70 ]; then ctx_color=$BYELLOW
else                                   ctx_color=$BGREEN
fi

# Cost color
case ${cost_tier:-0} in
  0) cost_color=$GREEN ;;
  1) cost_color=$YELLOW ;;
  2) cost_color=$ORANGE ;;
  *) cost_color=$BRED ;;
esac

# ── Last skill used (from log, within last 120s) ──────────────────────────────
# Requires the PostToolUse hook from settings-hooks.json to be active.
# Log format: "2026-04-17 14:23:01\tskill-name"
skill_str=""
log_file="$HOME/.claude/skill_usage.log"
if [ -f "$log_file" ]; then
  last_line=$(tail -1 "$log_file" 2>/dev/null)
  if [ -n "$last_line" ]; then
    last_ts=$(echo "$last_line" | awk '{print $1, $2}')
    last_skill=$(echo "$last_line" | awk '{print $3}')
    now_epoch=$(date +%s)
    last_epoch=$(date -j -f "%Y-%m-%d %H:%M:%S" "$last_ts" +%s 2>/dev/null \
              || date -d "$last_ts" +%s 2>/dev/null \
              || echo 0)
    age=$(( now_epoch - last_epoch ))
    if [ "$age" -le 120 ] && [ -n "$last_skill" ]; then
      short_skill="${last_skill##*:}"  # strip plugin prefix
      skill_str="⚙ ${short_skill}"
    fi
  fi
fi

# ── Build output ──────────────────────────────────────────────────────────────
S="${DIM} · ${R}"

out="${DIM}${mood}${R} ${BCYAN}${model_short}${R}"
[ -n "$cwd_raw" ]   && out+="${S}${BLUE}${cwd_raw}${R}"
[ -n "$git_str" ]   && out+="${S}${MAGENTA}${git_str}${R}"
[ -n "$skill_str" ] && out+="${S}${BOLD}${CYAN}${skill_str}${R}"
out+="${S}${ctx_color}${bar} ${ctx_pct}%${R}"
[ "${turns_raw:-0}" -gt 0 ] && out+="${S}${DIM}${turns_raw}t${R}"
out+="${S}${cost_color}${cost_display}${R}"
out+="${S}${DIM}${duration}${R}"

lines_part=""
[ "${lines_added:-0}" -gt 0 ]   && lines_part+="${BGREEN}+${lines_added}${R}"
[ "${lines_removed:-0}" -gt 0 ] && lines_part+="${lines_part:+ }${RED}−${lines_removed}${R}"
[ -n "$lines_part" ] && out+="${S}${lines_part}"

[ "${cache_tokens:-0}" -gt 5000 ] && out+="${S}${DIM}⚡cache${R}"

echo -e "$out"
