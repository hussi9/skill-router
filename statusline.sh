#!/usr/bin/env bash
# Claude Code statusline — model · dir · git · ROUTER · skill · context · cost · time · lines
#
# Shows skill-router activity inline:
#   🔀 R5  …………………… session count of router fires
#   ▶ ship-feature 2/4 …. live chain progress (when a chain is mid-flight)
#   ⚙ frontend-design ✓.. last skill, ✓ if it came via a catalog upgrade
#   ✦ saved …………………… badge when a saved-chain match won
#
# INSTALL:
#   cp statusline.sh ~/.claude/statusline.sh
#   chmod +x ~/.claude/statusline.sh
#   then merge settings-hooks.json into ~/.claude/settings.json

input=$(cat)

# ── Parse session JSON via one Python call ────────────────────────────────────
parse_py='
import sys, json, os
try:
    d = json.loads(sys.argv[1])
except Exception:
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

cost_tier = 0
if cost_usd >= 2.0: cost_tier = 3
elif cost_usd >= 1.0: cost_tier = 2
elif cost_usd >= 0.5: cost_tier = 1
print(f"{model_raw}|{cwd}|{ctx_pct}|{cost_usd:.4f}|{duration_ms}|{added}|{removed}|{turns}|{ctx_used}|{ctx_total}|{cache_tok}|{cost_tier}")
'

raw=$(python3 -c "$parse_py" "$input" 2>/dev/null)
IFS='|' read -r model_raw cwd_raw ctx_pct cost_raw duration_ms \
               lines_added lines_removed turns_raw ctx_used ctx_total cache_tokens cost_tier <<< "$raw"

# ── Shorten model name ────────────────────────────────────────────────────────
case "$model_raw" in
  *opus*)   model_short="opus"   ;;
  *sonnet*) model_short="sonnet" ;;
  *haiku*)  model_short="haiku"  ;;
  *)        model_short="${model_raw##*-}" ;;
esac

# ── Git info ──────────────────────────────────────────────────────────────────
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

# ── Context mini-bar (10 blocks) ──────────────────────────────────────────────
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

# ── Cost formatting ───────────────────────────────────────────────────────────
cost_num="${cost_raw:-0}"
cost_display=$(printf '$%.2f' "$cost_num" 2>/dev/null || echo '$0.00')
cost_tier="${cost_tier:-0}"

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
BMAGENTA='\033[1;35m'
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

# ── Skill-router awareness (pulls 3 signals from logs) ────────────────────────
# Computed via one Python call so we don't fork many awk/date subprocesses:
#   1) router_active   — true if skill-router fired in last 30s
#   2) router_count    — how many times skill-router fired this session
#   3) chain_progress  — "name N/M" if a chain is mid-flight (last 5 min)
#   4) saved_match     — "name" if the active chain was a saved match
#   5) catalog_upgrade — true if last skill came via a catalog upgrade
#   6) last_skill      — short name of last skill fired (within 120s)

router_py='
import os, json, sys, time
home = os.path.expanduser("~")
session_dur_ms = int(sys.argv[1] or 0)
now = time.time()
session_start = now - (session_dur_ms / 1000.0)

router_active = "0"
router_count = "0"
chain_progress = ""
saved_match = ""
catalog_upgrade = "0"
last_skill = ""
thinking_level = ""    # "ultra", "hard", "think", or empty

# 1) skill_usage.log: timestamp\tskill (no extra field — provenance comes from skill_router_log.jsonl)
log = os.path.join(home, ".claude", "skill_usage.log")
if os.path.isfile(log):
    try:
        with open(log) as f:
            lines = f.readlines()[-200:]
        rcount = 0
        last_router_age = 99999
        last_skill_name = ""
        last_t = 0
        for line in lines:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2: continue
            ts, skill = parts[0], parts[1]
            try:
                t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            except Exception:
                continue
            if t < session_start: continue
            if skill == "skill-router":
                rcount += 1
                last_router_age = now - t
            else:
                last_skill_name = skill.split(":")[-1]
                last_t = t
        router_count = str(rcount)
        if last_router_age <= 30: router_active = "1"
        if last_t and (now - last_t) <= 120 and last_skill_name:
            last_skill = last_skill_name
    except Exception:
        pass

# 2) skill_router_log.jsonl — chain progress + thinking + catalog-upgrade
chainlog = os.path.join(home, ".claude", "skill_router_log.jsonl")
if os.path.isfile(chainlog):
    try:
        with open(chainlog) as f:
            entries = f.readlines()[-50:]
        active = None
        last_thinking_active = None
        last_upgrade_age = 99999
        for line in entries:
            try:
                e = json.loads(line)
            except Exception:
                continue
            try:
                t = time.mktime(time.strptime(e.get("ts",""), "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            if (now - t) > 300: continue
            etype = e.get("type")
            if etype == "chain-start":
                active = {"name": e.get("name",""), "of": len(e.get("steps",[])), "step": 0,
                          "saved": e.get("saved", False)}
            elif etype == "chain-step" and active is not None:
                active["step"] = e.get("step", active["step"])
                if e.get("via") == "catalog-upgrade":
                    last_upgrade_age = now - t
            elif etype == "chain-end":
                active = None
            elif etype == "thinking-active":
                # event has {"level": "ultra"|"hard"|"think", "active": true|false}
                if e.get("active"):
                    last_thinking_active = {"level": e.get("level",""), "t": t}
                else:
                    last_thinking_active = None
            elif etype == "catalog-upgrade":
                last_upgrade_age = now - t
        if active and active["of"] > 0:
            name_short = active["name"][:18]
            step_n = active["step"]
            of_n = active["of"]
            chain_progress = f"{name_short} {step_n}/{of_n}"
            if active.get("saved"): saved_match = active["name"]
        if last_thinking_active and (now - last_thinking_active["t"]) <= 60:
            thinking_level = last_thinking_active["level"]
        if last_upgrade_age <= 120:
            catalog_upgrade = "1"
    except Exception:
        pass

print(f"{router_active}|{router_count}|{chain_progress}|{saved_match}|{catalog_upgrade}|{last_skill}|{thinking_level}")
'

router_raw=$(python3 -c "$router_py" "${duration_ms:-0}" 2>/dev/null)
IFS='|' read -r router_active router_count chain_progress saved_match catalog_upgrade last_skill thinking_level <<< "$router_raw"

# ── Build router segment ──────────────────────────────────────────────────────
router_seg=""
if [ "${router_active:-0}" = "1" ]; then
  router_seg="${BMAGENTA}🔀${R} ${BOLD}router${R}"
elif [ "${router_count:-0}" -gt 0 ]; then
  router_seg="${DIM}🔀 R${router_count}${R}"
fi

chain_seg=""
if [ -n "$chain_progress" ]; then
  saved_marker=""
  [ -n "$saved_match" ] && saved_marker=" ${BGREEN}✦saved${R}"
  chain_seg="${BMAGENTA}▶${R} ${BOLD}${chain_progress}${R}${saved_marker}"
fi

# ── Thinking-depth segment ────────────────────────────────────────────────────
thinking_seg=""
case "$thinking_level" in
  ultra) thinking_seg="${BRED}🧠 ultra${R}" ;;
  hard)  thinking_seg="${ORANGE}🧠 hard${R}" ;;
  think) thinking_seg="${YELLOW}🧠 think${R}" ;;
esac

# ── Last-skill segment with provenance markers ────────────────────────────────
skill_seg=""
if [ -n "$last_skill" ]; then
  marker=""
  [ "${catalog_upgrade:-0}" = "1" ] && marker=" ${BGREEN}✓${R}"
  skill_seg="${BOLD}${CYAN}⚙ ${last_skill}${R}${marker}"
fi

# ── Build output ──────────────────────────────────────────────────────────────
S="${DIM} · ${R}"

out="${DIM}${mood}${R} ${BCYAN}${model_short}${R}"
[ -n "$cwd_raw" ]    && out+="${S}${BLUE}${cwd_raw}${R}"
[ -n "$git_str" ]    && out+="${S}${MAGENTA}${git_str}${R}"
[ -n "$router_seg" ]   && out+="${S}${router_seg}"
[ -n "$chain_seg" ]    && out+="${S}${chain_seg}"
[ -n "$thinking_seg" ] && out+="${S}${thinking_seg}"
[ -n "$skill_seg" ]    && out+="${S}${skill_seg}"
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
